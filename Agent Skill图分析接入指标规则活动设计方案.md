# Agent Skill图分析接入指标规则活动设计方案

## 1. 背景

当前系统在“智能体构建 > 技能构建”中，已经能够基于以下信息构建一个面向 Oracle Property Graph 的 Agent Skill：

- 业务分析域
- Oracle 源数据库
- Property Graph 对象
- 业务流程
- Skill 基本描述、分析目标、执行规则、输出要求

但目前 Skill 的上下文主要还是：

- 图拓扑
- 流程步骤
- 本体对象基础描述

系统中已经存在的三类关键业务语义尚未真正进入 Skill 的分析执行闭环：

- 指标定义（Metric Definition）
- 业务规则（Business Rule）
- 业务活动（Business Activity）

这导致 Agent 在做缺陷分析、根因分析、影响推断时，容易停留在“看图结构、查样例数据、做自然语言解释”的层面，而无法稳定遵循当前系统中已经配置好的业务口径、判定阈值和闭环动作。

本方案目标是：让 Agent 在访问本体对象 Graph 进行分析时，能够显式使用系统中的指标、规则和活动，形成“图探索 + 指标聚合 + 规则判定 + 活动建议”的统一分析能力。

## 2. 当前系统基础

### 2.1 已有模型与配置能力

系统已具备以下业务语义模型：

- `SysMetricDefinition`
  - 指标编码、名称、所属实体
  - 计算表达式 `calculation_expr`
  - 聚合方式 `aggregation_method`
  - 统计周期 `calculation_period`
  - 单位 `unit`
  - 阈值配置 `threshold_config`

- `SysBusinessRule`
  - 规则名称、分类 `rule_category`
  - 触发事件 `trigger_event`
  - 作用范围 `scope_entity_id` / `scope_relation_id`
  - 条件定义 `condition_json`
  - 关联活动 `activity_id`
  - 优先级 `priority`

- `SysBusinessActivity`
  - 活动名称、类型 `activity_type`
  - 描述 `activity_desc`
  - 关联流程 `process_id`
  - 配置 `config_json`

### 2.2 当前 Skill 构建链路

当前 Skill 构建流程主要做了这些事：

1. 选择分析域、流程、源数据库、Property Graph、大模型
2. 生成 Skill 的基础描述与 Prompt
3. 生成 Skill ZIP 包
4. 在 Skill 测试中基于样例表结构和样例数据做一次 LLM 分析

当前不足：

- Skill 上下文未装载指标定义
- Skill 上下文未装载业务规则
- Skill 上下文未装载业务活动
- Skill 测试仍偏“表分析”，不是“图分析 + 业务语义分析”
- Agent 输出无法说明“用了哪些指标、命中了哪些规则、建议了哪些活动”

## 3. 设计目标

本方案实现后，Agent Skill 应具备以下能力：

1. 能根据用户问题识别当前分析意图
   - 缺陷分析
   - 根因分析
   - 影响推断
   - 追溯分析

2. 能装载当前分析域中与 Skill 相关的业务语义
   - 关键指标
   - 判定规则
   - 业务活动

3. 能把业务语义映射到 Property Graph 的节点、边、属性

4. 能按照“流程约束 + 图探索 + 指标计算 + 规则判定”的方式形成分析结论

5. 能输出结构化结果
   - 结论摘要
   - 核心证据
   - 使用指标
   - 命中规则
   - 建议活动
   - 风险与不确定性

## 4. 总体设计

### 4.1 核心思路

在当前 Skill 构建链路中引入一层新的业务语义上下文：

- `Analysis Semantics Pack`

它作为 Skill 的一部分被保存到 `context_json` 中，同时也输出到 Skill 包中的参考文件中，供 Agent 执行和测试时使用。

### 4.2 Analysis Semantics Pack 结构

建议结构如下：

```json
{
  "analysis_profile": {
    "analysis_modes": ["DEFECT_ANALYSIS", "ROOT_CAUSE", "IMPACT_INFERENCE"],
    "entry_objects": ["DefectEvent", "MetricResult", "ProductionBatch"],
    "entry_identifiers": ["defect_id", "batch_no", "equipment_id", "lot_no"],
    "default_time_window": "7D"
  },
  "metric_catalog": [],
  "rule_catalog": [],
  "activity_playbook": [],
  "graph_semantic_map": {
    "metric_bindings": [],
    "rule_bindings": [],
    "activity_bindings": []
  }
}
```

