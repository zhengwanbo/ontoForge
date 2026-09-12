# 本体、数据映射与 DDL 多版本支撑设计方案

## 1. 背景

当前 ontoForge 在“业务分析域”维度已经支持：

- Guide 自动生成本体设计
- 本体关系构建
- 数据映射管理
- DDL 生成与执行
- Oracle Property Graph 浏览与图查询

但真正落库的业务元数据仍然是“每个业务域只有一份当前态”：

- `SYS_ONTOLOGY_ENTITY`
- `SYS_ONTOLOGY_PROPERTY`
- `SYS_ONTOLOGY_RELATION`
- `SYS_ENTITY_MAPPING`
- `SYS_PROPERTY_MAPPING`
- `SYS_RELATION_MAPPING`
- `SYS_DDL_LOG`

这会带来几个明显问题：

1. 同一个业务分析域无法并行维护多个本体实现版本。
2. 新版本试验会直接覆盖旧版本元数据，缺少稳定回退能力。
3. 数据映射和 DDL 与本体版本没有强绑定，无法明确“这份映射/DDL 属于哪个版本”。
4. Oracle 落地时只能有一套当前 graph 对象，无法让多个版本并存验证。
5. 图浏览、Agent Skill、图查询建议等下游能力缺少版本上下文，容易混用不同版本对象。

本设计目标是在不推翻现有架构的前提下，为本项目补齐“同一业务域多版本共存”的完整支撑能力。

## 2. 现状判断

### 2.1 已有版本能力

当前系统里已经存在一个“弱版本”概念：

- `SYS_ONTOLOGY_BLUEPRINT.version_no`

它主要服务于 Guide 设计预览和逻辑蓝图沉淀，特点是：

- 按业务域递增
- 可保存 `blueprint_json`
- 可在“应用当前预览”时写入本体元数据

但它不是完整的运行版本，因为：

- 应用 blueprint 后，实体/关系/映射仍写入当前态表
- blueprint 与后续映射确认、DDL 生成、DDL 执行没有强制一一绑定
- 没有真正意义上的“版本冻结”“版本发布”“版本部署记录”

### 2.2 当前单版本约束点

按现有代码与模型，主要约束点如下：

1. `SYS_ONTOLOGY_ENTITY / PROPERTY / RELATION` 仅按 `domain_id` 隔离，没有 `version_id`
2. `SYS_ENTITY_MAPPING / PROPERTY_MAPPING / RELATION_MAPPING` 通过对象 ID 关联当前实体/属性/关系，也没有版本容器
3. DDL 页默认读取“当前域最新 blueprint + 当前域当前实体/映射”
4. DDL 执行日志 `SYS_DDL_LOG` 只记录 `domain_id`，不能精确归属到某个版本
5. 部署后的 Oracle 节点/边/graph 对象默认按固定名称生成，如：
   - `ONTO_NODE_<ENTITY>`
   - `ONTO_EDGE_<RELATION>`
   - `<GRAPH_NAME>`
6. 图浏览、图查询、Agent Skill 默认面向一个域下的一套图对象

结论：当前系统只能支撑“一个业务域，一套当前本体实现”。

## 3. 设计目标

### 3.1 核心目标

系统需要支持：

1. 同一业务域下存在多个本体实现版本。
2. 每个版本拥有自己的：
   - 本体实体/属性/关系
   - 数据映射
   - DDL 生成结果
   - DDL 执行记录
   - 部署 graph 对象
3. 一个版本从 Guide 设计、映射确认、DDL 生成到 DDL 落地形成完整闭环。
4. 不同版本可并行存在：
   - 一个版本继续编辑
   - 一个版本已冻结待部署
   - 一个版本已部署供浏览/分析使用
5. 前端所有核心页面都能明确“当前正在操作哪个版本”。

### 3.2 非目标

本阶段不追求：

1. 多版本自动合并
2. Git 风格逐字段 patch 级 diff 存储
3. 同一版本内的多人实时协同编辑
4. 跨域版本继承

## 4. 设计原则

1. 业务分析域仍然是顶层业务边界，版本是域下的实现分支。
2. 版本必须成为实体、映射、DDL、部署、浏览、Skill 的一等上下文。
3. 新增版本后，任何页面默认都不能再仅按 `domain_id` 取“当前态”。
4. 已发布/已部署版本必须可冻结，避免后续编辑污染历史。
5. Oracle 物理对象命名必须稳定可追溯，且能与版本一一对应。

## 5. 推荐总体方案

推荐采用：

