# Guide 结构化本体生成改造设计文档

## 1. 文档目的

本文用于指导当前 Guide 自动生成链路从“LLM 主导的实体/关系建议生成”改造为“结构化分析优先、领域模板生成主导、LLM 负责收敛与润色”的实现方案。后续代码修改以本文为准。

适用场景以当前高伟 TAMS SFR 根因分析 POC 为起点，但方案设计应支持后续扩展到其他业务分析域。

## 2. 背景与问题

当前系统中的 Guide 生成链路已经具备：

- 业务文档解析
- DDL / 数据库两种表来源
- 规则数据解析
- LLM 生成设计文档、实体候选、关系候选、部署建议
- blueprint 落库
- 可选应用到本体元数据

但对于本次目标场景，这种实现存在三个核心问题：

### 2.1 核心业务知识没有被结构化沉淀

当前系统能识别 `SPEC_LIMIT` 类规则表，但只能输出轻量级 `rule_summary`。对于以下高价值结论没有结构化承载：

- 业务场景边界
- 首期纳入的 metric family
- 硬判定指标与扩展证据指标
- 关键过程站位
- 测试宽表与过程宽表的拆解策略
- 缺陷语义分类

### 2.2 实体与关系仍主要依赖 LLM 自由发挥

在当前实现中，`LLMService.generate_entity_candidates()` 和 `generate_relation_candidates()` 仍承担“决定做哪些对象、哪些关系”的核心职责。这与本次 POC 需要的稳定建模不一致。

本次场景中，对象与关系骨架已经明确，应优先由程序根据领域模板稳定产出，而不是反复让 LLM 猜测。

### 2.3 标准化视图设计没有成为一等输出

当前 `deployment_design` 主要是建议性结构，尚未成为后续映射和 DDL 的稳定输入。但本次方案已经明确需要三层结构：

- 原始层
- 标准化层
- 图层

因此标准化视图清单必须从“建议”升级为“正式设计产物”。

## 3. 改造目标

改造后，Guide 生成链路应满足以下目标：

1. 先基于输入文档、DDL、规则数据做确定性结构化分析。
2. 再根据业务场景模板直接生成 canonical ontology 骨架。
3. 再由 LLM 对 canonical 结果做补充描述、范围说明和边 SQL 草案。
4. 将标准化视图计划作为 blueprint 的正式组成部分。
5. 前端 Guide 改为分步式确认，不再只是“一次提交、一次预览”。
6. 后续 DDL 与映射阶段优先消费标准化视图计划，而不是重新自由推导。

## 4. 总体方案

## 4.1 改造后的生成策略

Guide 新增两种生成策略：

- `llm_first`
  - 保留当前实现，兼容旧逻辑
- `structured_domain_pipeline`
  - 新默认策略
  - 先结构化分析，再模板生成，再 LLM 收敛

建议本次改造完成后，默认使用 `structured_domain_pipeline`。

## 4.2 改造后的执行链

新的 Guide 主流程定义为：

1. 输入资料整理
2. 结构化分析
3. 范围确认
4. canonical 本体生成
5. 标准化视图计划生成
6. LLM 补充与润色
7. blueprint 落库
8. 选择性应用到本体元数据

与当前实现相比，最大的变化是：

- LLM 不再负责“主导决定对象和关系”
- 服务端逻辑直接产出核心对象骨架和标准化视图计划

## 5. 目标业务场景下的建模原则

本次 TAMS SFR 场景中的核心建模原则如下：

### 5.1 对象骨架必须稳定

以下对象不再交由 LLM 自由决定，应由领域生成器稳定输出：

- `ProductUnit`
- `ProductModel`
- `TestRun`
- `MetricResult`
- `MetricSpec`
- `DefectType`
- `ProcessEvent`
- `Station`
- `Equipment`
- `ToolingCarrier`
- `MaterialLot`
- `AlarmEvent`
- `AALogFeature`
- `HistoricalCase`
- `RootCausePattern`
- `CorrectiveAction`
- `ImpactScope`

### 5.2 关系骨架必须稳定

以下关系不再交由 LLM 自由决定，应由领域生成器稳定输出：

- `属于`
- `有测试`
- `产生`
- `对照`
- `指向`
- `经过`
- `运行于`
- `使用`
- `消耗`
- `相似于`
- `支持`
- `解决`
- `影响`

### 5.3 视图骨架必须稳定

以下标准化视图应成为当前场景的正式设计产物：

- `V_UNIT_BASE`
- `V_PROCESS_EVENT`
- `V_TEST_RUN`
- `V_METRIC_RESULT`
- `V_METRIC_SPEC`
- `V_METRIC_OOS`
- `V_ALARM_EVENT`
- `V_RECIPE_FEATURE`
- `V_AA_FEATURE`
- `V_HISTORY_CASE`
- `V_ROOT_CAUSE_PATTERN`