其中：

- `analysis_profile`
  - 描述 Skill 的主要分析类型、分析入口和默认分析窗口
- `metric_catalog`
  - 当前 Skill 启用的指标集合
- `rule_catalog`
  - 当前 Skill 启用的规则集合
- `activity_playbook`
  - 当前 Skill 允许输出的建议动作集合
- `graph_semantic_map`
  - 负责把指标、规则、活动绑定到具体本体对象和图关系

## 5. 业务语义如何进入 Agent 分析

### 5.1 指标的使用方式

指标不只是展示字段说明，而要成为 Agent 的“分析度量层”。

Agent 使用指标时分两类：

- 解释型指标
  - 用于回答“发生了什么”
  - 例如缺陷率、超规率、返修率、批次波动率

- 判定型指标
  - 用于回答“是否异常”
  - 结合阈值配置决定风险等级

#### 5.1.1 缺陷分析中的指标使用

- 按缺陷类型统计数量、占比、趋势
- 按设备、工位、批次、供应商统计缺陷率
- 按测量指标统计超规率
- 按时间窗口统计异常集中度

#### 5.1.2 根因分析中的指标使用

- 候选根因节点的样本覆盖率
- 候选设备/工位的异常富集度
- 候选物料批次与缺陷批次的共现比率
- 上下游节点异常传播强度

#### 5.1.3 影响推断中的指标使用

- 受影响批次数
- 受影响工单数
- 潜在波及产品数
- 下游扩散路径长度
- 高风险客户/库存/订单影响量

### 5.2 规则的使用方式

业务规则不应只在后台作为静态文书存在，而应成为 Agent 推理时的显式约束。

规则建议按四类使用：

- `VALIDATION`
  - 校验分析前提是否成立
  - 例如样本量太少时不能直接判定根因

- `DECISION`
  - 将指标或关系模式转为业务判断
  - 例如连续三批同设备超上限则判定设备高疑似

- `DERIVATION`
  - 生成中间业务标签
  - 例如“疑似异常批次”“高风险设备”

- `ALERT`
  - 定义何时给出高优先级风险结论

#### 5.2.1 规则编译原则

建议将 `condition_json` 统一编译为内部 DSL，再由 Agent 或执行器映射成：

- 图查询过滤条件
- 聚合后判定条件
- 结果分级条件

建议 DSL 结构：

```json
{
  "scope": "ENTITY",
  "scope_entity": "MetricResult",
  "conditions": [
    {"field": "metric_value", "op": ">", "value": 12},
    {"field": "metric_name", "op": "=", "value": "SFR_SCORE"}
  ],
  "logic": "AND",
  "severity": "HIGH"
}
```

### 5.3 活动的使用方式

业务活动不直接参与取数，但决定分析闭环如何落地。

建议 Agent 在分析结论之后，根据命中的规则和置信度输出活动建议。

活动可分为：

- `MANUAL_REVIEW`
  - 证据不足、需人工复核

- `CREATE_TASK`
  - 自动建议创建排查任务

- `CALL_PROCESS`
  - 自动建议进入某个流程

- `NOTIFY`
  - 自动建议通知责任角色

- `DATA_ACTION`
  - 后续可扩展为标签回写或状态更新

第一阶段建议：

- 仅输出“建议活动”
- 不直接自动执行活动

这样可以先完成分析闭环，而不引入执行副作用风险。

## 6. 分析执行链路设计

建议 Skill 执行时采用以下 6 步：

### 6.1 第一步：意图识别

根据用户问题与 Skill 的 `analysis_profile` 识别本次任务类型：

- 缺陷分析
- 根因分析
- 影响推断
- 追溯分析

### 6.2 第二步：语义装载

加载以下上下文：

- Property Graph 实时拓扑
- 流程步骤
- 当前本体对象、关系、关键属性
- 指标定义
- 规则定义
- 活动定义
- 指标/规则/活动与图对象的绑定关系