- `业务域 (Domain)` 作为业务边界
- `实现版本 (Implementation Version)` 作为运行主线
- `Guide Blueprint` 继续作为设计输入/设计快照

也就是：

```text
Domain
  ├─ Blueprint v1/v2/v3 ...   （设计预览、逻辑方案）
  ├─ Ontology Version A       （真正可编辑/可映射/可部署的实现版本）
  ├─ Ontology Version B
  └─ Ontology Version C
```

其中：

- Blueprint 解决“设计草案版本”
- Ontology Version 解决“运行实现版本”

两者不能再混为一谈。

## 6. 版本核心模型

### 6.1 新增主表：`SYS_ONTOLOGY_VERSION`

建议新增：

| 字段 | 说明 |
| --- | --- |
| `version_id` | 主键 |
| `domain_id` | 所属业务域 |
| `version_no` | 域内递增整数版本号 |
| `version_code` | 展示编号，如 `V1` / `V2026.09.12.1` |
| `version_name` | 版本名称，如“金鱼轮胎质量追溯-工序本体V2” |
| `base_version_id` | 克隆来源版本，可空 |
| `source_blueprint_id` | 来源 blueprint，可空 |
| `source_blueprint_version` | 来源 blueprint version，可空 |
| `lifecycle_status` | `DRAFT / MAPPING / READY_FOR_DDL / DEPLOYED / ARCHIVED` |
| `edit_status` | `EDITABLE / FROZEN` |
| `is_default_edit_version` | 当前域默认编辑版本 |
| `is_default_runtime_version` | 当前域默认运行版本 |
| `graph_name` | 逻辑图名，如 `tire_process_graph` |
| `graph_object_name` | 实际部署 graph 对象名，如 `TIRE_PROCESS_GRAPH_V003` |
| `target_source_id` | 默认部署目标数据源 |
| `target_schema_name` | 默认部署目标 schema |
| `summary_json` | 版本摘要 |
| `created_by / created_at / updated_at` | 审计字段 |

### 6.2 版本与当前核心表的关系

推荐给以下表新增 `version_id`：

- `SYS_ONTOLOGY_ENTITY`
- `SYS_ONTOLOGY_PROPERTY`
- `SYS_ONTOLOGY_RELATION`
- `SYS_ENTITY_MAPPING`
- `SYS_PROPERTY_MAPPING`
- `SYS_RELATION_MAPPING`
- `SYS_PROCESS_DEF`（建议）
- `SYS_METRIC_DEFINITION`（建议）
- `SYS_BUSINESS_RULE`（建议）
- `SYS_BUSINESS_ACTIVITY`（建议）
- `SYS_AGENT_SKILL`（建议）
- `SYS_DDL_LOG`

说明：

- 第一期必须覆盖本体、映射、DDL
- 第二期再扩规则、活动、流程、Skill

### 6.3 版本内对象标识

仅增加 `version_id` 还不够，还需要保留“同一业务概念跨版本的稳定身份”。

建议增加以下 lineage 字段：

#### `SYS_ONTOLOGY_ENTITY`

- `entity_lineage_id`

#### `SYS_ONTOLOGY_PROPERTY`

- `property_lineage_id`

#### `SYS_ONTOLOGY_RELATION`

- `relation_lineage_id`

作用：

1. 版本 diff 时识别“同一个实体在不同版本中的演进”
2. 克隆新版本时保留 lineage
3. 避免只靠 `entity_name` 做版本间比对

## 7. 为什么不建议只靠 `blueprint_version`

如果继续用 `blueprint_version` 充当运行版本，会有几个问题：

1. blueprint 是设计包，不是编辑态元数据容器
2. blueprint 生成后，映射会继续人工修改，已经偏离原 blueprint
3. DDL 执行结果、部署 graph 名称、运行状态并不属于 blueprint
4. 一份 blueprint 可以衍生多个实现版本

因此：

- `blueprint_version` 保留
- `ontology version` 新增
- 两者通过 `source_blueprint_id/version` 建立来源关系

## 8. 版本生命周期设计

建议版本生命周期如下：

```text
DRAFT
  -> MAPPING
  -> READY_FOR_DDL
  -> DEPLOYED
  -> ARCHIVED
```

### 8.1 DRAFT

适用于：

- 刚从 Guide 生成
- 从旧版本克隆
- 正在编辑实体、属性、关系

约束：

- 可编辑本体
- 不允许标记为默认运行版本

### 8.2 MAPPING

适用于：

- 本体结构基本稳定
- 进入映射补全和确认

约束：

- 可继续小范围编辑本体
- 映射状态成为主要关注点