## 6. 后端设计

## 6.1 请求模型改造

文件：

- `backend/app/schemas/schemas.py`

建议为 `OntologyGuideGenerateRequest` 增加以下字段：

- `generation_strategy: Optional[str] = "structured_domain_pipeline"`
- `business_scenario: Optional[str] = None`
- `focus_metric_families: List[str] = []`
- `focus_stations: List[str] = []`
- `history_case_sources: List[str] = []`

字段用途：

- `generation_strategy`
  - 切换旧版 LLM 主导流程和新版结构化流程
- `business_scenario`
  - 指定场景模板，例如 `SFR_ROOTCAUSE`
- `focus_metric_families`
  - 明确首期 family 范围
- `focus_stations`
  - 明确首期过程站位范围
- `history_case_sources`
  - 指定历史案例输入来源

## 6.2 Guide Service 主流程改造

文件：

- `backend/app/services/ontology_guide_service.py`

### 6.2.1 保留当前 `generate()` 作为总入口

但改为内部按策略分派：

- `_generate_with_llm_first()`
- `_generate_with_structured_domain_pipeline()`

### 6.2.2 新增结构化分析阶段

建议新增以下方法：

- `_extract_document_facts()`
- `_analyze_spec_limit_rule_data()`
- `_analyze_source_schema_keywords()`
- `_classify_table_archetypes()`
- `_extract_focus_stations_from_process_table()`
- `_derive_metric_semantics()`
- `_build_focus_scope()`

每个方法职责如下。

`_extract_document_facts()`

- 从问卷正文提取：
  - POC 场景名
  - 分析目标
  - 追溯链路
  - 关键对象
  - 关键站位
  - 历史知识来源

`_analyze_spec_limit_rule_data()`

- 解析 `SPEC_LIMIT` 类规则集
- 产出：
  - family 统计
  - metric 清单
  - 上下限覆盖率
  - OOS 判定逻辑
  - 首期纳入 family
  - 扩展证据 family

`_analyze_source_schema_keywords()`

- 从 DDL 或远端表结构中识别：
  - 产品主索引表
  - SFR 测试宽表
  - AA 表
  - Alarm 表
  - Process 宽表

`_classify_table_archetypes()`

- 将表归类为：
  - `unit_index`
  - `process_wide_event`
  - `test_wide_result`
  - `rule_spec`
  - `alarm_event_source`
  - `aa_feature_source`
  - `history_case_source`

`_extract_focus_stations_from_process_table()`

- 从 `PDX25_TAMS_PROCESS` 这类宽表识别重点站位
- 当前至少覆盖：
  - `LBI`
  - `LBI_OVEN`
  - `AA_INLINE_AA`
  - `AA_OVEN`
  - `CUBE_FTU`
  - `CUBE_FTD`

`_derive_metric_semantics()`

- 将测项命名模式转为缺陷语义分类，例如：
  - 中心解析力偏低
  - 边缘解析力偏低
  - 左右边缘不对称
  - 上下边缘不对称
  - 高频/角度解析力不足
  - 焦点补偿异常

### 6.2.3 结构化分析结果输出

建议将以下结构加入 blueprint payload：

- `document_facts`
- `rule_analysis`
- `schema_analysis`
- `source_table_roles`
- `focus_scope`
- `metric_semantics`

这些字段是后续 canonical 生成与前端确认页的数据来源。

## 6.3 领域模板生成器

建议新增目录：

- `backend/app/services/domain_ontology_generators/`

首个生成器文件：

- `backend/app/services/domain_ontology_generators/tams_sfr_generator.py`

建议新增统一接口：

- `build_canonical_model(analysis_context: Dict[str, Any]) -> Dict[str, Any]`
- `build_view_plan(analysis_context: Dict[str, Any], canonical_model: Dict[str, Any]) -> Dict[str, Any]`

### 6.3.1 canonical model 输出

输出中至少包含：

- `entities`
- `relations`
- `entity_groups`
- `relation_groups`
- `mapping_hints`

### 6.3.2 view plan 输出

输出中至少包含：

- `raw_layer`
- `standardized_views`
- `graph_layer`
- `view_dependencies`

## 6.4 LLM 职责收缩

文件：

- `backend/app/services/llm_service.py`

当前 LLM 方法不应被直接删除，但职责要收缩。

### 6.4.1 `generate_ontology_design_document()`

从“决定首期对象范围”改成：

- 对结构化分析结论生成自然语言说明
- 解释为什么当前 scope 被收敛到这些 family / 站位 / 对象
- 输出 deferred scope

### 6.4.2 `generate_entity_candidates()`

从“自由生成实体”改成：

