# Guide 自动生成业务实体与关系执行过程

本文整理当前代码中 Guide 自动生成业务实体与关系的实际执行链路，覆盖前端入口、后端主流程、LLM 调用顺序、蓝图落库与应用行为。本文依据当前实现整理，不描述历史方案。

## 1. 入口与参与文件

- 前端入口：`frontend/src/views/ontology/OntologyBuild.vue`
- 生成接口：`POST /api/v1/ontology/domains/{domain_id}/guide/generate`
- 应用接口：`POST /api/v1/ontology/domains/{domain_id}/guide/apply`
- 接口定义：`backend/app/api/ontology.py`
- 请求模型：`backend/app/schemas/schemas.py`
- 核心服务：`backend/app/services/ontology_guide_service.py`
- LLM 编排：`backend/app/services/llm_service.py`

前端弹窗标题为“Guide 自动生成业务实体与关系”。用户可以先上传业务说明文档、DDL 文件、规则数据文件，再执行生成预览或生成并应用。

## 2. 请求输入

`OntologyGuideGenerateRequest` 当前支持两种表来源模式：

- `table_source_mode=database`
  - 必填 `source_id`
  - 可选 `schema`
  - 业务关系表来自远端数据库元数据
- `table_source_mode=ddl`
  - 必须先上传并解析 DDL 文件
  - 业务关系表来自 DDL 解析结果

核心输入字段如下：

- `relation_tables`：本次参与分析的业务关系表
- `table_bindings`：表与 `source_role` 的绑定
- `rule_table_name`：数据库模式下可额外指定规则表
- `rule_datasets`：上传规则数据后解析出的结构化规则集
- `enabled_patterns`：启用的语义模式
- `business_document`：业务说明文档正文
- `model_config_id`：指定模型配置
- `sample_limit`：数据库模式下拉取样例行数
- `auto_apply`：生成后是否直接应用
- `overwrite_existing`：应用时是否覆盖已存在元数据

前端在调用前会做基础校验：必须先选分析域、表来源、至少一张关系表，并填写业务说明文档。

## 3. 生成主流程

`generate_ontology_from_guide()` 仅做参数接收与异常包装，真正逻辑全部在 `OntologyGuideService.generate()`。

执行顺序如下：

1. 校验分析域与输入参数。
2. 标准化 `relation_tables` 与 `table_bindings`。
3. 根据业务文档优先级重排已选表。
4. 按表来源加载表结构详情。
5. 解析业务文档摘要。
6. 识别或加载规则数据，生成规则摘要。
7. 推断表角色与语义模式。
8. 生成高层本体设计文档。
9. 分块生成实体候选并合并去重。
10. 按设计文档范围过滤实体候选。
11. 基于实体候选生成关系候选。
12. 按设计文档范围过滤关系候选。
13. 组装蓝图、映射建议和部署设计。
14. 可选：直接应用蓝图到本体元数据。
15. 无论是否应用，都会把结果保存为一份 Guide blueprint。

## 4. 表结构与规则数据准备

### 4.1 表结构来源

数据库模式下，服务会通过 `SourceDataService.get_remote_table_detail()` 读取：

- 表名
- owner
- 表注释
- 列定义
- 样例数据

DDL 模式下，服务会先通过 `parse_uploaded_ddl()` 把 `CREATE TABLE`、表注释、列注释解析成内存结构，再从中取出选中的表。

### 4.2 规则数据来源

规则数据有两条路径：

- 用户单独上传规则数据文件，经 `parse_uploaded_rule_data()` 解析
- 数据库模式下，如果显式指定规则表，或所选表名命中 `SPEC / LIMIT / RULE / THRESHOLD`，服务会自动尝试补读规则表样例数据

当前内置规则识别重点是 `SPEC_LIMIT` 类数据。服务会把规则表结构或具体阈值记录整理为 `rule_summary`，供后续 LLM 约束范围。

## 5. 表角色与语义模式

服务会为每张表确定 `source_role`。来源优先级如下：

1. 前端显式绑定
2. 规则表识别结果
3. 基于表名与字段名的启发式推断

当前角色包括：

- `entity_master`
- `process_history`
- `measurement`
- `rule_catalog`
- `case_library`
- `event_log`
- `reference_data`
- `other`

随后系统结合角色集和前端勾选项，生成 `semantic_patterns`。当前内置模式有：

- `master-data-linking`
- `process-trace`
- `measurement-threshold-violation`
- `case-rootcause-action`

这些模式既参与提示词约束，也会影响后续推荐的派生实体与部署设计草案。

## 6. LLM 调用链

当前 Guide 不是一次性让模型直接产出最终本体，而是拆成 4 个阶段：

### 6.1 高层本体设计文档

调用 `LLMService.generate_ontology_design_document()`，产出：

- `mvp_scope`
- `scope_reasoning`
- `included_entities`
- `included_relations`
- `excluded_or_deferred`
- `implementation_notes`

这一步的作用是先确定首期最小可行范围，后续实体和关系都要尽量收敛到这里。

### 6.2 实体候选

调用 `LLMService.generate_entity_candidates()`。输入包含：

- 业务摘要
- 高层设计文档
- 规则摘要
- 当前批次表结构
- 表角色
- 语义模式

输出为实体候选和属性候选，属性里尽量带上源表、源字段、映射类型等信息。

### 6.3 关系候选

调用 `LLMService.generate_relation_candidates()`。输入包含：