### 6.3 第三步：生成分析计划

输出一个结构化分析计划，例如：

1. 锁定入口对象
2. 扩展一跳/两跳关联节点
3. 计算关键指标
4. 按业务规则进行异常判断
5. 给出候选根因或影响范围
6. 匹配建议活动

### 6.4 第四步：执行图查询

建议使用模板化只读查询，而不是完全放任 LLM 自由生成 SQL。

建议内置以下模板：

- 节点定位模板
- 邻域扩展模板
- 路径追溯模板
- 分组聚合模板
- 时间窗对比模板
- 上下游扩散模板

查询形式以 Oracle `GRAPH_TABLE` 与只读 `SELECT` 为主。

### 6.5 第五步：规则评估与结论合成

规则评估必须基于取回的指标和证据，而不是纯语言推断。

要求 Agent 输出：

- 结论
- 证据
- 命中指标
- 命中规则
- 置信度
- 限制条件

### 6.6 第六步：活动建议输出

若满足条件，则输出建议活动：

- 活动名称
- 活动类型
- 触发原因
- 关联流程
- 优先级

## 7. 前端改造设计

页面：`智能体构建 > 技能构建`

### 7.1 新增配置区

建议在当前 SkillBuilder 中新增三个配置区：

#### 7.1.1 指标选择区

- 按实体筛选指标
- 按指标分类筛选
- 多选“核心分析指标”
- 可预览：
  - 指标定义
  - 计算表达式
  - 聚合方式
  - 阈值

#### 7.1.2 规则选择区

- 按规则分类筛选
- 按作用域实体/关系筛选
- 选择“判定规则”“解释规则”
- 可预览规则条件 JSON 或可读说明

#### 7.1.3 活动选择区

- 选择允许推荐的业务活动
- 选择是否启用闭环建议
- 预览活动类型、说明和关联流程

### 7.2 新增构建预览内容

当前“构建预览”建议补充：

- 本次启用的核心指标
- 本次启用的关键规则
- 本次允许推荐的活动
- Graph 分析策略摘要

## 8. 后端改造设计

### 8.1 新增聚合读取接口

建议新增：

`GET /agent/domains/{domain_id}/analysis-semantics`

返回：

- 实体列表
- 关系列表
- 指标列表
- 规则列表
- 活动列表
- 流程列表

用于 SkillBuilder 页面一次性加载选择所需语义。

### 8.2 Skill 保存载荷扩展

`AgentSkillCreate` / `AgentSkillUpdate` 建议扩展：

- `analysis_modes`
- `selected_metric_ids`
- `selected_rule_ids`
- `selected_activity_ids`
- `analysis_entry_entity_ids`
- `enable_activity_recommendation`

第一阶段若不想改表结构，可先放入 `context_json`。

### 8.3 Skill 上下文扩展

当前 `AgentService._build_skill_context()` 仅输出：

- `domain`
- `process`
- `entity`
- `property_graph`

建议扩展为：

```json
{
  "domain": {},
  "process": {},
  "entity": {},
  "property_graph": {},
  "analysis_profile": {},
  "metrics": [],
  "rules": [],
  "activities": [],
  "graph_semantic_map": {}
}
```

### 8.4 新增语义组装方法

建议在 `AgentService` 中新增：

- `_load_analysis_semantics(domain_id, payload)`
- `_build_metric_semantic_summary(metrics)`
- `_build_rule_semantic_summary(rules)`
- `_build_activity_playbook(activities)`
- `_build_graph_semantic_map(topology, metrics, rules, activities)`

## 9. Skill 包输出设计

当前 Skill 包已有：

- `SKILL.md`
- `references/property-graph.md`
- `references/analysis-flow.md`

建议新增：

- `references/metric-catalog.json`
- `references/rule-catalog.json`
- `references/activity-playbook.json`
- `references/analysis-strategy.md`

### 9.1 analysis-strategy.md 内容建议

包括：

- 本 Skill 支持的分析类型
- 推荐分析入口
- 指标解释规则
- 规则判定原则
- 活动输出原则
- 证据不足时的保守输出要求

## 10. Prompt 设计要求

Prompt 中必须显式要求 Agent：