- 对 canonical entities 补充：
  - 中文显示名
  - 对象描述
  - 属性补充说明
  - 属性映射备注

### 6.4.3 `generate_relation_candidates()`

从“自由生成关系”改成：

- 对 canonical relations 补充：
  - 中文关系说明
  - evidence tables
  - `joinCondition`
  - `edgeSql` 草案

### 6.4.4 `generate_semantic_deployment_design()`

从“自由生成部署设计”改成：

- 对已存在的 standardized views 做 SQL 草案补充
- 对 edge views 做 SQL 草案补充
- 不再决定核心 view 名称和层次结构

## 6.5 blueprint 结构改造

文件：

- `backend/app/services/ontology_guide_service.py`
- `backend/app/models/models.py`（可选，不强制）

当前阶段不强制新增数据库表结构，但 blueprint JSON 必须扩展为：

- `generation_strategy`
- `business_scenario`
- `document_facts`
- `rule_analysis`
- `schema_analysis`
- `focus_scope`
- `metric_semantics`
- `canonical_model`
- `view_plan`
- `llm_enrichment`

说明：

- 先扩 blueprint JSON，避免首轮改造就引入迁表风险
- 等结构稳定后，再考虑把部分字段拆成正式表

## 6.6 apply_blueprint 行为调整

文件：

- `backend/app/services/ontology_guide_service.py`

当前 `apply_blueprint()` 只消费 `entities` 和 `relations`。改造后建议：

1. 继续把 canonical entities / relations 落到当前本体元数据表。
2. 新增对 `mapping_hints` 的 seed 写入能力。
3. 将 `view_plan` 保留在 blueprint 中，先不直接落物理视图。
4. 对 `entity_category`、`relation_category` 等新增语义先放在描述或扩展 JSON 中，待后续是否迁表再决定。

## 6.7 DDL 生成消费 view_plan

文件：

- `backend/app/services/ddl_service.py`

当前 DDL 生成主要消费：

- entities
- relations
- blueprint deployment_design

改造后应新增优先级：

1. `view_plan.standardized_views`
2. `canonical_model`
3. 旧 `deployment_design`

也就是说，DDL 阶段应优先把 `V_UNIT_BASE`、`V_PROCESS_EVENT` 等标准化视图作为正式输入，而不是再让 LLM 推断一次。

## 7. 前端设计

文件：

- `frontend/src/views/ontology/OntologyBuild.vue`

## 7.1 Guide UI 从单页改为分步式

当前是一个大对话框直接提交。建议改成四步：

### Step 1 资料输入

- 上传业务问卷
- 上传 DDL
- 上传规则数据
- 选择 source / schema / model

### Step 2 分析结果确认

展示：

- 场景摘要
- 历史知识来源
- 关键链路
- 首期 metric family
- 关键主表 / 测试表 / 过程表
- 关键站位

用户可以在此确认或调整：

- 首期 family
- 首期站位
- 历史案例来源

### Step 3 本体生成预览

展示：

- canonical entities
- canonical relations
- 缺陷语义分类
- 根因知识层
- 影响范围层

### Step 4 标准化视图与应用

展示：

- 标准化视图清单
- 视图用途
- 依赖源表
- 推荐部署顺序
- 应用到元数据按钮

## 7.2 前端状态结构扩展

当前 `guidePreview` 主要关心：

- `entities`
- `relations`
- `rule_summary`
- `ontology_design_document`

改造后需要新增读取和渲染：

- `document_facts`
- `rule_analysis`
- `schema_analysis`
- `focus_scope`
- `metric_semantics`
- `canonical_model`
- `view_plan`

## 7.3 前端交互原则

- Step 2 是人为确认点，不能直接跳过
- Step 3 和 Step 4 只消费 Step 2 确认后的范围
- 允许用户回退修改 family / station，再重新生成 canonical model

## 8. 数据映射与标准化层改造

文件：

- `frontend/src/views/mapping/DataMappingOperation.vue`
- `backend/app/api/mapping.py`

当前映射页是按 ontology entity 统一设计。改造后应新增“标准化层视角”。

建议新增两类工作模式：

- `ENTITY_MAPPING`
  - 维持当前对象级映射确认
- `VIEW_PLAN_MAPPING`
  - 新增标准化视图映射确认

在 `VIEW_PLAN_MAPPING` 模式中，优先确认：

- `V_UNIT_BASE`
- `V_PROCESS_EVENT`
- `V_TEST_RUN`
- `V_METRIC_RESULT`
- `V_METRIC_SPEC`
- `V_METRIC_OOS`
- `V_ALARM_EVENT`
- `V_AA_FEATURE`

目标是把“宽表如何拆”为系统显式设计，而不是隐藏在 entity mapping 的公式中。

## 9. 推荐实施步骤

下面是建议的落地顺序。后续代码修改按此顺序推进。