- 高层设计文档
- 实体候选
- 规则摘要
- 表角色
- 关系表结构
- 语义模式

输出关系候选，并尽量带上：

- `evidenceTables`
- `sourceTable`
- `targetTable`
- `joinCondition`
- `edgeSql`

### 6.4 语义部署设计

调用 `LLMService.generate_semantic_deployment_design()`，生成：

- `semantic_views`
- `edge_views`
- `property_graph`

这部分是部署建议，不等于当前阶段已经落库执行。

## 7. 大上下文压缩与分块策略

Guide 当前实现做了两层上下文控制。

### 7.1 设计文档与关系生成使用压缩表结构

系统会对列数较多的表做裁剪：

- 总列预算：`GUIDE_LLM_MAX_TOTAL_COLUMNS = 180`
- 单表最少列：12
- 单表最多列：28
- 每表最多 1 行样例数据
- 每行最多 12 个样例字段

列会按主键、标识列、规则字段、测量字段、时间字段等规则打分，优先保留高价值字段。

### 7.2 实体候选生成使用全列分块

实体候选阶段尽量保留更多字段，但会按预算分块：

- 单 chunk 最多 4 张表
- 单 chunk 最多 240 列
- 若模型运行时仍报上下文超限，会递归拆 chunk
- 如果单表仍超限，会把单表按字段区段继续拆段

分块结果最终通过 `_merge_entity_candidate_results()` 合并去重。

## 8. 结果过滤与蓝图组装

实体候选生成后，系统会按高层设计文档里的 `included_entities` 做一次过滤。

关系候选生成后，会做两层过滤：

1. 源实体和目标实体必须都在当前实体候选中
2. 如果设计文档显式给了 `included_relations`，关系名必须在允许范围内

随后 `_build_ontology_design()` 组装最终蓝图：

- `entities`
- `relations`

这里会进一步处理关系名称：

- 优先保留中文短谓词
- 若关系名是英文，会尝试从 `relationDesc` 中提取中文谓词
- 实在无法提取时，回退为“关联”

## 9. 映射建议与部署建议

生成完成后，服务还会附带两份非落库结果：

- `mapping_design`
- `deployment_design`

`mapping_design` 会给出：

- 表角色
- 每个实体的来源表提示
- 推荐构建方式
- 每条关系的证据表与待映射状态

`deployment_design` 会给出：

- 候选语义视图
- 候选边视图
- Property Graph 草案

这两部分主要服务于后续“数据映射管理”和“DDL 生成与应用”。

## 10. Blueprint 落库

Guide 生成结束后，不管是否 `auto_apply`，系统都会把完整结果写入 `SysOntologyBlueprint`：

- `blueprint_json`：完整生成包
- `summary_json`：摘要
- `status`：`GENERATED` 或 `APPLIED`
- `version_no`：同一分析域内按版本递增

这就是前端“最新 Guide 预览”的来源。

## 11. 应用蓝图到本体元数据

`auto_apply=true` 时，或前端点击“应用当前预览”时，会调用 `apply_blueprint()`。

当前应用行为是“写平台元数据”，不是直接建 Oracle 物理对象。主要动作如下：

### 11.1 实体

- 按 `entityName` 匹配当前分析域已有实体
- 不存在则新建 `SysOntologyEntity`
- 存在且 `overwrite_existing=true` 时更新显示名、描述、构建方式
- 默认节点对象名统一写为：
  - 表：`ONTO_NODE_<ENTITY>`
  - 视图：`ONTO_NODE_<ENTITY>_V`

### 11.2 属性

- 对每个实体最多应用前 30 个属性
- 不存在则新建 `SysOntologyProperty`
- 已存在且允许覆盖时更新描述、类型、主键、可空性

### 11.3 关系

- 以 `source_entity_id + target_entity_id + relation_name` 判重
- 不存在则新建 `SysOntologyRelation`
- 仅 `MANY_TO_MANY` 会默认生成 `relation_table_name = ONTO_REL_<SOURCE>_<TARGET>`
- 其他关系此阶段不会自动补齐英文边表名

### 11.4 映射 seed

应用蓝图时还会顺带写入映射种子：

- `SysEntityMapping`
- `SysPropertyMapping`
- `SysRelationMapping`

这些 seed 的状态一般是 `PENDING` 或 `SUGGESTED`，用于后续人工确认，不代表映射已经最终落地。

## 12. 当前实现边界

需要特别注意以下几点：

- Guide 生成的是“本体设计预览 + 映射建议 + 部署建议”，不是最终 DDL。
- `apply_blueprint()` 只写平台元数据，不直接创建 Oracle 节点表、边表、视图或 Property Graph。
- 关系英文边表名不是 Guide 阶段自动确定的主出口，通常需要在后续关系编辑或数据映射阶段补齐。
- 规则数据当前重点支持 `SPEC_LIMIT` 类阈值规则，其他规则类型尚未形成通用解析框架。
- 实体候选支持 fallback；关系候选失败时当前会回退为空列表。

## 13. 建议的阅读顺序

如果后续要继续改 Guide，建议按下面顺序读代码：

1. `backend/app/api/ontology.py`
2. `backend/app/services/ontology_guide_service.py`
3. `backend/app/services/llm_service.py`
4. `frontend/src/views/ontology/OntologyBuild.vue`
5. `frontend/src/views/mapping/DataMappingOperation.vue`

这样可以先看请求入口，再看编排主链路，最后看预览结果怎样流向映射与 DDL 阶段。