### 8.3 READY_FOR_DDL

适用于：

- 本体与映射均已确认
- 准备生成 DDL

约束：

- 默认应冻结版本
- 不允许继续随意改实体结构

### 8.4 DEPLOYED

适用于：

- DDL 已执行成功
- Graph 对象已落地

约束：

- 必须冻结
- 可用于图浏览、图查询、Agent Skill

### 8.5 ARCHIVED

适用于：

- 历史版本不再作为默认编辑或默认运行版本

约束：

- 只读

## 9. 版本创建方式

系统需要支持三种创建方式：

### 9.1 从 Guide Blueprint 创建新版本

入口：

- Guide 预览结果页
- “应用为新版本”

行为：

1. 选择 blueprint
2. 创建 `SYS_ONTOLOGY_VERSION`
3. 把 blueprint 的实体/关系 seed 写入该版本下的本体元数据
4. 初始化映射 seed

### 9.2 从现有版本克隆新版本

入口：

- 本体关系构建页
- 版本管理页

行为：

1. 复制实体/属性/关系
2. 复制映射
3. 保留 lineage id
4. 新版本重新分配对象主键 ID

适合：

- V1 演进出 V2
- 做试验性分支

### 9.3 从已部署 Oracle Node/Edge/Graph 反向导入创建版本

结合当前已新增的 DDL 反向导入工具，后续建议支持：

- 从 `ONTO_NODE_* / ONTO_EDGE_* / PROPERTY GRAPH` DDL 反向生成一个新版本

适合：

- 外部已有 Oracle 图实现，希望纳入平台管理

## 10. 数据模型改造建议

## 10.1 `SYS_ONTOLOGY_ENTITY`

新增：

- `version_id`
- `entity_lineage_id`

约束建议：

- 唯一索引：`(version_id, entity_name)`
- 普通索引：`(domain_id, version_id)`

说明：

- `domain_id` 可继续保留，方便按域查
- 但真正页面查询必须优先按 `version_id`

## 10.2 `SYS_ONTOLOGY_PROPERTY`

新增：

- `version_id`
- `property_lineage_id`

约束建议：

- 唯一索引：`(entity_id, property_name)`

说明：

- 虽然可通过 `entity_id` 反推版本，但建议冗余 `version_id` 便于查询和校验

## 10.3 `SYS_ONTOLOGY_RELATION`

新增：

- `version_id`
- `relation_lineage_id`

约束建议：

- 唯一索引：`(version_id, source_entity_id, target_entity_id, relation_name)`

## 10.4 映射三表

新增：

- `version_id`

原因：

- 避免仅通过对象 ID 间接判断版本
- 便于快速按版本统计映射完整度

## 10.5 `SYS_DDL_LOG`

新增：

- `version_id`
- `graph_object_name`
- `object_naming_strategy`
- `ddl_version_snapshot_json`

作用：

1. 明确某次 DDL 执行对应哪个实现版本
2. 记录该版本实际部署到哪个 graph 对象名
3. 支持同一版本多次重复部署审计

### 10.6 新增部署记录表：`SYS_GRAPH_DEPLOYMENT`

建议新增，用于记录真正落地对象：

| 字段 | 说明 |
| --- | --- |
| `deployment_id` | 主键 |
| `version_id` | 对应实现版本 |
| `domain_id` | 业务域 |
| `source_id` | 目标数据源 |
| `schema_name` | 目标 schema |
| `graph_object_name` | 部署后的 graph 名 |
| `node_object_prefix` | 本次节点对象前缀/版本后缀 |
| `edge_object_prefix` | 本次边对象前缀/版本后缀 |
| `deployment_status` | `SUCCESS / FAILED / ROLLED_BACK / RETIRED` |
| `is_active_runtime` | 是否当前运行版本 |
| `ddl_log_id` | 对应 DDL 执行日志 |
| `deployed_at / deployed_by` | 审计字段 |

说明：

- `SYS_DDL_LOG` 记录“执行过程”
- `SYS_GRAPH_DEPLOYMENT` 记录“运行状态”

二者不要混用。

## 11. Oracle 对象命名规则

为了支持多个版本同时部署，Oracle 对象必须区分版本。

推荐：

### 11.1 技术版本后缀

使用短且稳定的技术后缀：

- `V001`
- `V002`
- `V003`

不要直接把长语义版本号塞到对象名里，避免超过 Oracle 名称长度限制。

### 11.2 节点对象

```text
ONTO_NODE_<ENTITY>_V001
ONTO_NODE_<ENTITY>_V002
```

### 11.3 边对象