### 阶段 1：后端输入与 blueprint 扩展

目标：

- 不改变旧链路可用性
- 为新版结构化流程加入口和结果容器

修改步骤：

1. 修改 `OntologyGuideGenerateRequest`
2. 为 `generate()` 增加策略分派
3. 扩 blueprint payload 结构
4. 保证旧 `llm_first` 逻辑仍可跑通

完成标准：

- Guide 接口支持 `generation_strategy`
- blueprint 能落额外分析字段

### 阶段 2：结构化分析阶段

目标：

- 将文档、DDL、规则文件转为确定性分析结果

修改步骤：

1. 新增 document facts 提取
2. 新增 `SPEC_LIMIT` family 统计与 OOS 规则分析
3. 新增 schema archetype 识别
4. 新增 focus stations 提取
5. 新增 metric semantics 分类

完成标准：

- API 返回中可直接看到 `document_facts / rule_analysis / schema_analysis / focus_scope`

### 阶段 3：领域模板生成器

目标：

- 将当前 TAMS SFR 场景的 canonical model 稳定化

修改步骤：

1. 新建 `domain_ontology_generators` 目录
2. 实现 `tams_sfr_generator.py`
3. 在 structured pipeline 中接入 canonical model 生成
4. 用 canonical model 替代自由实体/关系生成主出口

完成标准：

- 不依赖 LLM 也能稳定生成实体、关系、视图清单骨架

### 阶段 4：LLM 收缩为 enrich 层

目标：

- 保留 LLM 能力，但不让它控制核心骨架

修改步骤：

1. 调整 `generate_ontology_design_document()` 的 prompt
2. 调整 entity / relation prompt 输入为 canonical model
3. 调整 deployment prompt 输入为 view plan
4. 将 LLM 输出仅作为 enrichment 合并

完成标准：

- 核心骨架来自服务端模板
- LLM 只补说明、证据、SQL 草案

### 阶段 5：前端 Guide 分步化

目标：

- 让用户能确认分析结果，而不是只能看最终实体/关系

修改步骤：

1. Guide 对话框拆成四步
2. 增加分析结果确认页
3. 增加 canonical model 预览页
4. 增加 view plan 预览页

完成标准：

- 用户可以显式确认首期 family / station / 主表

### 阶段 6：映射与 DDL 消费 view plan

目标：

- 让标准化视图计划真正进入后续落地链路

修改步骤：

1. DDLService 优先消费 `view_plan`
2. 映射页增加标准化视图模式
3. 边 SQL 设计围绕标准化层输出

完成标准：

- `V_UNIT_BASE` 等视图进入正式生成和确认流程

## 10. 风险与注意事项

### 10.1 不建议首轮就改数据库主元数据表结构

原因：

- 当前系统已有本体构建、映射、DDL 生成联动
- 贸然迁表会扩大风险面

建议：

- 第一轮先把新增结构放到 blueprint JSON
- 待结构稳定后再考虑模型化

### 10.2 不建议删除旧 LLM-first 流程

原因：

- 其他业务域可能仍依赖泛化生成能力

建议：

- 保留 `llm_first`
- 新增 `structured_domain_pipeline`
- 等新版稳定后再评估是否废弃旧策略

### 10.3 不建议把全部 SQL 草案一次性硬编码

原因：

- 首轮目标是把视图层和对象层稳定下来
- SQL 细节仍需结合实际源表字段逐步确认

建议：

- 第一轮先固化视图骨架和映射槽位
- 第二轮再补每个视图 SQL 生成器

## 11. 建议新增/重点修改文件清单

后端：

- `backend/app/schemas/schemas.py`
- `backend/app/services/ontology_guide_service.py`
- `backend/app/services/llm_service.py`
- `backend/app/services/ddl_service.py`
- `backend/app/services/domain_ontology_generators/__init__.py`
- `backend/app/services/domain_ontology_generators/tams_sfr_generator.py`

前端：

- `frontend/src/views/ontology/OntologyBuild.vue`
- `frontend/src/views/mapping/DataMappingOperation.vue`
- `frontend/src/api/index.ts`

文档：

- `docs/guide-ontology-generation-flow.md`
- `docs/guide-structured-ontology-refactor-design.md`

## 12. 结论

本次改造的关键不是“再调一次 prompt”，而是把 Guide 的定位改掉：

- 从“LLM 猜本体”
- 改为“结构化分析驱动 + 领域模板生成 + LLM enrich”

只有这样，当前 app 才能稳定承接你列出的那套执行步骤，并把：

- 业务问卷理解
- `SPEC_LIMIT` 判定逻辑
- TAMS 宽表拆解策略
- canonical ontology
- 标准化视图设计

纳入一个可重复、可维护、可继续迭代的实现链路。