1. 先识别分析意图，再决定查询路径
2. 先引用指标定义，再做聚合
3. 先引用规则判定，再下异常结论
4. 证据不足时只能输出“疑似”或“待人工确认”
5. 输出中必须说明：
   - 使用了哪些指标
   - 命中了哪些规则
   - 推荐了哪些活动

建议固定输出结构：

1. 结论摘要
2. 分析路径
3. 关键指标
4. 命中规则
5. 根因/影响候选
6. 建议活动
7. 风险与限制

## 11. 三类典型分析场景

### 11.1 缺陷分析

入口对象：

- `DefectEvent`
- `MetricResult`
- `InspectionResult`

执行方式：

- 先统计缺陷现象、时间分布、工位分布、设备分布
- 计算缺陷率、超规率、重复缺陷率
- 用规则判断是否达到业务定义的异常条件
- 输出缺陷热点、异常站位、建议复核活动

### 11.2 根因分析

入口对象：

- 缺陷批次
- 异常设备
- 超规指标

执行方式：

- 沿图追溯物料、设备、工艺、人员、供应商、前序工站
- 计算富集度、覆盖率、同批次共现率
- 用规则排除低样本或弱证据候选
- 输出高/中/低置信根因链

### 11.3 影响推断

入口对象：

- 异常物料批次
- 异常设备
- 异常规则事件

执行方式：

- 沿下游关系扩散至批次、工单、产品、库存、客户
- 计算受影响对象数、扩散层级、潜在影响量
- 用规则区分直接影响与潜在影响
- 输出建议通知或冻结活动

## 12. 实施优先级

建议分三阶段实施。

### 12.1 第一阶段：让 Skill 能读到业务语义

目标：

- 新增语义聚合接口
- SkillBuilder 支持选择指标、规则、活动
- Skill `context_json` 注入业务语义
- Skill 包新增语义参考文件

特点：

- 改造风险低
- 快速见效
- 不改变现有核心执行器

### 12.2 第二阶段：让测试链路变成“图分析测试”

目标：

- 从当前表级测试升级为 Graph 分析测试
- 加入分析计划、指标聚合、规则判定、活动建议
- 输出标准化分析报告

### 12.3 第三阶段：加入规则 DSL 与模板化执行器

目标：

- 统一规则编译
- 模板化图查询执行
- 控制 LLM SQL 自由度
- 提升结论稳定性和可审计性

## 13. 风险与约束

### 13.1 规则表达不统一

当前 `condition_json` 可能存在格式不统一的问题，落地前应约束规则 JSON 结构。

### 13.2 指标表达式不一定可直接用于图查询

`calculation_expr` 可能是业务描述、SQL 片段或半结构化表达，需要定义一层统一解释规则。

### 13.3 活动不应直接自动执行

第一阶段只输出建议，不直接触发写操作、流程调用或通知，避免副作用。

### 13.4 图查询成本与上下文成本

缺陷根因分析、影响扩散分析容易产生大规模路径搜索，应限制：

- 最大扩散跳数
- 时间窗口
- 返回记录数
- 默认聚焦入口对象

## 14. 推荐落地顺序

建议按以下顺序推进：

1. 新增 `analysis-semantics` 聚合读取接口
2. 改造 SkillBuilder 支持指标/规则/活动选择与预览
3. 扩展 `AgentService._build_skill_context()`
4. 扩展 Skill 包输出语义参考文件
5. 升级 Skill 测试链路为图分析测试
6. 新增规则 DSL 与模板化图查询执行器

## 15. 预期效果

方案落地后，当前系统构建出的 Agent Skill 将从“能访问图并做一般分析”，升级为“能按照业务定义的指标、规则和活动做受控分析”：

- 对缺陷分析，能说明缺陷是如何按业务口径判定出来的
- 对根因分析，能说明候选根因为什么成立、为什么只是疑似
- 对影响推断，能说明影响范围是如何沿图扩散计算出来的
- 对结论输出，能说明使用了哪些指标、命中了哪些规则、建议了哪些活动

这会显著提高 Agent Skill 的：

- 可解释性
- 可复核性
- 可复用性
- 业务一致性
- 后续自动化闭环扩展能力