```text
ONTO_EDGE_<RELATION>_V001
ONTO_EDGE_<RELATION>_V002
```

### 11.4 Property Graph 对象

```text
<BASE_GRAPH_NAME>_V001
<BASE_GRAPH_NAME>_V002
```

例如：

```text
TIRE_PROCESS_GRAPH_V001
TIRE_PROCESS_GRAPH_V002
```

### 11.5 显示名称与技术名称分离

页面展示：

- 图名称：`金鱼轮胎质量追溯图谱 V2`

实际 Oracle 名：

- `TIRE_PROCESS_GRAPH_V002`

## 12. 各模块改造设计

## 12.1 业务分析域管理

新增“版本管理”能力：

- 列出该域全部版本
- 创建版本
- 从 blueprint 创建
- 从旧版本克隆
- 冻结/解冻
- 设为默认编辑版本
- 设为默认运行版本
- 归档版本

建议：

- 不把版本混在 Domain 列表页中编辑
- 单独做“域 > 版本”二级管理

## 12.2 Guide 自动生成

新增两个动作：

1. `保存为 Blueprint`
2. `应用为新版本`

当前的“直接应用当前预览”需要改为：

- 应用到现有版本
- 或新建版本后应用

默认推荐：

- 新建版本后应用

避免误覆盖旧版本。

## 12.3 本体关系构建

页面需要显式选择 `version_id`。

改造点：

1. 顶部增加版本选择器
2. 新建实体/属性/关系全部写入当前版本
3. 默认只允许编辑 `EDITABLE` 版本
4. 冻结版本只读展示
5. 提供“版本对比”能力

## 12.4 数据映射管理

页面必须基于版本工作。

改造点：

1. 顶部版本选择器
2. 获取实体、属性、关系时按 `version_id`
3. 批量映射任务 `SYS_MAPPING_TASK.request_json` 需带 `version_id`
4. 映射建议与确认结果只影响该版本
5. 映射完成度统计按版本显示

## 12.5 DDL 生成与应用

DDL 页需要从“按域生成”改为“按版本生成”。

改造点：

1. 顶部先选业务域，再选版本
2. DDL 上下文全部按 `version_id`
3. 生成的对象名自动附加版本后缀
4. 执行日志写入 `version_id`
5. 成功执行后落一条 `SYS_GRAPH_DEPLOYMENT`

页面还应展示：

- 版本号
- 目标数据源
- 目标 schema
- graph 技术名
- graph 部署状态
- 是否默认运行版本

## 12.6 本体浏览与图查询

浏览和图查询不应只按域取“当前图”。

需要支持：

1. 选择版本
2. 自动解析该版本当前 active deployment 的 `graph_object_name`
3. 若该版本未部署，页面应提示不可浏览

## 12.7 Agent Skill

Skill 必须绑定版本。

建议：

- `SYS_AGENT_SKILL` 增加 `version_id`

这样 Skill 构建时：

- 打包的是哪个版本的本体
- 绑定的是哪个版本的 graph 对象
- 后续测试和执行都可回放到同一版本上下文

## 13. API 改造建议

原则：

- 几乎所有域内接口都要新增 `version_id`
- 禁止后端静默回退到“该域任意版本”

### 13.1 本体 API

现有：

- `/domains/{domain_id}/entities`
- `/domains/{domain_id}/relations`

建议：

- `/domains/{domain_id}/versions`
- `/ontology/versions/{version_id}/entities`
- `/ontology/versions/{version_id}/relations`

### 13.2 映射 API

所有读取/写入映射的接口都要带 `version_id`。

### 13.3 DDL API

建议新增：

- `POST /ddl/versions/{version_id}/generate`
- `POST /ddl/versions/{version_id}/execute`
- `GET /ddl/versions/{version_id}/logs`

### 13.4 浏览 API

建议新增：

- `GET /ontology/versions/{version_id}/graph`
- `POST /ontology/versions/{version_id}/graph/instances`

## 14. 查询与兼容策略

为了减少改动面，建议分两层推进：

### 14.1 第一层：服务层显式按版本过滤

短期内：

- 继续保留现有表结构和 API 风格
- 但服务层强制传 `version_id`

### 14.2 第二层：逐步把前端页面主入口切换为版本化 API

避免一次性重写全部前端。

## 15. 迁移方案

## 15.1 第一步：补版本主表和字段

新增：

- `SYS_ONTOLOGY_VERSION`
- 相关表 `version_id`
- 必要索引

## 15.2 第二步：为现有每个业务域创建“初始化版本”

规则：

