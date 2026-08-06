import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse
from sqlalchemy.orm import Session
from app.models.models import SysLLMConfig, SysOntologyEntity, SysOntologyProperty
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def normalize_model_name(model_name: Optional[str], api_base_url: Optional[str] = None) -> str:
    """Normalize provider-specific model names for direct vendor endpoints."""
    normalized = (model_name or "").strip()
    if not normalized:
        return normalized

    host = ""
    if api_base_url:
        try:
            host = (urlparse(api_base_url).hostname or "").lower()
        except Exception:
            host = ""

    # Legacy configs may store LiteLLM-style model names for direct DeepSeek endpoints.
    if normalized.startswith("openai/") and "deepseek" in host:
        return normalized.split("/", 1)[1].strip()

    return normalized


class LLMService:
    DEFAULT_CONTEXT_WINDOW_TOKENS = 32000
    MIN_COMPLETION_TOKENS = 256
    CONTEXT_MARGIN_TOKENS = 512
    MIN_INPUT_BUDGET_TOKENS = 2048

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _build_verified_direct_relation_candidates(
        ontology_entities: List[Dict[str, Any]],
        ontology_relations: List[Dict[str, Any]],
        entity_mapping_results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Derive direct FK→PK joins before asking the LLM to design edges."""
        entities_by_id = {str(item.get("entity_id") or ""): item for item in ontology_entities}
        mappings_by_entity = {str(item.get("entity_id") or ""): item.get("mappings") or [] for item in entity_mapping_results}
        candidates: List[Dict[str, Any]] = []
        for relation in ontology_relations:
            source_id, target_id = str(relation.get("source_entity_id") or ""), str(relation.get("target_entity_id") or "")
            source_entity, target_entity = entities_by_id.get(source_id) or {}, entities_by_id.get(target_id) or {}
            source_pk = {str(prop.get("property_name") or "").upper() for prop in source_entity.get("properties") or [] if prop.get("is_primary_key")}
            target_pk = {str(prop.get("property_name") or "").upper() for prop in target_entity.get("properties") or [] if prop.get("is_primary_key")}
            source_mappings = mappings_by_entity.get(source_id, [])
            target_mappings = mappings_by_entity.get(target_id, [])
            for source_mapping in source_mappings:
                source_column = str(source_mapping.get("sourceColumn") or source_mapping.get("source_column") or "").upper()
                source_property = str(source_mapping.get("matchedPropertyName") or source_mapping.get("propertyName") or "").upper()
                if not source_column.endswith("ID"):
                    continue
                for target_mapping in target_mappings:
                    target_column = str(target_mapping.get("sourceColumn") or target_mapping.get("source_column") or "").upper()
                    target_property = str(target_mapping.get("matchedPropertyName") or target_mapping.get("propertyName") or "").upper()
                    if source_column != target_column or not target_column:
                        continue
                    if source_property not in source_pk and target_property not in target_pk:
                        continue
                    candidate = {
                        "relation_id": relation.get("relation_id"),
                        "source_table": str(source_mapping.get("sourceTable") or source_mapping.get("source_table") or "").upper(),
                        "target_table": str(target_mapping.get("sourceTable") or target_mapping.get("source_table") or "").upper(),
                        "source_column": source_column,
                        "target_column": target_column,
                        "join_condition": f"src.{source_column} = dst.{target_column}",
                        "reason": "两端已映射字段同名，且至少一端对应本体主键（FK→PK 直接关联）",
                    }
                    if candidate not in candidates:
                        candidates.append(candidate)
        return candidates

    def _get_config_by_id(self, config_id: Optional[str]) -> Optional[SysLLMConfig]:
        """按 ID 获取启用的大模型配置"""
        if not config_id:
            return None
        return self.db.query(SysLLMConfig).filter(
            SysLLMConfig.config_id == config_id,
            SysLLMConfig.is_active == "Y"
        ).first()

    def _get_default_config(self) -> Optional[SysLLMConfig]:
        """获取默认LLM配置"""
        config = self.db.query(SysLLMConfig).filter(
            SysLLMConfig.is_default == "Y",
            SysLLMConfig.is_active == "Y"
        ).first()
        if not config:
            config = self.db.query(SysLLMConfig).filter(SysLLMConfig.is_active == "Y").first()
        return config

    def get_runtime_limits(
        self,
        config: Optional[SysLLMConfig] = None,
        config_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not config:
            config = self._get_config_by_id(config_id) if config_id else self._get_default_config()

        configured_context_window = int(getattr(config, "context_window_tokens", 0) or 0) if config else 0
        context_window_tokens = configured_context_window or self.DEFAULT_CONTEXT_WINDOW_TOKENS
        context_source = "configured" if configured_context_window else "fallback"

        configured_max_output = int(getattr(config, "max_tokens", 0) or 0) if config else 0
        input_budget_tokens = max(0, context_window_tokens)

        return {
            "context_window_tokens": context_window_tokens,
            "context_window_source": context_source,
            "configured_context_window_tokens": configured_context_window or None,
            "configured_max_output_tokens": configured_max_output or None,
            "max_output_tokens": None,
            "input_budget_tokens": input_budget_tokens,
            "context_margin_tokens": self.CONTEXT_MARGIN_TOKENS,
        }

    def estimate_text_tokens(self, value: str) -> int:
        text = value or ""
        if not text:
            return 0
        cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        other_chars = max(0, len(text) - cjk_chars)
        token_count = cjk_chars + (other_chars + 3) // 4
        return max(1, token_count)

    def estimate_structured_payload_tokens(self, payload: Any) -> int:
        try:
            text = json.dumps(self._make_json_safe(payload), ensure_ascii=False, separators=(",", ":"))
        except Exception:
            text = str(payload)
        return self.estimate_text_tokens(text)

    def estimate_prompt_tokens(self, system_prompt: str, user_prompt: str) -> int:
        return self.estimate_text_tokens(system_prompt) + self.estimate_text_tokens(user_prompt) + 32

    def _is_context_limit_error(self, exc: Exception) -> bool:
        message = str(exc or "").upper()
        tokens = [
            "CONTEXT LENGTH",
            "MAXIMUM CONTEXT LENGTH",
            "PROMPT IS TOO LONG",
            "TOO MANY TOKENS",
            "TOKEN LIMIT",
            "MAX_TOKENS",
            "INPUT TOO LONG",
            "REQUEST TOO LARGE",
        ]
        return any(token in message for token in tokens)

    def _build_context_limit_error_message(
        self,
        model_name: str,
        estimated_input_tokens: int,
        runtime_limits: Dict[str, Any],
    ) -> str:
        return (
            f"当前模型 {model_name} 的可用输入预算约为 {runtime_limits['input_budget_tokens']} tokens，"
            f"本次请求预计输入约 {estimated_input_tokens} tokens，存在上下文超限风险。"
            f"请缩短业务文档、减少单次表数量，或在系统管理中调大该模型的最大Token。"
        )

    def _build_openai_client(self, config: SysLLMConfig):
        import httpx
        import openai

        http_client = httpx.Client(follow_redirects=True)
        client = openai.OpenAI(
            api_key=config.api_key_enc,
            base_url=config.api_base_url,
            http_client=http_client,
        )
        return client, http_client

    async def call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        config: Optional[SysLLMConfig] = None,
        timeout_override: Optional[int] = None
    ) -> str:
        """调用LLM"""
        if not config:
            config = self._get_default_config()

        if not config:
            logger.warning("No active LLM config found, fallback mock response is used")
            # Use mock response if no LLM config
            return self._mock_llm_response(system_prompt, user_prompt)

        try:
            resolved_model = normalize_model_name(config.model_name, config.api_base_url)
            runtime_limits = self.get_runtime_limits(config=config)
            estimated_input_tokens = self.estimate_prompt_tokens(system_prompt, user_prompt)
            logger.info(
                "Calling LLM: config=%s model=%s timeout=%s system_prompt_len=%s user_prompt_len=%s estimated_input_tokens=%s input_budget_tokens=%s context_window_tokens=%s max_output_tokens=%s",
                config.config_name,
                resolved_model,
                timeout_override or config.timeout,
                len(system_prompt or ""),
                len(user_prompt or ""),
                estimated_input_tokens,
                runtime_limits["input_budget_tokens"],
                runtime_limits["context_window_tokens"],
                "provider-default",
            )
            logger.debug("LLM system prompt preview: %s", (system_prompt or "")[:500])
            logger.debug("LLM user prompt preview: %s", (user_prompt or "")[:1000])
            if estimated_input_tokens > runtime_limits["input_budget_tokens"]:
                raise ValueError(
                    self._build_context_limit_error_message(
                        model_name=resolved_model,
                        estimated_input_tokens=estimated_input_tokens,
                        runtime_limits=runtime_limits,
                    )
                )
            client = None
            http_client = None
            try:
                client, http_client = self._build_openai_client(config)
                response = client.chat.completions.create(
                    model=resolved_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=config.temperature,
                    timeout=timeout_override or config.timeout
                )
            finally:
                if client:
                    try:
                        client.close()
                    except Exception:
                        pass
                elif http_client:
                    try:
                        http_client.close()
                    except Exception:
                        pass
            logger.info(
                "LLM call succeeded: config=%s model=%s response_len=%s",
                config.config_name,
                resolved_model,
                len(response.choices[0].message.content or ""),
            )
            logger.debug("LLM response preview: %s", (response.choices[0].message.content or "")[:1000])
            return response.choices[0].message.content
        except ValueError:
            raise
        except Exception as e:
            if self._is_context_limit_error(e):
                raise ValueError(
                    self._build_context_limit_error_message(
                        model_name=normalize_model_name(config.model_name, config.api_base_url),
                        estimated_input_tokens=self.estimate_prompt_tokens(system_prompt, user_prompt),
                        runtime_limits=self.get_runtime_limits(config=config),
                    )
                ) from e
            logger.exception("LLM call failed: %s", str(e))
            # Fallback to mock
            return self._mock_llm_response(system_prompt, user_prompt, str(e))

    async def auto_mapping(
        self,
        entity: SysOntologyEntity,
        properties: List[SysOntologyProperty],
        source_tables: List[Dict],
        domain_context: Optional[Dict[str, Any]] = None,
        source_context: Optional[Dict[str, Any]] = None,
        blueprint_context: Optional[Dict[str, Any]] = None,
        mapping_instruction: Optional[str] = None,
        config_id: Optional[str] = None,
    ) -> Dict:
        """LLM辅助自动映射"""
        logger.info(
            "Start auto mapping: entity=%s(%s) properties=%s candidate_tables=%s config_id=%s",
            entity.entity_display_name or entity.entity_name,
            entity.entity_id,
            len(properties),
            len(source_tables),
            config_id,
        )
        logger.info(
            "Auto mapping candidate table names: %s",
            [item.get("table_name") for item in source_tables[:12]],
        )
        system_prompt = self._build_auto_mapping_system_prompt(mapping_instruction)

        entity_info = {
            "entity_name": entity.entity_name,
            "entity_display_name": entity.entity_display_name,
            "entity_desc": entity.entity_desc,
            "properties": [
                {
                    "property_id": p.property_id,
                    "name": p.property_name,
                    "display_name": p.property_display_name,
                    "data_type": p.data_type,
                    "desc": p.property_desc,
                    "is_primary_key": p.is_primary_key
                }
                for p in properties
            ]
        }

        # Build compact source table metadata for the LLM prompt. The complete
        # metadata remains available in the source service and response payload.
        tables_info = [
            self._compact_auto_mapping_prompt_table(table)
            for table in source_tables[:12]
        ]

        prompt_payload = {
            "domain_context": domain_context or {},
            "entity": entity_info,
            "source_context": source_context or {},
            "blueprint_context": blueprint_context or {},
            "candidate_tables": tables_info,
        }
        sanitized_prompt_payload = self._make_json_safe(prompt_payload)

        extra_instruction = (mapping_instruction or "").strip()
        user_prompt = f"""请根据下面的信息，为业务实体生成“本体属性 + 来源字段”映射建议。

输入信息：
{json.dumps(sanitized_prompt_payload, ensure_ascii=False, indent=2)}

要求：
- 优先输出与当前实体直接相关的字段，不要把明显无关的列也塞进来。
- 如果已有属性已能承接该字段，返回 matchedPropertyId / matchedPropertyName。
- 如果是新增属性候选，matchedPropertyId / matchedPropertyName 返回空字符串。
- 如果 blueprint_context 中给出了 preferredTables / preferredRoles / recommendedBuildMode，应优先遵循，除非这些推荐与当前候选数据明显冲突。
- 所有属性映射都必须能够支撑后续生成来源于源数据表的 Oracle Graph 实体 DDL；不要输出无法落成 SQL 的概念性映射。
- mappingType 使用 DIRECT / COMPUTED / CONSTANT / LLM_DERIVED 之一。
- 如果属性值需要从源字段做截取、正则提取、前后缀拆分、标准化清洗、类型转换或 CASE 判断，必须使用 COMPUTED。
- 使用 COMPUTED 时，sourceTable / sourceColumn 必须仍然填写原始来源表和原始来源字段。
- 使用 COMPUTED 时，formula 必须填写 Oracle SQL 表达式，且不要包含 `AS 属性名`。
- 对于站点编码、产线编码、工序编码、设备编码、工单号、批次号等从复合字段中抽取标准编码的场景，优先输出 COMPUTED。
- 如果无法直接一一映射，但能明确给出抽取规则，宁可输出 COMPUTED，也不要错误地输出 DIRECT。
- 典型计算映射表达式示例：
  - `REGEXP_SUBSTR(PROCESS_NAME, '^ST[0-9]+')`
  - `SUBSTR(PROCESS_CODE, 1, 7)`
  - `REGEXP_SUBSTR(ROUTE_DESC, '[^_-]+', 1, 1)`
  - `TRIM(UPPER(LINE_CODE_RAW))`
  - `CASE WHEN RESULT = 'OK' THEN 'PASS' ELSE 'FAIL' END`
- 若当前实体最终应由多个源表联合生成，建议通过多个可落地属性映射体现来源；如果无法仅靠属性级映射表达，则应在后续 `entity_mapping.view_sql` 中补全实体级 SQL。
- confidence 使用 HIGH / MEDIUM / LOW。
- 返回严格 JSON。
{f"- 额外业务映射指令：{extra_instruction}" if extra_instruction else ""}"""

        result_text = await self.call_llm(system_prompt, user_prompt, self._get_config_by_id(config_id))
        fallback_result = self._generate_mock_mappings(entity, properties, source_tables)

        # Parse result
        try:
            parsed = self._extract_json_object(result_text)
            result = self._normalize_auto_mapping_result(parsed, entity, properties)
            if not result:
                result = fallback_result
        except json.JSONDecodeError:
            result = fallback_result

        return {
            **result,
            "domain_context": domain_context or {},
            "source_context": source_context or {},
            "blueprint_context": blueprint_context or {},
            "mapping_instruction": extra_instruction,
            "candidate_tables": [
                {
                    "table_name": table.get("table_name"),
                    "comments": table.get("comments") or table.get("table_comment"),
                    "column_count": len(table.get("columns", []) or []),
                    "sample_row_count": len(table.get("sample_rows", []) or []),
                    "columns": [
                        {
                            "column_name": column.get("column_name"),
                            "data_type": column.get("data_type"),
                            "comments": column.get("comments"),
                        }
                        for column in (table.get("columns", []) or [])[:12]
                    ],
                }
                for table in source_tables[:12]
            ],
            "llm_raw_output": result_text,
            "mapping_count": len(result.get("mappings", []) if isinstance(result, dict) else []),
        }

    async def design_ontology_property_graph_mapping(
        self,
        *,
        domain_context: Dict[str, Any],
        ontology_entities: List[Dict[str, Any]],
        ontology_relations: List[Dict[str, Any]],
        source_tables: List[Dict[str, Any]],
        entity_mapping_results: List[Dict[str, Any]],
        blueprint_context: Optional[Dict[str, Any]] = None,
        mapping_instruction: Optional[str] = None,
        config_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按完整本体一次性设计节点构建 SQL 与关系边 SQL。"""
        config = self._get_config_by_id(config_id) if config_id else self._get_default_config()
        if config_id and not config:
            raise ValueError("所选大模型配置不存在或未启用")

        compact_source_tables = [
            self._compact_auto_mapping_prompt_table(table)
            for table in source_tables[:24]
        ]
        compact_entity_results = [
            {
                "entity_id": item.get("entity_id"),
                "entity_name": item.get("entity_name"),
                "properties": [
                    {
                        "property_name": mapping.get("propertyName"),
                        "matched_property_id": mapping.get("matchedPropertyId"),
                        "source_table": mapping.get("sourceTable"),
                        "source_column": mapping.get("sourceColumn"),
                        "source_data_type": mapping.get("sourceDataType"),
                        "mapping_type": mapping.get("mappingType"),
                        "formula": mapping.get("formula"),
                        "is_vertex_key": mapping.get("is_vertex_key"),
                    }
                    for mapping in (item.get("mappings") or [])
                ],
            }
            for item in entity_mapping_results
        ]
        direct_relation_candidates = self._build_verified_direct_relation_candidates(
            ontology_entities,
            ontology_relations,
            entity_mapping_results,
        )
        blueprint = blueprint_context or {}
        prompt_payload = {
            "domain": domain_context,
            "ontology_entities": ontology_entities,
            "ontology_relations": ontology_relations,
            "source_tables": compact_source_tables,
            "property_mapping_candidates": compact_entity_results,
            "verified_direct_relation_candidates": direct_relation_candidates,
            "blueprint_context": {
                "rule_summary": blueprint.get("rule_summary") or {},
                "table_roles": blueprint.get("table_roles") or blueprint.get("source_role_bindings") or [],
                "mapping_design": blueprint.get("mapping_design") or {},
                "deployment_design": blueprint.get("deployment_design") or {},
            },
        }
        system_prompt = """你是 Oracle 26ai SQL Property Graph 与制造业本体落地专家。你必须从“完整本体”出发设计关系型承载层，而不是逐对象孤立匹配字段。

参考成熟的 Oracle 属性图构建脚本模式：
1. 每个本体对象对应一个稳定命名的节点表；节点表由源表通过 SELECT、JOIN、UNION ALL、DISTINCT 或 GROUP BY 构建。
2. 节点 SELECT 必须输出稳定且唯一的本体 KEY，并输出该本体对象的全部属性列；派生对象允许用拼接键、聚合或事件展开构建。
3. 每个本体关系对应一张边表；边 SELECT 必须输出稳定唯一的 EDGE_ID、SOURCE_ID、TARGET_ID，且两端值必须分别等于源节点和目标节点的 KEY。
4. 边必须根据本体对象之间的关系语义设计。JOIN 条件必须使用源、目标表中实际存在且能够匹配数据的业务关联列；严禁把不同含义的两端主键直接写成 JOIN。
5. 对于直接等值关联，优先选择两端同名的 `*_ID` 字段，并且该字段在源或目标本体对象中至少一端是主键（典型模式：子表外键 PRODUCT_ID = 产品表主键 PRODUCT_ID）。
6. `SOURCE_ID`、`TARGET_ID` 是图边端点，分别输出源、目标节点的主键；它们不是 JOIN 推断依据。必须先用业务关联列完成 JOIN，再投影两端节点主键。
7. 若不存在可证明的关联列，不要虚构边 SQL；返回空 joinCondition/edgeSql，并在 designReason 中说明“待人工确认”。
8. 节点 SQL 和边 SQL 都只返回 SELECT / WITH 查询体，不要包含 CREATE、ALTER、DROP、COMMENT 或结尾分号。
9. 默认使用 TABLE 构建方式，以便下一步 CTAS 后增加 PRIMARY KEY；仅在明确需要实时视图时使用 VIEW。
10. Oracle 对象名和输出列别名使用大写英文下划线；SQL 必须可执行。
11. 不得虚构输入中不存在的源表或源字段。
12. 返回严格 JSON，不要输出 Markdown。"""
        extra_instruction = (mapping_instruction or "").strip()
        user_prompt = f"""请根据完整本体、对象关系、源表结构和初步属性候选，一次性设计节点表与边表的源数据映射。

输入信息：
{json.dumps(self._make_json_safe(prompt_payload), ensure_ascii=False, indent=2)}

输出格式：
{{
  "entityMappings": [
    {{
      "entityId": "本体对象ID",
      "entityName": "本体对象名称",
      "nodeTableName": "ONTO_对象名",
      "buildType": "TABLE",
      "sourceTables": ["实际源表"],
      "keyPropertyName": "本体主键属性名",
      "keyOutputColumn": "节点SQL输出的主键列名",
      "nodeSql": "SELECT ...",
      "designReason": "节点构建说明"
    }}
  ],
  "relationMappings": [
    {{
      "relationId": "本体关系ID",
      "relationName": "本体关系名称",
      "sourceEntityName": "源本体对象名称",
      "targetEntityName": "目标本体对象名称",
      "edgeTableName": "ONTO_EDGE_关系名",
      "sourceTables": ["关系证据源表"],
      "joinCondition": "关系实现条件",
      "edgeSql": "SELECT ... AS EDGE_ID, ... AS SOURCE_ID, ... AS TARGET_ID ...",
      "designReason": "关系构建说明"
    }}
  ]
}}

必须覆盖输入中的每一个本体对象和每一条本体关系。节点表名、边表名、节点 KEY 和关系方向必须前后一致；keyOutputColumn 必须就是 nodeSql 中 keyPropertyName 对应的输出列别名。
关系输出必须在 designReason 中明确写出“关联列”和“两端节点主键投影”。不得使用 `src.<源PK> = dst.<目标PK>` 作为不同实体之间的 Join。
当输入 `verified_direct_relation_candidates` 包含当前 relationId 的候选时，必须优先且原样使用该候选的 joinCondition；这是已由系统根据属性映射验证的 FK→PK 关系，不得遗漏或替换。
{f"额外业务映射指令：{extra_instruction}" if extra_instruction else ""}"""

        raw_output = await self.call_llm(system_prompt, user_prompt, config)
        parsed = self._extract_json_object(raw_output)
        normalized = self._normalize_ontology_property_graph_mapping(
            parsed,
            ontology_entities=ontology_entities,
            ontology_relations=ontology_relations,
            entity_mapping_results=entity_mapping_results,
            verified_direct_relation_candidates=direct_relation_candidates,
        )
        return {
            **normalized,
            "llm_raw_output": raw_output,
            "source_table_count": len(compact_source_tables),
        }

    def _normalize_ontology_property_graph_mapping(
        self,
        payload: Optional[Dict[str, Any]],
        *,
        ontology_entities: List[Dict[str, Any]],
        ontology_relations: List[Dict[str, Any]],
        entity_mapping_results: Optional[List[Dict[str, Any]]] = None,
        verified_direct_relation_candidates: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {"entity_mappings": [], "relation_mappings": []}

        entity_by_id = {
            str(item.get("entity_id") or "").strip(): item
            for item in ontology_entities
            if item.get("entity_id")
        }
        entity_by_name = {
            str(item.get("entity_name") or "").strip().lower(): item
            for item in ontology_entities
            if item.get("entity_name")
        }
        # The relationship validator needs the actual source columns selected
        # during property mapping, not just conceptual property names.
        mapped_columns_by_entity: Dict[str, Dict[str, str]] = {}
        for mapping_result in entity_mapping_results or []:
            entity_id = str(mapping_result.get("entity_id") or "").strip()
            if not entity_id:
                continue
            column_map: Dict[str, str] = {}
            for mapping in mapping_result.get("mappings") or []:
                property_name = str(mapping.get("matchedPropertyName") or mapping.get("propertyName") or "").strip().upper()
                source_column = str(mapping.get("sourceColumn") or mapping.get("source_column") or "").strip().upper()
                if property_name and source_column:
                    column_map[source_column] = property_name
            mapped_columns_by_entity[entity_id] = column_map
        normalized_entities: List[Dict[str, Any]] = []
        seen_entity_ids = set()
        for item in payload.get("entityMappings") or payload.get("entity_mappings") or []:
            if not isinstance(item, dict):
                continue
            entity = entity_by_id.get(str(item.get("entityId") or item.get("entity_id") or "").strip())
            if not entity:
                entity = entity_by_name.get(str(item.get("entityName") or item.get("entity_name") or "").strip().lower())
            if not entity:
                continue
            entity_id = str(entity.get("entity_id") or "")
            if entity_id in seen_entity_ids:
                continue
            node_sql = self._normalize_readonly_mapping_sql(item.get("nodeSql") or item.get("node_sql"))
            if not node_sql:
                continue
            seen_entity_ids.add(entity_id)
            key_property_name = str(item.get("keyPropertyName") or item.get("key_property_name") or "").strip()
            if not key_property_name:
                continue
            normalized_entities.append({
                "entity_id": entity_id,
                "entity_name": entity.get("entity_name") or "",
                "node_table_name": self._normalize_oracle_object_name(
                    item.get("nodeTableName") or item.get("node_table_name"),
                    fallback=f"ONTO_NODE_{entity.get('entity_name') or entity_id}",
                ),
                "build_type": "VIEW" if str(item.get("buildType") or item.get("build_type") or "").upper() == "VIEW" else "TABLE",
                "source_tables": self._normalize_source_table_names(item.get("sourceTables") or item.get("source_tables") or []),
                "key_property_name": key_property_name,
                "key_output_column": self._normalize_oracle_object_name(
                    item.get("keyOutputColumn") or item.get("key_output_column"),
                    fallback=str(item.get("keyPropertyName") or item.get("key_property_name") or "ID"),
                ),
                "node_sql": node_sql,
                "design_reason": str(item.get("designReason") or item.get("design_reason") or "").strip(),
            })

        relation_by_id = {
            str(item.get("relation_id") or "").strip(): item
            for item in ontology_relations
            if item.get("relation_id")
        }
        relation_by_signature = {
            (
                str(item.get("relation_name") or "").strip().lower(),
                str(item.get("source_entity_name") or "").strip().lower(),
                str(item.get("target_entity_name") or "").strip().lower(),
            ): item
            for item in ontology_relations
        }
        verified_candidates_by_relation: Dict[str, List[Dict[str, Any]]] = {}
        for candidate in verified_direct_relation_candidates or []:
            relation_id = str(candidate.get("relation_id") or "").strip()
            join_condition = str(candidate.get("join_condition") or "").strip()
            if relation_id and join_condition:
                verified_candidates_by_relation.setdefault(relation_id, []).append(candidate)
        normalized_relations: List[Dict[str, Any]] = []
        seen_relation_ids = set()
        for item in payload.get("relationMappings") or payload.get("relation_mappings") or []:
            if not isinstance(item, dict):
                continue
            relation = relation_by_id.get(str(item.get("relationId") or item.get("relation_id") or "").strip())
            if not relation:
                signature = (
                    str(item.get("relationName") or item.get("relation_name") or "").strip().lower(),
                    str(item.get("sourceEntityName") or item.get("source_entity_name") or "").strip().lower(),
                    str(item.get("targetEntityName") or item.get("target_entity_name") or "").strip().lower(),
                )
                relation = relation_by_signature.get(signature)
            if not relation:
                continue
            relation_id = str(relation.get("relation_id") or "")
            if relation_id in seen_relation_ids:
                continue
            edge_sql = self._normalize_readonly_mapping_sql(item.get("edgeSql") or item.get("edge_sql"))
            upper_edge_sql = edge_sql.upper()
            if edge_sql and not all(re.search(rf"\bAS\s+{column}\b", upper_edge_sql) for column in ["EDGE_ID", "SOURCE_ID", "TARGET_ID"]):
                edge_sql = ""
            join_condition = self._normalize_relation_join_condition(
                item.get("joinCondition") or item.get("join_condition"),
                source_entity=entity_by_id.get(str(relation.get("source_entity_id") or "")),
                target_entity=entity_by_id.get(str(relation.get("target_entity_id") or "")),
                source_mapped_columns=mapped_columns_by_entity.get(str(relation.get("source_entity_id") or ""), {}),
                target_mapped_columns=mapped_columns_by_entity.get(str(relation.get("target_entity_id") or ""), {}),
            )
            verified_candidates = verified_candidates_by_relation.get(relation_id) or []
            if verified_candidates:
                # Deterministic metadata evidence outranks an omitted or
                # hallucinated LLM Join.  This prevents a clear
                # ProductionBatch.PRODUCT_ID -> Product.PRODUCT_ID relation
                # from disappearing from the global mapping recommendation.
                join_condition = str(verified_candidates[0]["join_condition"])
            # If enough entity metadata is present, reject an LLM relation
            # that did not prove the actual join pair.  Legacy callers without
            # property context retain their existing compatibility behavior.
            source_entity = entity_by_id.get(str(relation.get("source_entity_id") or "")) or {}
            target_entity = entity_by_id.get(str(relation.get("target_entity_id") or "")) or {}
            has_property_context = bool(source_entity.get("properties") and target_entity.get("properties"))
            if has_property_context and not join_condition:
                continue
            if not edge_sql and not join_condition:
                continue
            seen_relation_ids.add(relation_id)
            normalized_relations.append({
                "relation_id": relation_id,
                "relation_name": relation.get("relation_name") or "",
                "source_entity_id": relation.get("source_entity_id") or "",
                "target_entity_id": relation.get("target_entity_id") or "",
                "edge_table_name": self._normalize_oracle_object_name(
                    item.get("edgeTableName") or item.get("edge_table_name"),
                    fallback=f"ONTO_EDGE_{relation.get('relation_name') or relation_id}",
                ),
                "source_tables": self._normalize_source_table_names(
                    item.get("sourceTables") or item.get("source_tables") or
                    [item.get("source_table") for item in verified_candidates if item.get("source_table")]
                ),
                "join_condition": join_condition,
                "edge_sql": edge_sql,
                "design_reason": str(item.get("designReason") or item.get("design_reason") or "").strip() or (
                    str(verified_candidates[0].get("reason") or "") if verified_candidates else ""
                ),
            })

        # The model may omit a relationship despite having a deterministic
        # FK→PK candidate.  Surface that candidate anyway so the global
        # mapping result remains complete and reviewable.
        for relation_id, candidates in verified_candidates_by_relation.items():
            if relation_id in seen_relation_ids or not candidates:
                continue
            relation = relation_by_id.get(relation_id)
            if not relation:
                continue
            candidate = candidates[0]
            seen_relation_ids.add(relation_id)
            normalized_relations.append({
                "relation_id": relation_id,
                "relation_name": relation.get("relation_name") or "",
                "source_entity_id": relation.get("source_entity_id") or "",
                "target_entity_id": relation.get("target_entity_id") or "",
                "edge_table_name": self._normalize_oracle_object_name(
                    fallback=f"ONTO_EDGE_{relation.get('relation_name') or relation_id}",
                    value=None,
                ),
                "source_tables": self._normalize_source_table_names([candidate.get("source_table")]),
                "join_condition": candidate["join_condition"],
                "edge_sql": "",
                "design_reason": candidate.get("reason") or "",
            })

        return {
            "entity_mappings": normalized_entities,
            "relation_mappings": normalized_relations,
        }

    def _normalize_relation_join_condition(
        self,
        value: Any,
        *,
        source_entity: Optional[Dict[str, Any]],
        target_entity: Optional[Dict[str, Any]],
        source_mapped_columns: Dict[str, str],
        target_mapped_columns: Dict[str, str],
    ) -> str:
        """Accept only a proven direct foreign-key/primary-key equality.

        The relationship recommendation is intentionally narrower than free-form
        SQL: a shared ID name must be present on both mapped nodes and must be a
        PK for at least one endpoint.  Complex relations stay pending for the
        later relation-table workflow instead of being fabricated by the LLM.
        """
        condition = str(value or "").strip().rstrip(";")
        match = re.fullmatch(
            r"(?i)\s*(?:src\.)?([A-Za-z][A-Za-z0-9_$#]*)\s*=\s*(?:dst\.)?([A-Za-z][A-Za-z0-9_$#]*)\s*",
            condition,
        )
        if not match:
            return ""
        source_column, target_column = match.group(1).upper(), match.group(2).upper()
        if source_column != target_column or not source_column.endswith("ID"):
            return ""
        source_property = source_mapped_columns.get(source_column, source_column)
        target_property = target_mapped_columns.get(target_column, target_column)
        source_pk = {
            str(item.get("property_name") or "").upper()
            for item in (source_entity or {}).get("properties") or []
            if item.get("is_primary_key")
        }
        target_pk = {
            str(item.get("property_name") or "").upper()
            for item in (target_entity or {}).get("properties") or []
            if item.get("is_primary_key")
        }
        if source_pk or target_pk:
            if source_property not in source_pk and target_property not in target_pk:
                return ""
        return f"src.{source_column} = dst.{target_column}"

    def _normalize_readonly_mapping_sql(self, value: Any) -> str:
        sql = str(value or "").strip().rstrip(";").strip()
        if not sql or not re.match(r"^(SELECT|WITH)\b", sql, re.IGNORECASE):
            return ""
        padded = f" {sql.upper()} "
        blocked_tokens = [
            " INSERT ", " UPDATE ", " DELETE ", " MERGE ", " DROP ", " ALTER ",
            " TRUNCATE ", " GRANT ", " REVOKE ", " COMMENT ", " EXECUTE ",
            " BEGIN ", " DECLARE ", " CREATE ",
        ]
        if any(token in padded for token in blocked_tokens):
            return ""
        return sql

    def _normalize_oracle_object_name(self, value: Any, *, fallback: str) -> str:
        token = re.sub(r"[^A-Za-z0-9_$#]+", "_", str(value or fallback).upper()).strip("_")
        if not token:
            token = re.sub(r"[^A-Za-z0-9_$#]+", "_", fallback.upper()).strip("_")
        if token and token[0].isdigit():
            token = f"OBJ_{token}"
        return token[:100]

    def _normalize_source_table_names(self, values: Any) -> List[str]:
        if not isinstance(values, list):
            return []
        result = []
        for value in values:
            token = ".".join(
                self._normalize_oracle_object_name(part, fallback="")
                for part in str(value or "").split(".")
                if self._normalize_oracle_object_name(part, fallback="")
            )
            if token and token not in result:
                result.append(token)
        return result

    async def select_relevant_tables_for_mapping(
        self,
        entity: SysOntologyEntity,
        properties: List[SysOntologyProperty],
        table_catalog: List[Dict[str, Any]],
        domain_context: Optional[Dict[str, Any]] = None,
        blueprint_context: Optional[Dict[str, Any]] = None,
        mapping_instruction: Optional[str] = None,
        config_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        config = self._get_config_by_id(config_id) if config_id else self._get_default_config()
        if config_id and not config:
            raise ValueError("所选大模型配置不存在或未启用")
        runtime_limits = self.get_runtime_limits(config=config)
        compact_catalog = self._compact_mapping_table_catalog_for_prompt(table_catalog, runtime_limits)
        logger.info(
            "Start table selection for mapping: entity=%s(%s) properties=%s catalog_tables=%s config_id=%s",
            entity.entity_display_name or entity.entity_name,
            entity.entity_id,
            len(properties),
            len(compact_catalog),
            config_id,
        )
        system_prompt = """你是一个面向 Oracle Property Graph 落地的数据建模专家。你的任务不是直接做属性映射，而是先根据业务分析域、本体对象及其属性，在候选源数据表中挑出最可能承载该对象数据的对象表。

要求：
1. 只依据业务分析域描述、本体对象描述、属性列表、候选表的表名和表注释进行判断。
2. 不要根据字段明细做映射，本阶段只负责“锁表”。
3. 优先选择真正承载业务对象主体数据的源数据表，避免日志表、结果表、过程表干扰，除非这些表本身就是最终本体属性来源。
4. 如果输入中的 blueprintContext 给出了 preferredTables / preferredRoles，应优先从这些推荐表中锁表，除非它们明显与当前实体语义冲突。
5. 最终目标是为后续 Oracle Graph DDL 生成提供可落地的源表设计，因此锁表结果必须有利于后续生成来源于源数据表的实体表/视图，而不是停留在概念层。
6. 返回严格 JSON。

输出格式：
{
  "selectedTables": [
    {
      "tableName": "表名",
      "reason": "选择理由"
    }
  ]
}"""
        prompt_payload = {
            "domain_context": domain_context or {},
            "entity": {
                "entity_name": entity.entity_name,
                "entity_display_name": entity.entity_display_name,
                "entity_desc": entity.entity_desc,
                "properties": [
                    {
                        "property_name": item.property_name,
                        "property_display_name": item.property_display_name,
                        "property_desc": item.property_desc,
                    }
                    for item in properties
                ],
            },
            "candidate_tables": [
                {
                    "table_name": item.get("table_name"),
                    "comments": item.get("comments"),
                    "source_role": item.get("source_role"),
                    "blueprint_preferred": item.get("blueprint_preferred"),
                }
                for item in compact_catalog
            ],
            "blueprint_context": blueprint_context or {},
            "mapping_instruction": (mapping_instruction or "").strip(),
        }
        user_prompt = f"""请从候选表中先挑选最可能作为该本体对象来源对象表的数据表。

输入信息：
{json.dumps(self._make_json_safe(prompt_payload), ensure_ascii=False, indent=2)}

要求：
- 优先挑选能够直接承载本体属性来源的对象主体表，不要优先返回与最终属性图实体无关的弱相关表。
- 如果 blueprintContext 给出了 preferredTables，请优先从这些表中选择，除非业务含义明显不匹配。
- 返回一组真正相关的源数据表，数量由业务需要决定；不要因为追求覆盖率而返回过多弱相关表。
- 这些表必须能够支撑后续生成来源于源数据表的 Oracle Graph 实体对象 DDL。
- 返回严格 JSON。"""
        result_text = await self.call_llm(system_prompt, user_prompt, config)
        parsed = self._extract_json_object(result_text)
        selected = []
        catalog_name_set = {
            (item.get("table_name") or "").strip().upper()
            for item in compact_catalog
            if item.get("table_name")
        }
        if isinstance(parsed, dict) and isinstance(parsed.get("selectedTables"), list):
            seen_selected = set()
            for item in parsed.get("selectedTables", []):
                if not isinstance(item, dict):
                    continue
                table_name = (item.get("tableName") or "").strip().upper()
                if not table_name or table_name not in catalog_name_set or table_name in seen_selected:
                    continue
                seen_selected.add(table_name)
                selected.append({
                    "table_name": table_name,
                    "reason": (item.get("reason") or "").strip(),
                })

        if not selected:
            selected = [
                {
                    "table_name": item.get("table_name"),
                    "reason": "基于候选排序的回退选表结果",
                }
                for item in compact_catalog
                if item.get("table_name")
            ]

        logger.info("Table selection result: entity=%s selected_tables=%s", entity.entity_id, [item["table_name"] for item in selected])
        logger.debug("Table selection raw output: %s", result_text[:1000])
        return {
            "catalog_tables": [
                {
                    "table_name": item.get("table_name"),
                    "comments": item.get("comments"),
                    "source_role": item.get("source_role"),
                    "blueprint_preferred": item.get("blueprint_preferred"),
                }
                for item in compact_catalog
            ],
            "selected_tables": selected,
            "llm_raw_output": result_text,
            "blueprint_context": blueprint_context or {},
        }

    def _compact_mapping_table_catalog_for_prompt(
        self,
        table_catalog: List[Dict[str, Any]],
        runtime_limits: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        candidate_budget = max(
            1500,
            min(
                24000,
                int((runtime_limits.get("input_budget_tokens") or self.DEFAULT_CONTEXT_WINDOW_TOKENS) * 0.12),
            ),
        )
        compact_catalog: List[Dict[str, Any]] = []
        for item in table_catalog:
            compact_item = {
                "table_name": item.get("table_name"),
                "comments": self._truncate_text(item.get("comments") or item.get("table_comment") or "", 180),
                "source_role": item.get("source_role"),
                "blueprint_preferred": item.get("blueprint_preferred"),
            }
            estimated_tokens = self.estimate_structured_payload_tokens({
                "candidate_tables": compact_catalog + [compact_item]
            })
            if compact_catalog and estimated_tokens > candidate_budget:
                break
            compact_catalog.append(compact_item)

        if not compact_catalog:
            compact_catalog = [
                {
                    "table_name": item.get("table_name"),
                    "comments": self._truncate_text(item.get("comments") or item.get("table_comment") or "", 180),
                    "source_role": item.get("source_role"),
                    "blueprint_preferred": item.get("blueprint_preferred"),
                }
                for item in table_catalog[:1]
            ]

        logger.info(
            "Mapping table catalog compacted for prompt: raw_count=%s compacted_count=%s candidate_budget=%s",
            len(table_catalog),
            len(compact_catalog),
            candidate_budget,
        )
        return compact_catalog

    def _compact_auto_mapping_prompt_column(self, column: Dict[str, Any]) -> Dict[str, Any]:
        compact_column: Dict[str, Any] = {}
        column_name = column.get("column_name")
        data_type = column.get("data_type")
        default_value = column.get("default_value")
        comments = self._truncate_text(column.get("comments") or "", 120)

        if isinstance(column_name, str):
            column_name = column_name.strip()
        if column_name:
            compact_column["column_name"] = column_name

        if isinstance(data_type, str):
            data_type = data_type.strip()
        if data_type:
            compact_column["data_type"] = data_type

        if isinstance(default_value, str):
            default_value = default_value.strip()
        if default_value is not None and default_value != "":
            compact_column["default_value"] = default_value

        if comments:
            compact_column["comments"] = comments
        return compact_column

    def _compact_auto_mapping_prompt_table(self, table: Dict[str, Any]) -> Dict[str, Any]:
        compact_table: Dict[str, Any] = {
            "table_name": table.get("table_name"),
            "columns": [
                self._compact_auto_mapping_prompt_column(column)
                for column in (table.get("columns") or [])[:40]
            ],
        }
        owner = table.get("owner")
        table_comments = self._truncate_text(
            table.get("comments") or table.get("table_comments") or "",
            300,
        )
        sample_rows = (table.get("sample_rows") or [])[:3]

        if isinstance(owner, str):
            owner = owner.strip()
        if owner:
            compact_table["owner"] = owner
        if table_comments:
            compact_table["table_comments"] = table_comments
        if sample_rows:
            compact_table["sample_rows"] = sample_rows
        return compact_table

    def _build_auto_mapping_system_prompt(self, mapping_instruction: Optional[str] = None) -> str:
        extra_instruction = (mapping_instruction or "").strip()
        return f"""你是一个制造业数据架构专家，擅长根据业务实体定义，从源数据表结构中识别最相关的字段，并形成本体属性映射建议，最终服务于 Oracle Property Graph DDL 落地。

你的任务：
1. 阅读业务实体定义、已有本体属性、数据库表描述、字段描述和样例数据。
2. 必须先根据业务分析域名称和分析域描述，判断当前映射的业务范围、业务对象边界和应关注的业务概念。
3. 只有与当前业务分析域范围一致的数据表和字段，才可以参与映射推荐。
4. 找出最相关的源表字段，作为该业务实体的属性候选；所有本体属性最终都必须可回溯到源数据表字段或基于源字段的 Oracle SQL 计算表达式。
5. 如果已有本体属性能够匹配，则优先复用已有属性。
6. 如果源字段很相关但当前本体里还没有对应属性，可以建议新增属性。
7. 属性名称使用英文/下划线风格，显示名称和描述使用中文，保持简洁。
8. 每条建议都必须带上来源表、来源字段、数据类型、置信度和理由。
9. 优先选择业务含义清晰、注释充分、与实体语义最接近的表和字段。
10. 最终这些映射要生成来源于源数据表的实体表/视图 DDL，因此不要给出无法落地到 SQL 的概念性属性。
11. 输出必须是 JSON，不要输出 Markdown。
12. 只要属性值不是源字段原值直接映射，而是需要截取、正则提取、编码拆分、标准化清洗、类型转换或 CASE 判断，就必须输出 mappingType = COMPUTED。
13. 当 mappingType = COMPUTED 时，sourceTable / sourceColumn 仍然必须填写原始来源；formula 必须是 Oracle SQL 表达式，且不能包含 `AS 属性名`。
14. 制造业常见场景中，站点编码、产线编码、工序编码、设备编码、工单号、批次号等如果是从复合字段里抽取出来的，应优先输出 COMPUTED，而不是 DIRECT。
15. 如果模型能明确写出可执行的抽取公式，应优先给出 formula，不要只写概念性说明。
16. 若 formula 非空，而 mappingType 不是 COMPUTED，则说明输出不合规。
17. 若某属性无法明确落到源表字段或基于源字段的 SQL 表达式，则不要输出该映射建议。
{f"18. 还必须遵守以下额外业务映射指令：{extra_instruction}" if extra_instruction else ""}

典型示例：
1. 直接映射
{{
  "propertyName": "defect_code",
  "sourceTable": "PDX25_DEFECT",
  "sourceColumn": "DEFECT_CODE",
  "mappingType": "DIRECT",
  "formula": ""
}}

2. 从复合字段中抽取工站编码
{{
  "propertyName": "station_code",
  "sourceTable": "PDX25_TAMS_PROCESS",
  "sourceColumn": "PROCESS_NAME",
  "mappingType": "COMPUTED",
  "formula": "REGEXP_SUBSTR(PROCESS_NAME, '^ST[0-9]+')"
}}

3. 从带分隔符字符串中抽取首段产线编码
{{
  "propertyName": "line_code",
  "sourceTable": "PDX25_ROUTE_LOG",
  "sourceColumn": "ROUTE_DESC",
  "mappingType": "COMPUTED",
  "formula": "REGEXP_SUBSTR(ROUTE_DESC, '[^_-]+', 1, 1)"
}}

输出格式：
{{
  "mappings": [
    {{
      "propertyName": "英文属性名",
      "propertyDisplayName": "中文显示名",
      "propertyDesc": "属性说明",
      "matchedPropertyId": "已存在属性ID，没有则返回空字符串",
      "matchedPropertyName": "已存在属性名，没有则返回空字符串",
      "sourceTable": "源表名",
      "sourceColumn": "源字段名",
      "sourceDataType": "源字段类型",
      "mappingType": "DIRECT",
      "confidence": "HIGH",
      "reason": "匹配理由",
      "formula": ""
    }}
  ]
}}"""

    def _make_json_safe(self, value: Any) -> Any:
        if hasattr(value, "read"):
            try:
                return self._make_json_safe(value.read())
            except Exception:
                return None
        if isinstance(value, dict):
            return {str(key): self._make_json_safe(val) for key, val in value.items()}
        if isinstance(value, list):
            return [self._make_json_safe(item) for item in value]
        if isinstance(value, tuple):
            return [self._make_json_safe(item) for item in value]
        if isinstance(value, datetime):
            return value.isoformat(sep=" ", timespec="seconds")
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value) if value.as_tuple().exponent < 0 else int(value)
        if isinstance(value, bytes):
            return value.hex()
        return value

    async def generate_data_object_comments(
        self,
        table_detail: Dict[str, Any],
        primary_config_id: Optional[str] = None,
        verifier_config_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """为数据表和字段生成描述建议"""
        system_prompt = """你是一个资深数据架构师，擅长根据数据库表结构推断业务含义，并生成简洁、准确的中文表说明与字段说明。

你的任务：
1. 仅针对缺少 comments 的表和字段生成描述建议。
2. 优先根据表名、字段名、数据类型、默认值和样例数据推断含义。
3. 描述必须简洁、偏业务语义，避免空话和模板化措辞。
4. 不要臆造不存在的业务规则；不确定时给出保守、中性的描述。
5. 输出必须是 JSON，不要输出 Markdown。

输出格式：
{
  "table_comment": "表描述，没有建议时返回空字符串",
  "columns": [
    {
      "column_name": "字段名",
      "comment": "字段描述"
    }
  ]
}"""

        primary_config = self._get_config_by_id(primary_config_id) if primary_config_id else self._get_default_config()
        if primary_config_id and not primary_config:
            raise ValueError("主模型配置不存在或未启用")

        verifier_config = self._get_config_by_id(verifier_config_id) if verifier_config_id else None
        if verifier_config_id and not verifier_config:
            raise ValueError("校验模型配置不存在或未启用")

        prompt_payload = self._build_data_object_prompt_payload(table_detail)

        user_prompt = f"""请为下面的数据对象补全缺失的 comments。

数据对象信息：
{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}

要求：
- 已有 comments 的表和字段不要改写。
- 只为 comments 为空的对象返回建议。
- 返回严格 JSON。"""

        result_text = await self.call_llm(system_prompt, user_prompt, primary_config)
        primary_result = self._normalize_data_object_comment_result(self._extract_json_object(result_text))

        if primary_result:
            result = {
                **primary_result,
                "generation_mode": "llm",
            }
        else:
            result = self._fallback_data_object_comments(table_detail)

        result["primary_model"] = self._config_brief(primary_config)

        if verifier_config:
            verified_result = await self._verify_data_object_comments(
                table_detail=table_detail,
                candidate_result=result,
                verifier_config=verifier_config,
            )
            if verified_result:
                result = {
                    **verified_result,
                    "generation_mode": result.get("generation_mode", "llm"),
                    "primary_model": result.get("primary_model"),
                    "verification_mode": "llm",
                    "verifier_model": self._config_brief(verifier_config),
                }
            else:
                result = {
                    **result,
                    "verification_mode": "llm",
                    "verifier_model": self._config_brief(verifier_config),
                }
        else:
            result["verification_mode"] = ""
            result["verifier_model"] = None

        return result

    async def generate_ontology_blueprint(
        self,
        domain: Any,
        business_document: str,
        relation_tables: List[Dict[str, Any]],
        source_role_bindings: Optional[List[Dict[str, Any]]] = None,
        semantic_patterns: Optional[List[Dict[str, Any]]] = None,
        config_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """根据分析域说明、业务文档和关系表元数据生成本体实体与关系蓝图"""
        config = self._get_config_by_id(config_id) if config_id else self._get_default_config()
        if config_id and not config:
            raise ValueError("所选大模型配置不存在或未启用")

        system_prompt = """你是一个资深企业本体架构师，负责把业务文档、源数据角色和语义模式转化为“业务实体对象 + 业务关系”的本体蓝图。

你的任务：
1. 阅读业务分析域名称、业务分析域说明、用户输入的业务文档，以及所选源表的表说明、字段说明。
2. 结合每张源表的 sourceRole（如 entity_master / process_history / measurement / rule_catalog / case_library）理解它在语义模型中的职责。
3. 优先从业务说明文档中识别“本次最小可行域（MVP）/ 首次切实可行范围”，只抽取为当前分析目标真正必要的业务实体对象。
4. 结合已启用的 semanticPatterns（如 process-trace、measurement-threshold-violation、case-rootcause-action）抽取真正必要的业务实体对象。
4. 避免重复、避免把纯中间关系表直接当作业务实体；优先抽取更稳定的业务语义对象。
5. 识别实体之间的业务关系，并给出合适的关系类型：
   - ONE_TO_ONE
   - ONE_TO_MANY
   - MANY_TO_MANY
   - INHERITANCE
   - ASSOCIATION
6. 为每个实体给出简洁中文显示名、简洁说明、建议构建方式（通常为 TABLE 或 VIEW）。
7. 为每个实体补出一组必要的核心属性，优先包含标识类字段和关键业务字段，不要为了覆盖所有列而把属性设计得过于复杂。
8. 当 measurement-threshold-violation 模式启用，且输入中同时存在 measurement 与 rule_catalog 角色时，可以生成“测量值 / 规则 / 超差事件”这类派生语义对象。
9. 对于本体对象/属性/关系生成，应尽量基于当前输入中每张表的全量字段进行判断；只有当 omitted_column_count > 0 时，才表示当前批次对该表做了裁剪。
10. 如果某张表带有 segment_index / segment_count，表示这是一张宽表的字段分片；你需要基于当前分片先提取稳定实体、属性和关系线索，后续系统会把多分片结果合并。
11. 本次设计目标是“切实可行”，不是“一次建全”。实体数量、关系数量和每个实体的属性数量都应保持克制，避免过度设计。
12. 输出必须是严格 JSON，不要输出 Markdown，不要解释过程。

输出格式：
{
  "entities": [
    {
      "entityName": "英文实体名，建议 PascalCase",
      "entityDisplayName": "中文显示名",
      "entityDesc": "实体说明",
      "buildType": "TABLE",
      "sourceHints": ["来源表1", "来源表2"],
      "properties": [
        {
          "propertyName": "英文属性名，下划线风格",
          "propertyDisplayName": "中文显示名",
          "propertyDesc": "属性说明",
          "dataType": "VARCHAR2",
          "isPrimaryKey": "Y",
          "isNullable": "N",
          "sourceTable": "来源源表名",
          "sourceColumn": "来源源字段名；若为计算属性也必须填写原始来源字段",
          "sourceDataType": "原始来源字段类型",
          "mappingType": "DIRECT 或 COMPUTED",
          "formula": "若 mappingType=COMPUTED，则填写 Oracle SQL 表达式"
        }
      ]
    }
  ],
  "relations": [
    {
      "sourceEntityName": "源实体英文名",
      "targetEntityName": "目标实体英文名",
      "relationName": "关系名称，可中文",
      "relationType": "ASSOCIATION",
      "relationDesc": "关系说明",
      "evidenceTables": ["关系表1"]
    }
  ]
}"""

        compact_tables = relation_tables[: min(len(relation_tables), 8)]
        prompt_payload = {
            "domain_name": getattr(domain, "domain_name", ""),
            "domain_desc": getattr(domain, "domain_desc", ""),
            "business_document": self._truncate_text((business_document or "").strip(), 8000),
            "source_role_bindings": source_role_bindings or [],
            "semantic_patterns": semantic_patterns or [],
            "relation_tables": [
                self._compact_guide_prompt_table(table)
                for table in compact_tables
            ],
        }

        user_prompt = f"""请根据以下上下文生成业务本体蓝图。

输入信息：
{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}

要求：
- relation_tables 中如果 omitted_column_count = 0，表示当前表字段已完整提供；只有 omitted_column_count > 0 时才表示仍有未展示列。
- 如果某张表带有 segment_index / segment_count，表示当前批次只提供了这张宽表的一部分字段，请照样提取本批次可确认的实体、属性和关系线索。
- 优先根据业务说明文档确定“本次最小可行域”范围，只设计当前分析目标真正需要的本体对象和关系。
- 优先抽取“业务实体”，不要把明显的中间关系表直接原样复制为业务实体。
- 如果关系表中体现的是两个实体之间的关联，应尽量还原成两个实体 + 一条关系。
- 如果某张表被标注为 measurement 或 rule_catalog，请优先从“测量、规则、判定、异常”角度理解，而不是仅按源表名机械翻译。
- 实体名必须稳定、简洁、避免重复。
- 属性只保留必要核心字段，不要把所有源表字段机械照搬，也不要为了完整性引入过多次要属性。
- 对每个属性，尽可能给出属性级来源线索：`sourceTable / sourceColumn / mappingType / formula`。如果属性无法回溯到源数据表字段或基于源字段的 Oracle SQL 表达式，就不要纳入本次实体设计。
- 关系必须引用 entities 中实际存在的实体名。
- 返回严格 JSON。"""

        result_text = await self.call_llm(
            system_prompt,
            user_prompt,
            config,
            timeout_override=max((config.timeout if config else 60), 180)
        )
        normalized = self._normalize_ontology_blueprint_result(
            payload=self._extract_json_object(result_text),
            relation_tables=relation_tables,
        )
        generation_mode = "llm"
        if not normalized:
            normalized = self._fallback_ontology_blueprint(relation_tables)
            generation_mode = "fallback"

        return {
            **normalized,
            "generation_mode": generation_mode,
            "model": self._config_brief(config),
        }

    async def generate_entity_candidates(
        self,
        domain: Any,
        business_summary: Dict[str, Any],
        ontology_design_document: Dict[str, Any],
        selected_table_schema: Dict[str, Any],
        rule_summary: Dict[str, Any],
        table_roles: List[Dict[str, Any]],
        semantic_patterns: Optional[List[Dict[str, Any]]] = None,
        config_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        config = self._get_config_by_id(config_id) if config_id else self._get_default_config()
        if config_id and not config:
            raise ValueError("所选大模型配置不存在或未启用")

        system_prompt = """你是一个资深企业本体架构师，当前只负责“实体候选与属性候选”的发现，不负责最终关系生成。

你的任务：
1. 优先阅读“高层本体/图谱设计文档”，并把它视为本次实体范围的第一约束。
2. 再结合业务摘要、规则摘要（如有）、表角色识别结果，以及当前批次源表结构补充实体细节。
3. 只生成设计文档中明确纳入首期范围的实体，除非某个缺失实体是支撑这些核心实体落地所必需的最小补充对象。
4. 表名含 WAREHOUSE、STORE、RETAIL 且具有主键的表是默认主数据实体；即使设计文档遗漏，也必须生成对应候选。
5. 优先抽取稳定业务实体，不要把纯中间宽表直接当成业务实体。
6. 为每个候选实体给出必要核心属性，属性数量保持克制。
7. 输出必须是严格 JSON，不要输出 Markdown，不要解释过程。

输出格式：
{
  "entity_candidates": [
    {
      "entityName": "英文实体名，建议 PascalCase",
      "entityDisplayName": "中文显示名",
      "entityDesc": "实体说明",
      "candidateLevel": "HIGH",
      "buildType": "TABLE",
      "sourceHints": ["来源表1"],
      "sourceRoles": ["measurement"],
      "properties": [
        {
          "propertyName": "英文属性名，下划线风格",
          "propertyDisplayName": "中文显示名",
          "propertyDesc": "属性说明",
          "dataType": "VARCHAR2",
          "isPrimaryKey": "Y",
          "isNullable": "N",
          "sourceTable": "来源源表名",
          "sourceColumn": "来源源字段名；若为计算属性也必须填写原始来源字段",
          "sourceDataType": "原始来源字段类型",
          "mappingType": "DIRECT 或 COMPUTED",
          "formula": "若 mappingType=COMPUTED，则填写 Oracle SQL 表达式"
        }
      ]
    }
  ]
}"""

        prompt_payload = {
            "domain_name": getattr(domain, "domain_name", ""),
            "domain_desc": getattr(domain, "domain_desc", ""),
            "business_summary": business_summary or {},
            "ontology_design_document": ontology_design_document or {},
            "rule_summary": rule_summary or {},
            "table_roles": table_roles or [],
            "semantic_patterns": semantic_patterns or [],
            "selected_table_schema": selected_table_schema or {},
        }

        user_prompt = f"""请根据以下上下文生成实体候选。

输入信息：
{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}

要求：
- 生成实体名称，实体属性，以及实体之间的关系。
- candidateLevel 只能使用 HIGH / MEDIUM / LOW。
- 优先抽取业务语义稳定、能承接后续映射和图谱构建的实体。
- 如果 rule_summary.has_concrete_rule_data = true，必须优先依据其中给出的缺陷识别范围、规格族、指标名和上下限语义来设计缺陷相关实体，不要退回泛化命名。
- 严格遵循"ontology_design_document"中定义的范围和优先对象，不要因为源表字段多而扩张设计边界。
- 每个属性都应尽量提供来源于源数据表的属性级来源线索；如果无法明确来源，不要生成该属性。
- 如果当前批次只覆盖部分表，也先输出当前能确认的实体候选。
- 返回严格 JSON。"""

        result_text = await self.call_llm(
            system_prompt,
            user_prompt,
            config,
            timeout_override=max((config.timeout if config else 60), 180)
        )
        normalized = self._normalize_entity_candidates_result(
            payload=self._extract_json_object(result_text),
            relation_tables=(selected_table_schema or {}).get("tables") or [],
        )
        if not normalized:
            blueprint = self._fallback_ontology_blueprint((selected_table_schema or {}).get("tables") or [])
            normalized = {
                "entity_candidates": [
                    {
                        **item,
                        "candidateLevel": "MEDIUM",
                        "sourceRoles": [],
                    }
                    for item in (blueprint.get("entities") or [])
                ]
            }
            generation_mode = "fallback"
        else:
            generation_mode = "llm"

        return {
            **normalized,
            "generation_mode": generation_mode,
            "model": self._config_brief(config),
        }

    async def generate_relation_candidates(
        self,
        domain: Any,
        business_summary: Dict[str, Any],
        ontology_design_document: Dict[str, Any],
        rule_summary: Dict[str, Any],
        table_roles: List[Dict[str, Any]],
        entity_candidates: List[Dict[str, Any]],
        relation_tables: List[Dict[str, Any]],
        semantic_patterns: Optional[List[Dict[str, Any]]] = None,
        config_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        config = self._get_config_by_id(config_id) if config_id else self._get_default_config()
        if config_id and not config:
            raise ValueError("所选大模型配置不存在或未启用")

        system_prompt = """你是一个资深企业本体架构师，当前只负责“关系候选”的发现。

你的任务：
1. 优先阅读“高层本体/图谱设计文档”，并把它视为本次关系范围的第一约束。
2. 再结合业务摘要、规则摘要（如有）、表角色识别结果、实体候选，以及当前批次源表结构。
3. 只在当前已识别的实体候选之间建立关系候选，并且优先保留设计文档中明确要求的首期核心关系。
4. 如果业务摘要体现为缺陷/异常分析场景，可重点识别：
   - 缺陷案例与超限规则
   - 缺陷案例与测量观察
   - 测量观察与测试会话
   - 测试会话与过程站位/设备/工装
   - 缺陷案例与物料/批次/供应商
   否则按通用业务语义识别主对象、过程对象、规则对象、资源对象和结果对象之间的关系。
5. 关系设计要保持简洁，只输出本次分析目标真正需要的关系。
6. 输出必须是严格 JSON，不要输出 Markdown，不要解释过程。

输出格式：
{
  "relation_candidates": [
    {
      "sourceEntityName": "源实体英文名",
      "targetEntityName": "目标实体英文名",
      "relationName": "关系名称，必须使用中文",
      "relationType": "ASSOCIATION",
      "relationDesc": "关系说明",
      "candidateLevel": "HIGH",
      "evidenceTables": ["关系表1"],
      "sourceTable": "关系源表",
      "targetTable": "关系目标表；单表关系可与 sourceTable 相同",
      "joinCondition": "若涉及多表关联，填写候选 Join 条件",
      "edgeSql": "若已经能明确关系边来源SQL，填写返回 EDGE_ID/SOURCE_ID/TARGET_ID 的 Oracle SQL"
    }
  ]
}"""

        prompt_payload = {
            "domain_name": getattr(domain, "domain_name", ""),
            "domain_desc": getattr(domain, "domain_desc", ""),
            "business_summary": business_summary or {},
            "ontology_design_document": ontology_design_document or {},
            "rule_summary": rule_summary or {},
            "table_roles": table_roles or [],
            "semantic_patterns": semantic_patterns or [],
            "entity_candidates": entity_candidates or [],
            "relation_tables": relation_tables or [],
        }

        user_prompt = f"""请根据以下上下文生成关系候选。

输入信息：
{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}

要求：
- 只生成 relation_candidates，不生成新的 entities。
- relationType 只能使用 ONE_TO_ONE / ONE_TO_MANY / MANY_TO_MANY / INHERITANCE / ASSOCIATION。
- candidateLevel 只能使用 HIGH / MEDIUM / LOW。
- 如果 rule_summary.has_concrete_rule_data = true，关系应明确服务于“规格判定 -> 超规指标 -> 缺陷事件/表型 -> 追溯对象”的链路，不要只输出泛化关系。
- 关系必须引用 entity_candidates 中已经存在的实体名。
- relationName 必须使用中文、简短且直接表达源节点到目标节点的业务谓词：优先使用 2～6 个字，不要重复源实体或目标实体名称；完整业务语义写入 relationDesc。
- 优先输出“判定”“产生”“参与”“执行”“归属”“包含”等简洁关系名；例如源节点为“规格规则”、目标节点为“超差事件”时使用“判定”，不要写“规格规则判定超差事件”。
- 不要输出 hasXxx、belongsTo、occursOn、snake_case、camelCase 这类英文关系名。
- 关系设计必须服务于后续 Oracle Graph 边表/edge_sql 落地，尽量补充 `sourceTable / targetTable / joinCondition / edgeSql` 草案；如果一时无法完整写出 `edgeSql`，也至少给出证据表和候选来源表线索。
- 严格遵循 ontology_design_document 中定义的首期关系范围，关系数量保持克制，不要为了覆盖所有潜在线索而构造过于复杂的关系网络。
- 返回严格 JSON。"""

        result_text = await self.call_llm(
            system_prompt,
            user_prompt,
            config,
            timeout_override=max((config.timeout if config else 60), 180)
        )
        normalized = self._normalize_relation_candidates_result(
            payload=self._extract_json_object(result_text),
            entity_candidates=entity_candidates or [],
            relation_tables=relation_tables or [],
        )
        if not normalized:
            normalized = {"relation_candidates": []}
            generation_mode = "fallback"
        else:
            generation_mode = "llm"

        return {
            **normalized,
            "generation_mode": generation_mode,
            "model": self._config_brief(config),
        }

    async def generate_ontology_design_document(
        self,
        domain: Any,
        business_summary: Dict[str, Any],
        selected_table_schema: Dict[str, Any],
        rule_summary: Dict[str, Any],
        table_roles: List[Dict[str, Any]],
        semantic_patterns: Optional[List[Dict[str, Any]]] = None,
        config_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        config = self._get_config_by_id(config_id) if config_id else self._get_default_config()
        if config_id and not config:
            raise ValueError("所选大模型配置不存在或未启用")

        system_prompt = """你是一个资深企业本体架构师，当前只负责“高层本体/图谱设计文档”的制定。

你的任务：
1. 先阅读业务摘要，识别本次最小可行域（MVP）/ 首次切实可行范围。
2. 再结合规则摘要（如有）、表角色识别和源表结构，决定本次首期到底应该构建哪些实体、哪些关系。
3. 本次输出的是高层设计文档，不是最终落库对象清单；重点是定义“做什么”和“先不做什么”。
4. 表名含 WAREHOUSE、STORE、RETAIL 且具有主键的已选业务主数据表，必须纳入 included_entities；不得静默省略。
5. 设计必须切实可行，范围克制，避免因为源表很多或字段很多而扩张。
6. 输出必须是严格 JSON，不要输出 Markdown，不要解释过程。

输出格式：
{
  "mvp_scope": "一句话描述本次最小可行域",
  "scope_reasoning": "为什么这样收敛",
  "included_entities": [
    {
      "entityName": "英文实体名",
      "entityDisplayName": "中文名",
      "reason": "纳入原因",
      "priority": "CORE"
    }
  ],
  "included_relations": [
    {
      "relationName": "关系名，必须使用中文",
      "reason": "纳入原因",
      "priority": "CORE"
    }
  ],
  "excluded_or_deferred": [
    {
      "name": "对象或关系名",
      "reason": "为什么本次不做"
    }
  ],
  "implementation_notes": [
    "后续实现建议"
  ]
}"""

        prompt_payload = {
            "domain_name": getattr(domain, "domain_name", ""),
            "domain_desc": getattr(domain, "domain_desc", ""),
            "business_summary": business_summary or {},
            "rule_summary": rule_summary or {},
            "table_roles": table_roles or [],
            "semantic_patterns": semantic_patterns or [],
            "selected_table_schema": selected_table_schema or {},
        }

        user_prompt = f"""请先做高层本体/图谱设计文档，再作为后续实体和关系生成的边界依据。

输入信息：
{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}

要求：
- 输出本次切实可行的最小范围，不要贪多。
- 如果业务文档已经明确给出首期对象或关系建议，必须优先遵循。
- 如果 rule_summary.has_concrete_rule_data = true，应把这些规则数据视为缺陷识别依据，明确首期缺陷识别范围与相关对象，不要仅停留在通用对象层。
- included_relations 中的 relationName 必须使用中文、简短的关系谓词（优先 2～6 个字），不要重复两端实体名称；完整说明写入 reason。不要使用 hasXxx / belongsTo / occursOn / camelCase / snake_case 之类英文关系名。
- 如果某些对象理论上有价值但首期不必要，应放入 excluded_or_deferred。
- 表名含 WAREHOUSE、STORE、RETAIL 且具有主键的已选业务表属于默认主数据实体，必须列入 included_entities。
- 返回严格 JSON。"""

        result_text = await self.call_llm(
            system_prompt,
            user_prompt,
            config,
            timeout_override=max((config.timeout if config else 60), 180)
        )
        normalized = self._normalize_ontology_design_document_result(self._extract_json_object(result_text))
        if not normalized:
            normalized = {
                "mvp_scope": "围绕当前业务摘要中的核心对象和关键过程构建首期本体。",
                "scope_reasoning": "未获得稳定设计文档输出，回退为最小化默认范围。",
                "included_entities": [],
                "included_relations": [],
                "excluded_or_deferred": [],
                "implementation_notes": [],
            }
            generation_mode = "fallback"
        else:
            generation_mode = "llm"

        return {
            **normalized,
            "generation_mode": generation_mode,
            "model": self._config_brief(config),
        }

    async def generate_semantic_deployment_design(
        self,
        domain: Any,
        business_document: str,
        entities: List[Dict[str, Any]],
        relations: List[Dict[str, Any]],
        relation_tables: List[Dict[str, Any]],
        source_role_bindings: Optional[List[Dict[str, Any]]] = None,
        semantic_patterns: Optional[List[Dict[str, Any]]] = None,
        base_deployment_design: Optional[Dict[str, Any]] = None,
        config_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        config = self._get_config_by_id(config_id) if config_id else self._get_default_config()
        if config_id and not config:
            raise ValueError("所选大模型配置不存在或未启用")

        system_prompt = """你是一个Oracle 26ai 语义建模专家。你的任务不是生成本体对象，而是基于已经确认的本体对象、关系、业务目标和源表结构，生成可直接用于数据分析与属性图构建的“完整业务语义视图”和“边视图”。

核心要求：
1. 生成的视图必须面向后续 Agent 缺陷分析使用，而不是简单对源表做薄包装。
2. 只生成真正有分析价值的语义视图，不要为每张源表机械生成一个 view。
3. 如果一个业务语义对象需要由多段工序、多个测试表或规则表拼接而成，必须直接输出完整 SQL。
4. 语义视图应尽量完整覆盖当前业务场景所需数据，而不是只投影极少数字段。
5. 关系边视图应返回 EDGE_ID、SOURCE_ID、TARGET_ID，必要时可附加其他边属性。
6. SQL 必须使用 Oracle 26ai 兼容语法。
7. source_tables 里每张表可能只展示代表性字段子集；如果 omitted_column_count > 0，说明原表还有未展示的长尾字段，请结合业务文档、本体实体关系和已展示字段生成整体设计。
8. 输出必须是严格 JSON，不要输出 Markdown，不要解释过程。

输出格式：
{
  "semantic_views": [
    {
      "view_name": "VW_EXAMPLE",
      "view_kind": "semantic",
      "source_role": "process_history",
      "source_tables": ["SOURCE_TABLE_A"],
      "purpose": "该视图用于什么分析",
      "deploy": true,
      "deploy_reason": "为什么值得直接部署",
      "sql": "select ..."
    }
  ],
  "edge_views": [
    {
      "view_name": "VW_E_EXAMPLE",
      "purpose": "该边视图用于什么关系分析",
      "deploy": true,
      "deploy_reason": "为什么值得直接部署",
      "source_tables": ["SOURCE_TABLE_A", "SOURCE_TABLE_B"],
      "sql": "select EDGE_ID, SOURCE_ID, TARGET_ID ..."
    }
  ],
  "property_graph": {
    "graph_name": "GRAPH_NAME",
    "vertex_entities": ["EntityA"],
    "edge_relations": ["关系A"],
    "note": "图的设计说明"
  }
}"""

        compact_tables = []
        for table in relation_tables[:12]:
            compact_tables.append(
                self._compact_guide_prompt_table(
                    table,
                    max_columns=60,
                    max_sample_rows=2,
                )
            )

        prompt_payload = {
            "domain": {
                "domain_name": getattr(domain, "domain_name", ""),
                "domain_desc": getattr(domain, "domain_desc", ""),
            },
            "business_document": self._truncate_text((business_document or "").strip(), 9000),
            "entities": entities,
            "relations": relations,
            "source_role_bindings": source_role_bindings or [],
            "semantic_patterns": semantic_patterns or [],
            "base_deployment_design": base_deployment_design or {},
            "source_tables": compact_tables,
        }

        user_prompt = f"""请基于以下输入，生成“完整业务语义视图”和“边视图”的部署设计。

输入信息：
{json.dumps(self._make_json_safe(prompt_payload), ensure_ascii=False, indent=2)}

要求：
- 语义视图必须服务于后续缺陷分析、追溯、归因和属性图查询。
- 不要为 source_wrap 草案默认生成 SQL，除非你判断它已经是完整业务语义视图。
- 如果像 ProcessStep、Measurement、Violation 这类对象需要由多个来源拼接，请直接输出完整 SQL。
- edge_view 的 sql 必须返回 EDGE_ID、SOURCE_ID、TARGET_ID。
- 只保留值得部署的对象，deploy=true 的对象数量宁少勿滥。
- 返回严格 JSON。"""

        result_text = await self.call_llm(
            system_prompt,
            user_prompt,
            config,
            timeout_override=max((config.timeout if config else 60), 240),
        )
        normalized = self._normalize_semantic_deployment_design(self._extract_json_object(result_text))
        if not normalized:
            normalized = base_deployment_design or {"semantic_views": [], "edge_views": [], "property_graph": {}}
            normalized["generation_mode"] = "fallback"
        else:
            normalized["generation_mode"] = "llm"
        normalized["model"] = self._config_brief(config)
        normalized["llm_raw_output"] = result_text
        return normalized

    async def generate_structured_ontology_scope_document(
        self,
        domain: Any,
        business_summary: Dict[str, Any],
        document_facts: Dict[str, Any],
        rule_analysis: Dict[str, Any],
        schema_analysis: Dict[str, Any],
        focus_scope: Dict[str, Any],
        canonical_model: Dict[str, Any],
        config_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        config = self._get_config_by_id(config_id) if config_id else self._get_default_config()
        if config_id and not config:
            raise ValueError("所选大模型配置不存在或未启用")

        system_prompt = """你是一个制造业本体方案架构师。当前你的任务不是重新设计对象或关系，而是基于已经确定的 canonical ontology 骨架，补写一份简洁、可执行的“高层范围说明文档”。

要求：
1. 不能删除 canonical_model 中已有核心对象和关系。
2. 只能解释为什么当前首期范围这样收敛，以及哪些内容延后。
3. included_entities / included_relations 必须只引用 canonical_model 中已经存在的对象和关系。
4. 输出必须是严格 JSON，不要输出 Markdown。"""

        prompt_payload = {
            "domain_name": getattr(domain, "domain_name", ""),
            "domain_desc": getattr(domain, "domain_desc", ""),
            "business_summary": business_summary or {},
            "document_facts": document_facts or {},
            "rule_analysis": rule_analysis or {},
            "schema_analysis": schema_analysis or {},
            "focus_scope": focus_scope or {},
            "canonical_model": canonical_model or {},
        }
        user_prompt = f"""请基于以下结构化分析结果和既定 canonical ontology，生成高层范围说明文档。

输入信息：
{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}

输出格式：
{{
  "mvp_scope": "一句话描述当前首期范围",
  "scope_reasoning": "为什么这样收敛",
  "included_entities": [
    {{"entityName":"英文实体名","entityDisplayName":"中文名","reason":"纳入原因","priority":"CORE"}}
  ],
  "included_relations": [
    {{"relationName":"中文关系名","reason":"纳入原因","priority":"CORE"}}
  ],
  "excluded_or_deferred": [
    {{"name":"延后对象或关系名","reason":"延后原因"}}
  ],
  "implementation_notes": ["实施建议"]
}}

要求：
- 不要发明新的核心对象或关系。
- 解释应围绕 focus_scope、规则覆盖范围、关键站位和标准化层设计展开。
- 返回严格 JSON。"""

        result_text = await self.call_llm(
            system_prompt,
            user_prompt,
            config,
            timeout_override=max((config.timeout if config else 60), 180),
        )
        normalized = self._normalize_ontology_design_document_result(self._extract_json_object(result_text))
        if not normalized:
            normalized = {
                "mvp_scope": "围绕首期规则覆盖范围、关键测试链路和过程追溯对象构建本体。",
                "scope_reasoning": "当前按结构化分析锁定产品、测试、规格、过程、设备与根因知识层的最小可行范围。",
                "included_entities": [
                    {
                        "entityName": item.get("entityName"),
                        "entityDisplayName": item.get("entityDisplayName") or item.get("entityName"),
                        "reason": "已纳入 canonical ontology 首期骨架。",
                        "priority": "CORE",
                    }
                    for item in (canonical_model.get("entities") or [])[:12]
                    if item.get("entityName")
                ],
                "included_relations": [
                    {
                        "relationName": item.get("relationName"),
                        "reason": "已纳入 canonical ontology 首期关系骨架。",
                        "priority": "CORE",
                    }
                    for item in (canonical_model.get("relations") or [])[:12]
                    if item.get("relationName")
                ],
                "excluded_or_deferred": [],
                "implementation_notes": ["后续阶段补充标准化视图 SQL 与边 SQL。"],
            }
            generation_mode = "fallback"
        else:
            generation_mode = "llm"

        normalized["generation_mode"] = generation_mode
        normalized["model"] = self._config_brief(config)
        return normalized

    async def enrich_structured_canonical_model(
        self,
        domain: Any,
        business_summary: Dict[str, Any],
        rule_analysis: Dict[str, Any],
        schema_analysis: Dict[str, Any],
        canonical_model: Dict[str, Any],
        selected_table_schema: Dict[str, Any],
        table_roles: List[Dict[str, Any]],
        config_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        config = self._get_config_by_id(config_id) if config_id else self._get_default_config()
        if config_id and not config:
            raise ValueError("所选大模型配置不存在或未启用")

        system_prompt = """你是一个制造业本体建模专家。当前你的任务不是创建新的对象或关系，而是对既有 canonical ontology 做 enrichment。

允许的工作：
1. 补充或润色实体中文名、实体说明、属性说明。
2. 为既有关系补充关系说明、证据表、sourceTable、targetTable、joinCondition、edgeSql 草案。
3. 必须保留现有 entityName、relationName 语义骨架，不要新增新的核心对象或关系。
4. 输出严格 JSON。"""

        prompt_payload = {
            "domain_name": getattr(domain, "domain_name", ""),
            "business_summary": business_summary or {},
            "rule_analysis": rule_analysis or {},
            "schema_analysis": schema_analysis or {},
            "table_roles": table_roles or [],
            "selected_table_schema": selected_table_schema or {},
            "canonical_model": canonical_model or {},
        }
        user_prompt = f"""请对以下 canonical ontology 做 enrichment。

输入信息：
{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}

输出格式：
{{
  "entities": [{{ ...与 canonical_model.entities 相同 schema ... }}],
  "relations": [{{ ...与 canonical_model.relations 相同 schema ... }}]
}}

要求：
- entities 只能返回 canonical_model 中已经存在的 entityName。
- relations 只能返回 canonical_model 中已经存在的 sourceEntityName / targetEntityName / relationName 组合。
- 优先补充关系证据表和 joinCondition / edgeSql 草案。
- 返回严格 JSON。"""

        result_text = await self.call_llm(
            system_prompt,
            user_prompt,
            config,
            timeout_override=max((config.timeout if config else 60), 180),
        )
        normalized = self._normalize_ontology_blueprint_result(
            payload=self._extract_json_object(result_text),
            relation_tables=(selected_table_schema or {}).get("tables") or [],
        )
        if not normalized:
            normalized = {
                "entities": canonical_model.get("entities") or [],
                "relations": canonical_model.get("relations") or [],
            }
            generation_mode = "fallback"
        else:
            generation_mode = "llm"

        normalized["generation_mode"] = generation_mode
        normalized["model"] = self._config_brief(config)
        return normalized

    async def enrich_structured_view_plan(
        self,
        domain: Any,
        business_document: str,
        canonical_model: Dict[str, Any],
        view_plan: Dict[str, Any],
        selected_table_schema: Dict[str, Any],
        config_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        config = self._get_config_by_id(config_id) if config_id else self._get_default_config()
        if config_id and not config:
            raise ValueError("所选大模型配置不存在或未启用")

        system_prompt = """你是一个 Oracle 26ai 语义建模专家。当前你的任务不是重新规划 view plan，而是对既有 view plan 做 enrichment。

允许的工作：
1. 为已有 standardized view 补充更准确的 purpose、deploy_reason、SQL 草案。
2. 为已有 edge view 补充 purpose、deploy_reason、SQL 草案。
3. 不要新增新的 view_name。
4. 输出严格 JSON。"""

        compact_tables = []
        for table in (selected_table_schema or {}).get("tables") or []:
            compact_tables.append(
                self._compact_guide_prompt_table(table, max_columns=40, max_sample_rows=1)
            )

        prompt_payload = {
            "domain_name": getattr(domain, "domain_name", ""),
            "business_document": self._truncate_text((business_document or "").strip(), 8000),
            "canonical_model": canonical_model or {},
            "view_plan": view_plan or {},
            "selected_table_schema": compact_tables,
        }
        user_prompt = f"""请对以下 view plan 做 enrichment。

输入信息：
{json.dumps(self._make_json_safe(prompt_payload), ensure_ascii=False, indent=2)}

输出格式：
{{
  "semantic_views": [{{"view_name":"V_EXAMPLE","view_kind":"standardized","source_role":"standardized","source_tables":["T"],"purpose":"...","deploy":true,"deploy_reason":"...","sql":"select ..."}}],
  "edge_views": [{{"view_name":"VW_E_EXAMPLE","purpose":"...","deploy":true,"deploy_reason":"...","source_tables":["T1","T2"],"sql":"select EDGE_ID, SOURCE_ID, TARGET_ID ..."}}],
  "property_graph": {{"graph_name":"GRAPH_NAME","vertex_entities":["A"],"edge_relations":["关系"],"note":"说明"}}
}}

要求：
- 只能返回 view_plan 中已存在的 view_name。
- 没把握的对象可以 deploy=false 且 sql 为空。
- 返回严格 JSON。"""

        result_text = await self.call_llm(
            system_prompt,
            user_prompt,
            config,
            timeout_override=max((config.timeout if config else 60), 240),
        )
        normalized = self._normalize_semantic_deployment_design(self._extract_json_object(result_text))
        if not normalized:
            normalized = {
                "semantic_views": view_plan.get("standardized_views") or [],
                "edge_views": view_plan.get("edge_views") or [],
                "property_graph": view_plan.get("graph_layer") or {},
            }
            generation_mode = "fallback"
        else:
            generation_mode = "llm"

        normalized["generation_mode"] = generation_mode
        normalized["model"] = self._config_brief(config)
        return normalized

    async def generate_process_blueprint(
        self,
        domain: Any,
        process_type: str,
        process_description: str,
        config_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """根据用户提供的流程说明生成可编辑的流程图蓝图。"""
        config = self._get_config_by_id(config_id) if config_id else self._get_default_config()
        if config_id and not config:
            raise ValueError("所选大模型配置不存在或未启用")

        type_labels = {
            "DATA_ANALYSIS": "数据分析流程",
            "BUSINESS_PROCESS": "业务处理流程",
            "CUSTOM": "自定义流程",
        }
        normalized_type = process_type if process_type in type_labels else "CUSTOM"
        system_prompt = """你是一名企业流程架构师。请将用户的流程说明转化为可编辑的流程图。

只能输出严格 JSON，不要输出 Markdown 或解释。节点 type 只能是 start、dataInput、analysis、decision、action、end。
数据分析流程优先使用 dataInput、analysis、decision；业务处理流程优先使用 action、decision。流程必须有且仅有一个 start 和一个 end。

输出格式：
{
  "process_name": "简洁的中文流程名称",
  "process_desc": "流程目的说明",
  "nodes": [
    {"id":"n1", "type":"start", "label":"开始", "desc":"", "config":{}},
    {"id":"n2", "type":"analysis", "label":"分析步骤", "desc":"步骤说明", "config":{}},
    {"id":"n3", "type":"end", "label":"结束", "desc":"", "config":{}}
  ],
  "edges": [{"source":"n1", "target":"n2"}, {"source":"n2", "target":"n3"}]
}"""
        prompt_payload = {
            "domain_name": getattr(domain, "domain_name", ""),
            "domain_desc": getattr(domain, "domain_desc", ""),
            "process_type": type_labels[normalized_type],
            "process_description": self._truncate_text(process_description, 10000),
        }
        result_text = await self.call_llm(
            system_prompt,
            f"请生成流程图蓝图：\n{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}",
            config,
            timeout_override=max((config.timeout if config else 60), 180),
        )
        normalized = self._normalize_process_blueprint(
            self._extract_json_object(result_text), normalized_type, process_description
        )
        generation_mode = "llm"
        if not normalized:
            normalized = self._fallback_process_blueprint(normalized_type, process_description)
            generation_mode = "fallback"
        return {
            **normalized,
            "process_type": normalized_type,
            "generation_mode": generation_mode,
            "model": self._config_brief(config),
        }

    async def generate_ontology_adjustment_plan(
        self,
        domain: Any,
        entities: List[Any],
        relations: List[Any],
        instruction: str,
        selected_entity_id: Optional[str] = None,
        config_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """根据自然语言描述生成本体对象、属性与关系调整计划。"""
        config = self._get_config_by_id(config_id) if config_id else self._get_default_config()
        if config_id and not config:
            raise ValueError("所选大模型配置不存在或未启用")

        selected_entity = next((item for item in entities if item.entity_id == selected_entity_id), None)
        system_prompt = """你是制造业本体建模专家。你的任务是根据操作员的自然语言要求，对现有本体中的实体、属性和关系生成“可执行的调整计划”。

约束：
1. 只能围绕当前分析域已有本体做调整，除非用户明确要求新增对象或新增属性。
2. 尽量优先复用现有实体和属性，不要随意重复创建近义对象。
3. 实体 action 只能是 create / update / delete。
4. 属性 action 只能是 create / update / delete。
5. 关系 action 只能是 create / update / delete。
6. entityName 使用 PascalCase；propertyName 使用英文下划线；displayName 和 desc 使用中文。
7. buildType 只能是 TABLE 或 VIEW。
8. relationType 只能是 ONE_TO_ONE / ONE_TO_MANY / MANY_TO_MANY / INHERITANCE / ASSOCIATION。
9. 如果是 update 或 delete，优先填写现有 entityId / propertyId / relationId；如果无法确定，可再补 entityName / propertyName / relationName 作为定位线索。
10. 若用户表达不清，不要臆造大范围修改；保持最小必要改动。
11. 输出必须是严格 JSON，不要输出 Markdown，不要解释过程。

输出格式：
{
  "summary": "一句话总结本次调整意图",
  "entityActions": [
    {
      "action": "update",
      "entityId": "ent_xxx",
      "entityName": "DefectRecord",
      "entityDisplayName": "缺陷记录",
      "entityDesc": "实体说明",
      "buildType": "TABLE",
      "color": "#66bb6a",
      "reason": "调整原因"
    }
  ],
  "propertyActions": [
    {
      "action": "create",
      "entityId": "ent_xxx",
      "entityName": "DefectRecord",
      "propertyId": "",
      "propertyName": "defect_level",
      "propertyDisplayName": "缺陷等级",
      "propertyDesc": "缺陷严重程度等级",
      "dataType": "VARCHAR2",
      "isPrimaryKey": "N",
      "isNullable": "Y",
      "orderNum": 0,
      "reason": "补充分析需要的关键属性"
    }
  ],
  "relationActions": [
    {
      "action": "create",
      "relationId": "",
      "sourceEntityId": "ent_a",
      "sourceEntityName": "DefectRecord",
      "targetEntityId": "ent_b",
      "targetEntityName": "WorkOrder",
      "relationName": "关联工单",
      "relationType": "ASSOCIATION",
      "relationDesc": "缺陷记录关联到工单",
      "reason": "补齐对象关系"
    }
  ]
}"""

        prompt_payload = {
            "domain": {
                "domain_id": getattr(domain, "domain_id", ""),
                "domain_name": getattr(domain, "domain_name", ""),
                "domain_desc": getattr(domain, "domain_desc", ""),
            },
            "selected_entity": {
                "entity_id": selected_entity.entity_id,
                "entity_name": selected_entity.entity_name,
                "entity_display_name": selected_entity.entity_display_name,
                "entity_desc": selected_entity.entity_desc,
                "build_type": selected_entity.build_type,
                "properties": [
                    {
                        "property_id": prop.property_id,
                        "property_name": prop.property_name,
                        "property_display_name": prop.property_display_name,
                        "property_desc": prop.property_desc,
                        "data_type": prop.data_type,
                        "is_primary_key": prop.is_primary_key,
                        "is_nullable": prop.is_nullable,
                    }
                    for prop in (selected_entity.properties or [])
                ],
            } if selected_entity else None,
            "entities": [
                {
                    "entity_id": entity.entity_id,
                    "entity_name": entity.entity_name,
                    "entity_display_name": entity.entity_display_name,
                    "entity_desc": entity.entity_desc,
                    "build_type": entity.build_type,
                    "status": entity.status,
                    "properties": [
                        {
                            "property_id": prop.property_id,
                            "property_name": prop.property_name,
                            "property_display_name": prop.property_display_name,
                            "property_desc": prop.property_desc,
                            "data_type": prop.data_type,
                            "is_primary_key": prop.is_primary_key,
                            "is_nullable": prop.is_nullable,
                        }
                        for prop in (entity.properties or [])[:50]
                    ],
                }
                for entity in entities[:40]
            ],
            "relations": [
                {
                    "relation_id": relation.relation_id,
                    "source_entity_id": relation.source_entity_id,
                    "source_entity_name": relation.source_entity.entity_name if relation.source_entity else "",
                    "target_entity_id": relation.target_entity_id,
                    "target_entity_name": relation.target_entity.entity_name if relation.target_entity else "",
                    "relation_name": relation.relation_name,
                    "relation_type": relation.relation_type,
                    "relation_desc": relation.relation_desc,
                }
                for relation in relations[:60]
            ],
            "instruction": self._truncate_text(instruction, 4000),
        }

        user_prompt = f"""请基于以下现有本体上下文，为操作员生成可执行的最小化调整计划。

输入信息：
{json.dumps(self._make_json_safe(prompt_payload), ensure_ascii=False, indent=2)}

要求：
- 如果用户只要求调整某个对象的属性，不要改动无关实体。
- 若需要新增属性，优先挂到最合适的现有实体下。
- 删除动作只在用户明确表达“删除 / 去掉 / 移除 / 不再需要”时使用。
- 返回严格 JSON。"""

        result_text = await self.call_llm(
            system_prompt,
            user_prompt,
            config,
            timeout_override=max((config.timeout if config else 60), 180),
        )
        normalized = self._normalize_ontology_adjustment_plan(self._extract_json_object(result_text))
        generation_mode = "llm"
        if not normalized:
            normalized = {
                "summary": "未能从模型结果中提取有效调整动作",
                "entityActions": [],
                "propertyActions": [],
                "relationActions": [],
            }
            generation_mode = "fallback"
        return {
            **normalized,
            "generation_mode": generation_mode,
            "model": self._config_brief(config),
            "selected_entity_id": selected_entity_id,
            "instruction": instruction.strip(),
            "llm_raw_output": result_text,
        }

    def _normalize_ontology_adjustment_plan(self, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not payload:
            return None

        allowed_entity_actions = {"create", "update", "delete"}
        allowed_property_actions = {"create", "update", "delete"}
        allowed_relation_actions = {"create", "update", "delete"}
        allowed_build_types = {"TABLE", "VIEW"}
        allowed_relation_types = {"ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_MANY", "INHERITANCE", "ASSOCIATION"}

        entity_actions = []
        for item in payload.get("entityActions") or []:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "").strip().lower()
            if action not in allowed_entity_actions:
                continue
            entity_name = self._sanitize_entity_name(item.get("entityName") or "")
            build_type = str(item.get("buildType") or "TABLE").strip().upper()
            entity_actions.append({
                "action": action,
                "entityId": str(item.get("entityId") or "").strip(),
                "entityName": entity_name,
                "entityDisplayName": str(item.get("entityDisplayName") or "").strip()[:200],
                "entityDesc": str(item.get("entityDesc") or "").strip()[:1000],
                "buildType": build_type if build_type in allowed_build_types else "TABLE",
                "color": str(item.get("color") or "").strip()[:20],
                "reason": str(item.get("reason") or "").strip()[:500],
            })

        property_actions = []
        for item in payload.get("propertyActions") or []:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "").strip().lower()
            if action not in allowed_property_actions:
                continue
            property_name = self._sanitize_property_name(item.get("propertyName") or "")
            data_type = str(item.get("dataType") or "VARCHAR2").strip().upper()[:50] or "VARCHAR2"
            is_primary_key = "Y" if str(item.get("isPrimaryKey") or "N").strip().upper() == "Y" else "N"
            is_nullable = "N" if str(item.get("isNullable") or "Y").strip().upper() == "N" else "Y"
            try:
                order_num = int(item.get("orderNum") or 0)
            except Exception:
                order_num = 0
            property_actions.append({
                "action": action,
                "entityId": str(item.get("entityId") or "").strip(),
                "entityName": self._sanitize_entity_name(item.get("entityName") or ""),
                "propertyId": str(item.get("propertyId") or "").strip(),
                "propertyName": property_name,
                "propertyDisplayName": str(item.get("propertyDisplayName") or "").strip()[:200],
                "propertyDesc": str(item.get("propertyDesc") or "").strip()[:500],
                "dataType": data_type,
                "isPrimaryKey": is_primary_key,
                "isNullable": is_nullable,
                "orderNum": max(order_num, 0),
                "reason": str(item.get("reason") or "").strip()[:500],
            })

        relation_actions = []
        for item in payload.get("relationActions") or []:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "").strip().lower()
            if action not in allowed_relation_actions:
                continue
            relation_type = str(item.get("relationType") or "ASSOCIATION").strip().upper()
            relation_actions.append({
                "action": action,
                "relationId": str(item.get("relationId") or "").strip(),
                "sourceEntityId": str(item.get("sourceEntityId") or "").strip(),
                "sourceEntityName": self._sanitize_entity_name(item.get("sourceEntityName") or ""),
                "targetEntityId": str(item.get("targetEntityId") or "").strip(),
                "targetEntityName": self._sanitize_entity_name(item.get("targetEntityName") or ""),
                "relationName": str(item.get("relationName") or "").strip()[:100],
                "relationType": relation_type if relation_type in allowed_relation_types else "ASSOCIATION",
                "relationDesc": str(item.get("relationDesc") or "").strip()[:1000],
                "reason": str(item.get("reason") or "").strip()[:500],
            })

        return {
            "summary": str(payload.get("summary") or "").strip()[:500],
            "entityActions": entity_actions,
            "propertyActions": property_actions,
            "relationActions": relation_actions,
        }

    def _normalize_semantic_deployment_design(self, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not payload:
            return None
        raw_semantic_views = payload.get("semantic_views")
        raw_edge_views = payload.get("edge_views")
        raw_property_graph = payload.get("property_graph")
        if raw_semantic_views is None:
            raw_semantic_views = []
        if raw_edge_views is None:
            raw_edge_views = []
        if not isinstance(raw_semantic_views, list) or not isinstance(raw_edge_views, list):
            return None
        if raw_property_graph is not None and not isinstance(raw_property_graph, dict):
            return None

        semantic_views = []
        for item in raw_semantic_views:
            if not isinstance(item, dict):
                continue
            view_name = str(item.get("view_name") or "").strip().upper()
            sql = str(item.get("sql") or "").strip()
            if not view_name:
                continue
            semantic_views.append({
                "view_name": view_name,
                "view_kind": str(item.get("view_kind") or "semantic").strip().lower() or "semantic",
                "source_role": str(item.get("source_role") or "").strip().lower(),
                "source_tables": [str(x).strip().upper() for x in (item.get("source_tables") or []) if str(x).strip()],
                "purpose": str(item.get("purpose") or "").strip()[:1000],
                "deploy": bool(item.get("deploy")) and bool(sql),
                "deploy_reason": str(item.get("deploy_reason") or "").strip()[:1000],
                "sql": sql or None,
            })

        edge_views = []
        for item in raw_edge_views:
            if not isinstance(item, dict):
                continue
            view_name = str(item.get("view_name") or "").strip().upper()
            sql = str(item.get("sql") or "").strip()
            if not view_name:
                continue
            edge_views.append({
                "view_name": view_name,
                "purpose": str(item.get("purpose") or "").strip()[:1000],
                "deploy": bool(item.get("deploy")) and bool(sql),
                "deploy_reason": str(item.get("deploy_reason") or "").strip()[:1000],
                "source_tables": [str(x).strip().upper() for x in (item.get("source_tables") or []) if str(x).strip()],
                "sql": sql or None,
            })

        property_graph = {
            "graph_name": str((raw_property_graph or {}).get("graph_name") or "").strip().upper(),
            "vertex_entities": [str(x).strip() for x in ((raw_property_graph or {}).get("vertex_entities") or []) if str(x).strip()],
            "edge_relations": [str(x).strip() for x in ((raw_property_graph or {}).get("edge_relations") or []) if str(x).strip()],
            "note": str((raw_property_graph or {}).get("note") or "").strip()[:2000],
        }

        return {
            "semantic_views": semantic_views,
            "edge_views": edge_views,
            "property_graph": property_graph,
        }

    def _normalize_process_blueprint(
        self, payload: Optional[Dict[str, Any]], process_type: str, process_description: str
    ) -> Optional[Dict[str, Any]]:
        if not payload or not isinstance(payload.get("nodes"), list):
            return None
        allowed_types = {"start", "dataInput", "analysis", "decision", "action", "end"}
        raw_nodes = payload["nodes"][:20]
        nodes, node_ids = [], set()
        for index, item in enumerate(raw_nodes):
            if not isinstance(item, dict):
                continue
            node_type = item.get("type") if item.get("type") in allowed_types else "action"
            node_id = str(item.get("id") or f"n{index + 1}").strip()[:50]
            if not node_id or node_id in node_ids:
                node_id = f"n{index + 1}"
            node_ids.add(node_id)
            # 生成流程采用紧凑网格布局，而不是单行无限向右延伸。
            # 这样在固定大小画布中也能直接看到完整流程。
            column = index % 5
            row = index // 5
            nodes.append({
                "id": node_id, "type": node_type,
                "label": str(item.get("label") or node_type)[:100],
                "desc": str(item.get("desc") or "")[:500],
                "config": item.get("config") if isinstance(item.get("config"), dict) else {},
                "position": {"x": 40 + column * 180, "y": 35 + row * 85},
            })
        if len(nodes) < 2 or sum(node["type"] == "start" for node in nodes) != 1 or sum(node["type"] == "end" for node in nodes) != 1:
            return None
        edges, seen_edges = [], set()
        for edge in (payload.get("edges") or [])[:30]:
            if not isinstance(edge, dict):
                continue
            source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
            if source == target or source not in node_ids or target not in node_ids or (source, target) in seen_edges:
                continue
            seen_edges.add((source, target))
            edges.append({"source": source, "target": target})
        if not edges:
            return None
        return {
            "process_name": str(payload.get("process_name") or "智能生成流程")[:200],
            "process_desc": str(payload.get("process_desc") or process_description)[:1000],
            "nodes": nodes, "edges": edges,
        }

    def _fallback_process_blueprint(self, process_type: str, process_description: str) -> Dict[str, Any]:
        middle_type = "analysis" if process_type == "DATA_ANALYSIS" else "action"
        summary = self._truncate_text(process_description, 180)
        nodes = [
            {"id": "n1", "type": "start", "label": "开始", "desc": "启动流程", "config": {}, "position": {"x": 70, "y": 180}},
            {"id": "n2", "type": middle_type, "label": "执行核心步骤", "desc": summary, "config": {}, "position": {"x": 290, "y": 180}},
            {"id": "n3", "type": "end", "label": "结束", "desc": "输出流程结果", "config": {}, "position": {"x": 510, "y": 180}},
        ]
        return {
            "process_name": "智能生成流程",
            "process_desc": self._truncate_text(process_description, 1000),
            "nodes": nodes,
            "edges": [{"source": "n1", "target": "n2"}, {"source": "n2", "target": "n3"}],
        }

    def _generate_mock_mappings(
        self,
        entity: SysOntologyEntity,
        properties: List[SysOntologyProperty],
        source_tables: List[Dict]
    ) -> Dict[str, Any]:
        """生成模拟映射建议"""
        normalized_properties = {
            (p.property_name or "").lower(): p for p in properties
        }
        mappings = []
        for table in source_tables[:8]:
            for col in table.get("columns", [])[:12]:
                column_name = col.get("column_name") or ""
                normalized_name = column_name.lower()
                matched_property = normalized_properties.get(normalized_name)
                mappings.append({
                    "propertyName": matched_property.property_name if matched_property else normalized_name,
                    "propertyDisplayName": matched_property.property_display_name if matched_property else column_name,
                    "propertyDesc": matched_property.property_desc if matched_property else (col.get("comments") or f"{column_name}属性"),
                    "matchedPropertyId": matched_property.property_id if matched_property else "",
                    "matchedPropertyName": matched_property.property_name if matched_property else "",
                    "sourceTable": table.get("table_name", ""),
                    "sourceColumn": column_name,
                    "sourceDataType": col.get("data_type", ""),
                    "mappingType": "DIRECT",
                    "confidence": "MEDIUM" if matched_property else "LOW",
                    "reason": "基于字段名称和注释生成的回退建议",
                    "formula": "",
                })
                if len(mappings) >= max(len(properties), 8):
                    return {"mappings": mappings, "generation_mode": "fallback"}

        return {"mappings": mappings, "generation_mode": "fallback"}

    def _normalize_auto_mapping_result(
        self,
        payload: Optional[Dict[str, Any]],
        entity: SysOntologyEntity,
        properties: List[SysOntologyProperty],
    ) -> Optional[Dict[str, Any]]:
        if not payload:
            return None

        property_index = {prop.property_id: prop for prop in properties}
        property_name_index = {(prop.property_name or "").lower(): prop for prop in properties}
        mappings = payload.get("mappings")
        if not isinstance(mappings, list):
            return None

        normalized_mappings: List[Dict[str, Any]] = []
        seen_pairs = set()
        for item in mappings:
            if not isinstance(item, dict):
                continue
            source_table = (item.get("sourceTable") or "").strip()
            source_column = (item.get("sourceColumn") or "").strip()
            if not source_table or not source_column:
                continue

            matched_property = None
            matched_property_id = (item.get("matchedPropertyId") or "").strip()
            if matched_property_id:
                matched_property = property_index.get(matched_property_id)
            if not matched_property:
                matched_property = property_name_index.get((item.get("matchedPropertyName") or item.get("propertyName") or "").strip().lower())

            property_name = (
                item.get("propertyName")
                or (matched_property.property_name if matched_property else "")
            ).strip()
            if not property_name:
                property_name = source_column.lower()

            unique_key = (property_name.lower(), source_table.upper(), source_column.upper())
            if unique_key in seen_pairs:
                continue
            seen_pairs.add(unique_key)

            formula = (item.get("formula") or "").strip()
            normalized_mappings.append({
                "propertyName": property_name,
                "propertyDisplayName": (
                    item.get("propertyDisplayName")
                    or (matched_property.property_display_name if matched_property else "")
                    or source_column
                ).strip(),
                "propertyDesc": (
                    item.get("propertyDesc")
                    or (matched_property.property_desc if matched_property else "")
                ).strip(),
                "matchedPropertyId": matched_property.property_id if matched_property else "",
                "matchedPropertyName": matched_property.property_name if matched_property else "",
                "sourceTable": source_table,
                "sourceColumn": source_column,
                "sourceDataType": (item.get("sourceDataType") or "").strip(),
                "mappingType": self._normalize_mapping_type(item.get("mappingType"), formula),
                "confidence": (item.get("confidence") or "MEDIUM").upper(),
                "reason": (item.get("reason") or "").strip(),
                "formula": formula,
                "entityName": entity.entity_name,
            })

        if not normalized_mappings:
            return None

        return {
            "mappings": normalized_mappings,
            "generation_mode": "llm",
        }

    def _normalize_mapping_type(self, mapping_type: Optional[str], formula: Optional[str]) -> str:
        normalized_type = (mapping_type or "DIRECT").strip().upper()
        valid_types = {"DIRECT", "COMPUTED", "CONSTANT", "LLM_DERIVED"}
        if normalized_type not in valid_types:
            normalized_type = "DIRECT"

        if (formula or "").strip() and normalized_type == "DIRECT":
            return "COMPUTED"

        return normalized_type

    def _normalize_ontology_blueprint_result(
        self,
        payload: Optional[Dict[str, Any]],
        relation_tables: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not payload:
            return None

        raw_entities = payload.get("entities")
        raw_relations = payload.get("relations")
        if not isinstance(raw_entities, list):
            return None
        if raw_relations is None:
            raw_relations = []
        if not isinstance(raw_relations, list):
            return None

        selected_tables = {
            (table.get("table_name") or "").upper()
            for table in relation_tables
            if table.get("table_name")
        }

        entities: List[Dict[str, Any]] = []
        entity_name_index: Dict[str, str] = {}
        entity_alias_index: Dict[str, str] = {}

        for idx, item in enumerate(raw_entities):
            if not isinstance(item, dict):
                continue

            raw_name = (item.get("entityName") or item.get("entity_name") or "").strip()
            raw_display_name = (item.get("entityDisplayName") or item.get("entity_display_name") or "").strip()
            entity_name = self._sanitize_entity_name(raw_name or raw_display_name or f"GeneratedEntity{idx + 1}")
            if not entity_name:
                continue
            normalized_key = entity_name.lower()
            if normalized_key in entity_name_index:
                continue

            build_type = (item.get("buildType") or item.get("build_type") or "TABLE").upper()
            if build_type not in {"TABLE", "VIEW"}:
                build_type = "TABLE"

            raw_properties = item.get("properties") or []
            properties: List[Dict[str, Any]] = []
            seen_properties = set()
            if isinstance(raw_properties, list):
                for prop_idx, prop in enumerate(raw_properties[:20]):
                    if not isinstance(prop, dict):
                        continue
                    property_name = self._sanitize_property_name(
                        (prop.get("propertyName") or prop.get("property_name") or "").strip()
                        or f"{entity_name.lower()}_{prop_idx + 1}"
                    )
                    if not property_name or property_name in seen_properties:
                        continue
                    seen_properties.add(property_name)
                    source_table = str(prop.get("sourceTable") or prop.get("source_table") or "").strip().upper()
                    if source_table and source_table not in selected_tables:
                        source_table = ""
                    properties.append({
                        "propertyName": property_name,
                        "propertyDisplayName": (prop.get("propertyDisplayName") or prop.get("property_display_name") or property_name).strip(),
                        "propertyDesc": (prop.get("propertyDesc") or prop.get("property_desc") or "").strip(),
                        "dataType": (prop.get("dataType") or prop.get("data_type") or "VARCHAR2").strip().upper(),
                        "isPrimaryKey": "Y" if str(prop.get("isPrimaryKey") or prop.get("is_primary_key") or "N").upper() == "Y" else "N",
                        "isNullable": "N" if str(prop.get("isNullable") or prop.get("is_nullable") or "Y").upper() == "N" else "Y",
                        "sourceTable": source_table,
                        "sourceColumn": str(prop.get("sourceColumn") or prop.get("source_column") or "").strip().upper(),
                        "sourceDataType": str(prop.get("sourceDataType") or prop.get("source_data_type") or "").strip().upper(),
                        "mappingType": self._normalize_mapping_type(prop.get("mappingType") or prop.get("mapping_type"), prop.get("formula") or prop.get("formula_expr")),
                        "formula": str(prop.get("formula") or prop.get("formula_expr") or "").strip(),
                    })

            source_hints = item.get("sourceHints") or item.get("source_hints") or []
            if not isinstance(source_hints, list):
                source_hints = []
            source_hints = [
                str(table_name).strip().upper()
                for table_name in source_hints
                if str(table_name).strip().upper() in selected_tables
            ]

            entity = {
                "entityName": entity_name,
                "entityDisplayName": raw_display_name or entity_name,
                "entityDesc": (item.get("entityDesc") or item.get("entity_desc") or "").strip(),
                "buildType": build_type,
                "sourceHints": source_hints,
                "properties": properties,
            }
            entities.append(entity)
            entity_name_index[normalized_key] = entity_name
            entity_alias_index[entity_name.lower()] = entity_name
            entity_alias_index[(raw_display_name or "").strip().lower()] = entity_name
            entity_alias_index[(raw_name or "").strip().lower()] = entity_name

        if not entities:
            return None

        relations: List[Dict[str, Any]] = []
        seen_relations = set()
        for item in raw_relations:
            if not isinstance(item, dict):
                continue

            source_name = self._resolve_ontology_entity_name(
                item.get("sourceEntityName") or item.get("source_entity_name"),
                entity_name_index,
                entity_alias_index,
            )
            target_name = self._resolve_ontology_entity_name(
                item.get("targetEntityName") or item.get("target_entity_name"),
                entity_name_index,
                entity_alias_index,
            )
            if not source_name or not target_name or source_name == target_name:
                continue

            relation_name = (item.get("relationName") or item.get("relation_name") or "").strip() or "关联"
            relation_type = (item.get("relationType") or item.get("relation_type") or "ASSOCIATION").upper()
            if relation_type not in {"ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_MANY", "INHERITANCE", "ASSOCIATION"}:
                relation_type = "ASSOCIATION"

            evidence_tables = item.get("evidenceTables") or item.get("evidence_tables") or []
            if not isinstance(evidence_tables, list):
                evidence_tables = []
            evidence_tables = [
                str(table_name).strip().upper()
                for table_name in evidence_tables
                if str(table_name).strip().upper() in selected_tables
            ]

            dedupe_key = (source_name.lower(), target_name.lower(), relation_name, relation_type)
            if dedupe_key in seen_relations:
                continue
            seen_relations.add(dedupe_key)
            relations.append({
                "sourceEntityName": source_name,
                "targetEntityName": target_name,
                "relationName": relation_name,
                "relationType": relation_type,
                "relationDesc": (item.get("relationDesc") or item.get("relation_desc") or "").strip(),
                "evidenceTables": evidence_tables,
                "sourceTable": self._normalize_relation_table_name(item.get("sourceTable") or item.get("source_table"), selected_tables),
                "targetTable": self._normalize_relation_table_name(item.get("targetTable") or item.get("target_table"), selected_tables),
                "joinCondition": str(item.get("joinCondition") or item.get("join_condition") or "").strip(),
                "edgeSql": str(item.get("edgeSql") or item.get("edge_sql") or "").strip(),
            })

        return {
            "entities": entities,
            "relations": relations,
        }

    def _normalize_entity_candidates_result(
        self,
        payload: Optional[Dict[str, Any]],
        relation_tables: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not payload:
            return None
        raw_candidates = payload.get("entity_candidates")
        if not isinstance(raw_candidates, list):
            return None

        normalized = self._normalize_ontology_blueprint_result(
            payload={"entities": raw_candidates, "relations": []},
            relation_tables=relation_tables,
        )
        if not normalized:
            return None

        role_by_table = {
            (table.get("table_name") or "").strip().upper(): (table.get("source_role") or "").strip().lower()
            for table in relation_tables
            if table.get("table_name")
        }
        raw_level_by_entity: Dict[str, str] = {}
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            entity_name = self._sanitize_entity_name(
                (item.get("entityName") or item.get("entity_name") or item.get("entityDisplayName") or item.get("entity_display_name") or "").strip()
            ).lower()
            if not entity_name:
                continue
            level = str(item.get("candidateLevel") or item.get("candidate_level") or "MEDIUM").strip().upper()
            raw_level_by_entity[entity_name] = level
        entity_candidates: List[Dict[str, Any]] = []
        for entity in normalized.get("entities") or []:
            source_hints = entity.get("sourceHints") or []
            source_roles = [
                role_by_table.get(str(table_name).strip().upper(), "")
                for table_name in source_hints
                if role_by_table.get(str(table_name).strip().upper(), "")
            ]
            level = raw_level_by_entity.get((entity.get("entityName") or "").strip().lower(), "MEDIUM")
            if level not in {"HIGH", "MEDIUM", "LOW"}:
                level = "MEDIUM"
            entity_candidates.append({
                **entity,
                "candidateLevel": level,
                "sourceRoles": sorted(set(source_roles)),
            })

        return {"entity_candidates": entity_candidates}

    def _normalize_relation_table_name(self, value: Any, selected_tables: set[str]) -> str:
        normalized = str(value or "").strip().upper()
        if not normalized:
            return ""
        return normalized if normalized in selected_tables else ""

    def _normalize_relation_candidates_result(
        self,
        payload: Optional[Dict[str, Any]],
        entity_candidates: List[Dict[str, Any]],
        relation_tables: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not payload:
            return None
        raw_candidates = payload.get("relation_candidates")
        if not isinstance(raw_candidates, list):
            return None

        normalized = self._normalize_ontology_blueprint_result(
            payload={"entities": entity_candidates, "relations": raw_candidates},
            relation_tables=relation_tables,
        )
        if normalized is None:
            return None
        raw_level_by_relation: Dict[tuple[str, str, str, str], str] = {}
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            source_name = self._sanitize_entity_name((item.get("sourceEntityName") or item.get("source_entity_name") or "").strip()).lower()
            target_name = self._sanitize_entity_name((item.get("targetEntityName") or item.get("target_entity_name") or "").strip()).lower()
            relation_name = (item.get("relationName") or item.get("relation_name") or "").strip()
            relation_type = str(item.get("relationType") or item.get("relation_type") or "ASSOCIATION").strip().upper()
            if source_name and target_name and relation_name:
                raw_level_by_relation[(source_name, target_name, relation_name, relation_type)] = str(item.get("candidateLevel") or item.get("candidate_level") or "MEDIUM").strip().upper()
        relation_candidates: List[Dict[str, Any]] = []
        for relation in normalized.get("relations") or []:
            level = raw_level_by_relation.get(
                (
                    (relation.get("sourceEntityName") or "").strip().lower(),
                    (relation.get("targetEntityName") or "").strip().lower(),
                    (relation.get("relationName") or "").strip(),
                    str(relation.get("relationType") or "").strip().upper(),
                ),
                "MEDIUM",
            )
            if level not in {"HIGH", "MEDIUM", "LOW"}:
                level = "MEDIUM"
            relation_candidates.append({
                **relation,
                "candidateLevel": level,
                "sourceTable": relation.get("sourceTable") or "",
                "targetTable": relation.get("targetTable") or "",
                "joinCondition": relation.get("joinCondition") or "",
                "edgeSql": relation.get("edgeSql") or "",
            })
        return {"relation_candidates": relation_candidates}

    def _normalize_ontology_design_document_result(self, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not payload or not isinstance(payload, dict):
            return None

        included_entities = []
        for item in payload.get("included_entities") or []:
            if not isinstance(item, dict):
                continue
            entity_name = self._sanitize_entity_name(
                (item.get("entityName") or item.get("entity_name") or item.get("entityDisplayName") or "").strip()
            )
            if not entity_name:
                continue
            included_entities.append({
                "entityName": entity_name,
                "entityDisplayName": (item.get("entityDisplayName") or item.get("entity_display_name") or entity_name).strip(),
                "reason": (item.get("reason") or "").strip(),
                "priority": (item.get("priority") or "CORE").strip().upper(),
            })

        included_relations = []
        for item in payload.get("included_relations") or []:
            if not isinstance(item, dict):
                continue
            relation_name = (item.get("relationName") or item.get("relation_name") or "").strip()
            if not relation_name:
                continue
            included_relations.append({
                "relationName": relation_name,
                "reason": (item.get("reason") or "").strip(),
                "priority": (item.get("priority") or "CORE").strip().upper(),
            })

        excluded_or_deferred = []
        for item in payload.get("excluded_or_deferred") or []:
            if isinstance(item, dict):
                name = (item.get("name") or "").strip()
                reason = (item.get("reason") or "").strip()
                if name:
                    excluded_or_deferred.append({"name": name, "reason": reason})
            elif isinstance(item, str) and item.strip():
                excluded_or_deferred.append({"name": item.strip(), "reason": ""})

        implementation_notes = [
            str(item).strip()
            for item in (payload.get("implementation_notes") or [])
            if str(item).strip()
        ]

        return {
            "mvp_scope": (payload.get("mvp_scope") or "").strip(),
            "scope_reasoning": (payload.get("scope_reasoning") or "").strip(),
            "included_entities": included_entities,
            "included_relations": included_relations,
            "excluded_or_deferred": excluded_or_deferred,
            "implementation_notes": implementation_notes,
        }

    def _resolve_ontology_entity_name(
        self,
        raw_value: Optional[str],
        entity_name_index: Dict[str, str],
        entity_alias_index: Dict[str, str],
    ) -> Optional[str]:
        normalized = (raw_value or "").strip()
        if not normalized:
            return None
        if normalized.lower() in entity_alias_index:
            return entity_alias_index[normalized.lower()]
        sanitized = self._sanitize_entity_name(normalized)
        if sanitized.lower() in entity_name_index:
            return entity_name_index[sanitized.lower()]
        return None

    def _fallback_ontology_blueprint(self, relation_tables: List[Dict[str, Any]]) -> Dict[str, Any]:
        entities: List[Dict[str, Any]] = []
        relations: List[Dict[str, Any]] = []
        entity_index: Dict[str, Dict[str, Any]] = {}
        relation_index = set()

        generic_ids = {
            "ID",
            "ROW",
            "USER",
            "ORG",
            "TENANT",
            "CREATED",
            "UPDATED",
            "MODIFIED",
        }

        for table in relation_tables:
            table_name = (table.get("table_name") or "").strip().upper()
            table_comment = (table.get("table_comment") or "").strip()
            columns = table.get("columns", []) or []

            candidate_entities: List[str] = []
            for column in columns:
                column_name = (column.get("column_name") or "").strip().upper()
                if not column_name.endswith("_ID"):
                    continue
                base_name = column_name[:-3]
                if not base_name or base_name in generic_ids:
                    continue
                entity_name = self._sanitize_entity_name(base_name)
                if not entity_name:
                    continue
                candidate_entities.append(entity_name)
                if entity_name.lower() not in entity_index:
                    property_name = self._sanitize_property_name(f"{base_name.lower()}_id")
                    entity_index[entity_name.lower()] = {
                        "entityName": entity_name,
                        "entityDisplayName": self._fallback_label(base_name),
                        "entityDesc": f"基于关系表 {table_name} 推断的业务实体",
                        "buildType": "TABLE",
                        "sourceHints": [table_name] if table_name else [],
                        "properties": [
                            {
                                "propertyName": property_name or "id",
                                "propertyDisplayName": f"{self._fallback_label(base_name)}ID",
                                "propertyDesc": f"{self._fallback_label(base_name)}唯一标识",
                                "dataType": "VARCHAR2",
                                "isPrimaryKey": "Y",
                                "isNullable": "N",
                                "sourceTable": table_name,
                                "sourceColumn": column_name,
                                "sourceDataType": "VARCHAR2",
                                "mappingType": "DIRECT",
                                "formula": "",
                            }
                        ],
                    }

            unique_candidates = []
            for entity_name in candidate_entities:
                if entity_name not in unique_candidates:
                    unique_candidates.append(entity_name)

            if len(unique_candidates) >= 2:
                source_name = unique_candidates[0]
                for target_name in unique_candidates[1:]:
                    relation_key = (source_name.lower(), target_name.lower(), table_name)
                    if relation_key in relation_index:
                        continue
                    relation_index.add(relation_key)
                    relations.append({
                        "sourceEntityName": source_name,
                        "targetEntityName": target_name,
                        "relationName": table_comment or "关联",
                        "relationType": "ASSOCIATION",
                        "relationDesc": f"基于关系表 {table_name} 推断的业务关联",
                        "evidenceTables": [table_name] if table_name else [],
                    })

            if not unique_candidates:
                entity_name = self._sanitize_entity_name(table_name or "BusinessObject")
                if entity_name.lower() not in entity_index:
                    properties: List[Dict[str, Any]] = []
                    seen_property_names = set()
                    for column in columns[:12]:
                        property_name = self._sanitize_property_name(column.get("column_name") or "")
                        if not property_name or property_name in seen_property_names:
                            continue
                        seen_property_names.add(property_name)
                        properties.append({
                            "propertyName": property_name,
                            "propertyDisplayName": self._fallback_label(column.get("column_name", "")),
                            "propertyDesc": (column.get("comments") or "").strip(),
                            "dataType": (column.get("data_type") or "VARCHAR2").strip().upper(),
                            "isPrimaryKey": "Y" if property_name == "id" or property_name.endswith("_id") else "N",
                            "isNullable": "Y" if str(column.get("nullable") or "Y").upper() == "Y" else "N",
                            "sourceTable": table_name,
                            "sourceColumn": (column.get("column_name") or "").strip().upper(),
                            "sourceDataType": (column.get("data_type") or "VARCHAR2").strip().upper(),
                            "mappingType": "DIRECT",
                            "formula": "",
                        })
                    entity_index[entity_name.lower()] = {
                        "entityName": entity_name,
                        "entityDisplayName": table_comment or self._fallback_label(table_name),
                        "entityDesc": table_comment or f"基于数据表 {table_name} 推断的业务实体",
                        "buildType": "TABLE",
                        "sourceHints": [table_name] if table_name else [],
                        "properties": properties,
                    }

        entities = list(entity_index.values())
        return {
            "entities": entities,
            "relations": relations,
        }

    def _mock_llm_response(self, system_prompt: str, user_prompt: str, error: str = "") -> str:
        """模拟LLM响应"""
        return json.dumps({"mappings": [], "note": "Mock response - no LLM config available", "error": error})

    def _extract_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        try:
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                payload = json.loads(text[json_start:json_end])
                if isinstance(payload, dict):
                    return payload
        except Exception:
            return None
        return None

    def _normalize_data_object_comment_result(self, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not payload:
            return None

        table_comment = payload.get("table_comment") or ""
        columns = payload.get("columns") or []
        if not isinstance(columns, list):
            return None

        normalized_columns = []
        for column in columns:
            if not isinstance(column, dict):
                continue
            column_name = column.get("column_name")
            comment = column.get("comment")
            if column_name and isinstance(comment, str):
                normalized_columns.append({
                    "column_name": column_name,
                    "comment": comment.strip(),
                })

        return {
            "table_comment": table_comment.strip() if isinstance(table_comment, str) else "",
            "columns": normalized_columns,
        }

    def _build_data_object_prompt_payload(self, table_detail: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "table_name": table_detail.get("table_name"),
            "owner": table_detail.get("owner"),
            "table_comment": table_detail.get("table_comment"),
            "columns": [
                {
                    "column_name": column.get("column_name"),
                    "data_type": column.get("data_type"),
                    "nullable": column.get("nullable"),
                    "default_value": column.get("default_value"),
                    "comments": column.get("comments"),
                }
                for column in table_detail.get("columns", [])
            ],
            "sample_rows": table_detail.get("sample_rows", [])[:5],
        }

    async def _verify_data_object_comments(
        self,
        table_detail: Dict[str, Any],
        candidate_result: Dict[str, Any],
        verifier_config: SysLLMConfig
    ) -> Optional[Dict[str, Any]]:
        system_prompt = """你是负责数据对象注释校验的资深数据治理专家。

你的任务：
1. 审核另一个模型给出的表和字段 comments 建议。
2. 如果建议准确、简洁且稳妥，则保留原建议。
3. 如果建议不准确、过度推断、表达啰嗦，进行修正。
4. 仍然只能为原本 comments 为空的对象给出建议，不得改写已有 comments。
5. 输出必须是 JSON，不要输出 Markdown 或解释。"""

        verification_payload = {
            "data_object": self._build_data_object_prompt_payload(table_detail),
            "candidate_comments": {
                "table_comment": candidate_result.get("table_comment", ""),
                "columns": candidate_result.get("columns", []),
                "generation_mode": candidate_result.get("generation_mode", ""),
            },
        }

        user_prompt = f"""请校验下面的数据对象 comments 建议，并返回最终版本。

输入信息：
{json.dumps(verification_payload, ensure_ascii=False, indent=2)}

要求：
- 如果原建议已经合适，直接保留。
- 如果存在不准确或过度臆断，请改成更稳妥的描述。
- 返回格式仍为严格 JSON：
{{
  "table_comment": "表描述，没有建议时返回空字符串",
  "columns": [
    {{
      "column_name": "字段名",
      "comment": "字段描述"
    }}
  ]
}}"""

        result_text = await self.call_llm(system_prompt, user_prompt, verifier_config)
        return self._normalize_data_object_comment_result(self._extract_json_object(result_text))

    def _config_brief(self, config: Optional[SysLLMConfig]) -> Optional[Dict[str, str]]:
        if not config:
            return None
        return {
            "config_id": config.config_id,
            "config_name": config.config_name,
            "model_name": normalize_model_name(config.model_name, config.api_base_url),
        }

    def _fallback_data_object_comments(self, table_detail: Dict[str, Any]) -> Dict[str, Any]:
        table_comment = ""
        if not table_detail.get("table_comment"):
            table_comment = self._fallback_label(table_detail.get("table_name", "")) + "数据表"

        columns = []
        for column in table_detail.get("columns", []):
            if column.get("comments"):
                continue
            column_name = column.get("column_name", "")
            data_type = column.get("data_type", "")
            default_value = column.get("default_value")
            comment = f"{self._fallback_label(column_name)}"
            if "ID" in column_name.upper():
                comment += "标识"
            elif default_value not in (None, ""):
                comment += f"，默认值为{default_value}"
            elif data_type:
                comment += f"，类型为{data_type}"
            columns.append({
                "column_name": column_name,
                "comment": comment,
            })

        return {
            "table_comment": table_comment,
            "columns": columns,
            "generation_mode": "fallback",
        }

    def _fallback_label(self, name: str) -> str:
        normalized = name.replace("_", " ").strip()
        if not normalized:
            return "字段"
        return normalized.title().replace(" ", "")

    def _compact_guide_prompt_column(self, column: Dict[str, Any]) -> Dict[str, Any]:
        compact_column: Dict[str, Any] = {}
        column_name = column.get("column_name")
        data_type = column.get("data_type")
        comments = self._truncate_text(column.get("comments") or "", 120)

        if isinstance(column_name, str):
            column_name = column_name.strip()
        if column_name:
            compact_column["column_name"] = column_name

        if isinstance(data_type, str):
            data_type = data_type.strip()
        if data_type:
            compact_column["data_type"] = data_type

        if comments:
            compact_column["comments"] = comments
        return compact_column

    def _compact_guide_prompt_table(
        self,
        table: Dict[str, Any],
        max_columns: Optional[int] = None,
        max_sample_rows: int = 0,
    ) -> Dict[str, Any]:
        compact_table: Dict[str, Any] = {}
        optional_text_fields = {
            "table_name": table.get("table_name"),
            "source_role": table.get("source_role"),
            "table_comment": self._truncate_text(table.get("table_comment") or "", 300),
        }
        for key, value in optional_text_fields.items():
            if isinstance(value, str):
                value = value.strip()
            if value:
                compact_table[key] = value

        for key in [
            "total_columns",
            "selected_column_count",
            "omitted_column_count",
            "segment_index",
            "segment_count",
            "segment_column_start",
            "segment_column_end",
        ]:
            value = table.get(key)
            if value is not None:
                compact_table[key] = value

        columns = table.get("columns") or []
        if max_columns is not None:
            columns = columns[:max_columns]
        compact_table["columns"] = [
            self._compact_guide_prompt_column(column)
            for column in columns
        ]

        if max_sample_rows > 0:
            sample_rows = (table.get("sample_rows") or [])[:max_sample_rows]
            if sample_rows:
                compact_table["sample_rows"] = sample_rows
        return compact_table

    def _truncate_text(self, value: str, max_chars: int) -> str:
        normalized = (value or "").strip()
        if len(normalized) <= max_chars:
            return normalized
        return normalized[:max_chars].rstrip() + "..."

    def _sanitize_entity_name(self, value: str) -> str:
        tokens = re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", value or "")
        cleaned = []
        for token in tokens:
            normalized = token.strip()
            if not normalized:
                continue
            if re.search(r"[\u4e00-\u9fff]", normalized):
                normalized = self._fallback_label(normalized)
            cleaned.append(normalized)

        if not cleaned:
            return ""

        result = "".join(part[:1].upper() + part[1:] for part in cleaned)
        result = re.sub(r"[^0-9A-Za-z]", "", result)
        if not result:
            return ""
        if result[0].isdigit():
            result = f"Entity{result}"
        return result[:100]

    def _sanitize_property_name(self, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            return ""
        if re.search(r"[\u4e00-\u9fff]", normalized):
            normalized = self._fallback_label(normalized)
        normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized)
        normalized = re.sub(r"[^0-9A-Za-z]+", "_", normalized).strip("_").lower()
        if not normalized:
            return ""
        if normalized[0].isdigit():
            normalized = f"field_{normalized}"
        return normalized[:100]

    def _extract_model_limits_from_metadata(self, metadata: Dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
        if not isinstance(metadata, dict):
            return None, None

        context_keys = {
            "context_window",
            "context_length",
            "max_context_tokens",
            "input_token_limit",
            "max_input_tokens",
        }
        output_keys = {
            "max_output_tokens",
            "output_token_limit",
            "max_completion_tokens",
        }

        found_context = None
        found_output = None
        stack = [metadata]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                for key, value in current.items():
                    normalized_key = str(key or "").strip().lower()
                    numeric_value = int(value) if isinstance(value, (int, float)) and value else None
                    if normalized_key in context_keys and numeric_value and not found_context:
                        found_context = numeric_value
                    if normalized_key in output_keys and numeric_value and not found_output:
                        found_output = numeric_value
                    if isinstance(value, dict):
                        stack.append(value)
                    elif isinstance(value, list):
                        stack.extend(item for item in value if isinstance(item, (dict, list)))
            elif isinstance(current, list):
                stack.extend(item for item in current if isinstance(item, (dict, list)))

        return found_context, found_output

    async def test_connection(self, config: SysLLMConfig) -> Dict:
        """测试LLM连接"""
        try:
            import time
            start_time = time.time()
            resolved_model = normalize_model_name(config.model_name, config.api_base_url)
            runtime_limits = self.get_runtime_limits(config=config)
            client = None
            http_client = None
            client, http_client = self._build_openai_client(config)
            detected_context_window = None
            detected_output_limit = None
            metadata = {}
            metadata_error = None
            try:
                raw_model = client.models.retrieve(resolved_model)
                metadata = raw_model.model_dump() if hasattr(raw_model, "model_dump") else dict(raw_model or {})
                detected_context_window, detected_output_limit = self._extract_model_limits_from_metadata(metadata)
            except Exception as metadata_exc:
                metadata_error = str(metadata_exc)

            response = client.chat.completions.create(
                model=resolved_model,
                messages=[{"role": "user", "content": "Hello, this is a test message."}],
                max_tokens=50,
                timeout=config.timeout
            )
            duration = time.time() - start_time
            try:
                client.close()
            except Exception:
                pass
            return {
                "success": True,
                "response": response.choices[0].message.content,
                "duration": round(duration, 2),
                "model": resolved_model,
                "configured_model": config.model_name,
                "context_window_tokens": runtime_limits.get("context_window_tokens"),
                "context_window_source": runtime_limits.get("context_window_source"),
                "input_budget_tokens": runtime_limits.get("input_budget_tokens"),
                "detected_context_window_tokens": detected_context_window,
                "detected_output_limit_tokens": detected_output_limit,
                "model_metadata_available": bool(metadata),
                "model_metadata_error": metadata_error,
            }
        except Exception as e:
            try:
                if 'client' in locals() and client:
                    client.close()
            except Exception:
                pass
            return {
                "success": False,
                "error": str(e),
                "model": normalize_model_name(config.model_name, config.api_base_url),
                "configured_model": config.model_name,
            }

    async def generate_ddl_prompt(self, domain, entities, relations, blueprint_package: Optional[Dict[str, Any]] = None) -> str:
        """构造DDL生成LLM Prompt"""
        system_prompt = """你是一个Oracle 26ai数据库与Property Graph设计专家，擅长根据本体定义、数据映射和部署设计生成可执行DDL。

请根据以下信息生成完整的DDL脚本，包含：
1. 管理层实体表/视图的 CREATE 语句（含字段、类型、Comments）
2. 多对多关系表或关系边视图的 CREATE 语句（如需要）
3. 已确认需要部署的语义视图层 CREATE OR REPLACE VIEW 语句
4. 已确认需要部署的关系边视图 CREATE OR REPLACE VIEW 语句
5. COMMENT ON TABLE/COLUMN 语句
6. Oracle 26ai Property Graph 创建语句

输出格式要求：
- 每个DDL块以注释说明开始
- 使用Oracle 26ai兼容的语法
- Management Table 使用 CREATE TABLE
- Management View 使用 CREATE OR REPLACE VIEW
- 只为 blueprint 中 deploy = true 的语义视图生成 CREATE OR REPLACE VIEW
- 只为 blueprint 中 deploy = true 的 edge_view 生成 CREATE OR REPLACE VIEW
- 语义视图优先使用 blueprint 中给出的推荐视图名
- 如果已经有关系映射 edge_sql，请优先基于该 SQL 生成边视图和 Property Graph Edge Table
- 不要输出解释文字，只输出 SQL 脚本"""

        # Build context
        entities_info = []
        for e in entities:
            entity_data = {
                "entity_name": e.entity_name,
                "entity_display_name": e.entity_display_name,
                "entity_desc": e.entity_desc,
                "build_type": e.build_type,
                "table_name": e.table_name,
                "entity_mapping": {
                    "build_type": e.entity_mapping.build_type if e.entity_mapping else None,
                    "view_sql": e.entity_mapping.view_sql if e.entity_mapping else None,
                    "mapping_status": e.entity_mapping.mapping_status if e.entity_mapping else None,
                } if e.entity_mapping else None,
                "properties": [
                    {
                        "name": p.property_name,
                        "display_name": p.property_display_name,
                        "data_type": p.data_type,
                        "is_primary_key": p.is_primary_key == "Y",
                        "is_nullable": p.is_nullable == "Y",
                        "desc": p.property_desc,
                        "mapping": {
                            "source_table": p.mapping.source_table if p.mapping else None,
                            "source_column": p.mapping.source_column if p.mapping else None,
                            "mapping_type": p.mapping.mapping_type if p.mapping else None,
                            "formula_expr": p.mapping.formula_expr if p.mapping else None
                        } if p.mapping else None
                    } for p in e.properties
                ]
            }
            entities_info.append(entity_data)

        relations_info = [
            {
                "source_entity": r.source_entity.entity_name,
                "target_entity": r.target_entity.entity_name,
                "relation_name": r.relation_name,
                "relation_type": r.relation_type,
                "relation_desc": r.relation_desc,
                "relation_table_name": r.relation_table_name,
                "relation_mapping": {
                    "source_table": r.relation_mapping.source_table if r.relation_mapping else None,
                    "target_table": r.relation_mapping.target_table if r.relation_mapping else None,
                    "join_condition": r.relation_mapping.join_condition if r.relation_mapping else None,
                    "edge_sql": r.relation_mapping.edge_sql if r.relation_mapping else None,
                    "mapping_status": r.relation_mapping.mapping_status if r.relation_mapping else None,
                } if r.relation_mapping else None,
            } for r in relations if r.source_entity and r.target_entity
        ]

        blueprint_context = blueprint_package or {}
        rule_summary = blueprint_context.get("rule_summary") or {}
        table_roles = blueprint_context.get("table_roles") or blueprint_context.get("source_role_bindings") or []
        entity_candidates = blueprint_context.get("entity_candidates") or []
        relation_candidates = blueprint_context.get("relation_candidates") or []
        deployment_design = blueprint_context.get("deployment_design") or {}
        mapping_design = blueprint_context.get("mapping_design") or {}

        user_prompt = f"""请为以下本体定义和映射结果生成 Oracle 26ai DDL 脚本：

分析域: {domain.domain_name}

最新设计包中的规则摘要:
{json.dumps(rule_summary, ensure_ascii=False, indent=2)}

最新设计包中的表角色识别:
{json.dumps(table_roles, ensure_ascii=False, indent=2)}

最新设计包中的实体候选:
{json.dumps(entity_candidates, ensure_ascii=False, indent=2)}

最新设计包中的关系候选:
{json.dumps(relation_candidates, ensure_ascii=False, indent=2)}

本体实体定义:
{json.dumps(entities_info, ensure_ascii=False, indent=2)}

本体关系定义:
{json.dumps(relations_info, ensure_ascii=False, indent=2)}

最新设计包中的映射设计:
{json.dumps(mapping_design, ensure_ascii=False, indent=2)}

最新设计包中的部署设计:
{json.dumps(deployment_design, ensure_ascii=False, indent=2)}

要求：
- 管理层对象（ONTO_ 前缀）要结合本体定义和属性映射生成。
- 如果某实体已经有 entity_mapping.view_sql，可直接复用或在其基础上修正，优先输出 VIEW。
- 如果关系已经有 relation_mapping.edge_sql，可优先基于该 SQL 生成关系边视图。
- 如果 blueprint 中的某个 semantic_view 标记为 deploy = false，则不要为它生成DDL。
- 如果 blueprint 中的某个 edge_view 标记为 deploy = false，则不要为它生成DDL。
- Property Graph 要基于最终生成的顶点表/视图和边表/边视图创建。
- 返回完整 SQL 脚本。"""

        return await self.call_llm(system_prompt, user_prompt)