- 每个业务域生成一个 `V001`
- 把当前单版本实体/映射/DDL 记录全部绑定到该版本

## 15.3 第三步：改服务层

让以下服务全部支持 `version_id`：

- ontology
- mapping
- ddl
- browse
- agent

## 15.4 第四步：改前端

增加版本选择器和版本管理页。

## 15.5 第五步：改 DDL 命名和部署记录

上线后，新版本 DDL 全部带版本后缀。

## 16. 向后兼容策略

为降低切换风险，建议保留以下兼容规则：

1. 如果接口未传 `version_id`，仅在过渡期内允许回退到 `is_default_edit_version = Y`
2. DDL 浏览类接口可回退到 `is_default_runtime_version = Y`
3. 一旦前端全面切换完成，逐步取消隐式回退

注意：

这种回退只能作为迁移期临时策略，不能长期依赖。

## 17. 版本 diff 能力建议

为提升可用性，建议新增版本对比能力：

1. 实体新增/删除/改名
2. 属性新增/删除/类型变化/主键变化
3. 关系新增/删除/基数变化
4. 映射变化
5. DDL 对象名变化

建议在服务端基于 `lineage_id` 优先做 diff。

## 18. 风险与关键决策

### 18.1 风险：只加 `version_id` 但不改页面上下文

结果会是：

- 库里有多个版本
- 页面仍然混查

这是最危险的半改状态。

### 18.2 风险：Graph 对象名不带版本后缀

结果会是：

- 不同版本部署互相覆盖

### 18.3 风险：已部署版本仍可编辑

结果会是：

- 平台元数据与 Oracle 实际对象不一致

因此必须有 `FROZEN` 控制。

## 19. 推荐实施顺序

### P0：数据模型和上下文打底

1. 新增 `SYS_ONTOLOGY_VERSION`
2. 本体/映射/DDL 表增加 `version_id`
3. 给现有域生成初始化版本

### P1：本体与映射版本化

1. 本体关系构建页支持版本选择
2. 数据映射页支持版本选择
3. Guide 支持“应用为新版本”

### P2：DDL 与部署版本化

1. DDL 生成按版本
2. Oracle 对象名带版本后缀
3. 新增 `SYS_GRAPH_DEPLOYMENT`

### P3：浏览、图查询、Skill 版本化

1. 图浏览绑定版本
2. 图查询绑定版本
3. Agent Skill 绑定版本

### P4：版本 diff 与发布流程

1. 版本对比
2. 默认编辑/默认运行切换
3. 归档与回滚

## 20. 推荐结论

对本项目最合适的方案不是“把现有 blueprint 继续扩展一下”，而是：

1. 保留 `Blueprint` 作为设计版本
2. 新增 `Ontology Version` 作为运行实现版本
3. 让本体、映射、DDL、部署、浏览、Skill 全部以 `version_id` 为主上下文
4. Oracle 节点、边、Property Graph 对象统一带版本后缀
5. 已部署版本冻结，只允许通过“克隆新版本”继续演进

这样才能真正支撑：

- 同域多个本体实现并行存在
- 同域多个映射版本并行存在
- 同域多个 graph 对象并行验证
- 可回退、可审计、可对比、可发布

## 21. 本项目建议新增对象清单

### 新表

- `SYS_ONTOLOGY_VERSION`
- `SYS_GRAPH_DEPLOYMENT`

### 现有表新增字段

- `SYS_ONTOLOGY_ENTITY.version_id`
- `SYS_ONTOLOGY_ENTITY.entity_lineage_id`
- `SYS_ONTOLOGY_PROPERTY.version_id`
- `SYS_ONTOLOGY_PROPERTY.property_lineage_id`
- `SYS_ONTOLOGY_RELATION.version_id`
- `SYS_ONTOLOGY_RELATION.relation_lineage_id`
- `SYS_ENTITY_MAPPING.version_id`
- `SYS_PROPERTY_MAPPING.version_id`
- `SYS_RELATION_MAPPING.version_id`
- `SYS_DDL_LOG.version_id`
- `SYS_DDL_LOG.graph_object_name`
- `SYS_PROCESS_DEF.version_id`
- `SYS_METRIC_DEFINITION.version_id`
- `SYS_BUSINESS_RULE.version_id`
- `SYS_BUSINESS_ACTIVITY.version_id`
- `SYS_AGENT_SKILL.version_id`

### 现有接口新增请求参数

- `version_id`

适用模块：

- 本体关系构建
- 数据映射管理
- DDL 生成与应用
- 本体浏览管理
- 图数据查询
- 智能体构建与测试

