---
name: supply-chain-oracle-graph
description: 通过 SQLcl MCP 分析 Oracle Database 26ai 中的金鼎仙泉/农夫山泉式五码合一供应链追溯数据。适用于瓶码、盒码、箱码、托码或垛码追溯，生产与质检分析，出库、运输、入库、经销商链路分析，以及针对 PG_JDXQ_SUPPLY_TRACE 的 Oracle Property Graph 灵活查询。
---

# 供应链 Oracle 图谱分析

使用 SQLcl MCP 对五码合一供应链数据给出有证据支撑的只读分析结果。编写业务查询前，先阅读 `references/schema-and-queries.md`。

## 连接与安全

1. 按 `list-connections`、`connect`、`run-sql` 的顺序调用 SQLcl MCP 工具。
2. 选择已保存的最小权限分析连接。不得索取、展示或在任何内容中写入密码；SQLcl 连接必须已通过 `conn -save -savepwd` 预先保存。
3. 查询前检查当前 Schema，确认 `PG_JDXQ_SUPPLY_TRACE` 与所需 `ONTO_NODE_*` / `ONTO_EDGE_*` 对象存在。
4. 每次仅执行一条只读 `SELECT` 或 `WITH ... SELECT` 语句。不得使用 DDL、DML、PL/SQL、`DBMS_*`、授权语句或宽泛的数据字典权限。
5. 使用明确且合理的行数限制。回答中说明数据粒度、使用的码/ID，以及缺失链路或前提假设。

若 SQLcl MCP 不可用，依据参考文件说明最小连接配置；不得用猜测的数据库结果替代查询结果。

## 查询选型

- 对汇总、状态分布、当前简单关联和精确业务单据，使用关系型 SQL。
- 对可变长度路径、码层级、多跳渠道路径或影响范围解释，使用 `GRAPH_TABLE(PG_JDXQ_SUPPLY_TRACE ...)`。
- 从最精确的已知标识开始：`BOTTLE_CODE`、`PACK_CODE`、`CASE_CODE`、`PALLET_CODE`、`STACK_CODE`、`BATCH_NO`、`OUTBOUND_NO`、`TRANSPORT_NO` 或 `DISTRIBUTOR_CODE`。

## Agent 工作流

1. 将请求归类为上游追溯（产品/批次/工厂/质检）、包装层级（瓶→盒→箱→托→垛）或下游追溯（出库→运输→入库→经销商/门店）。
2. 先用小范围查询校验输入标识。若无法匹配，应如实说明，不能放宽为无边界搜索。
3. 执行能够回答问题的最小查询：路径问题使用图查询模板，运营事实使用关系型查询模板。
4. 交叉核验关键下游结论：Seed 数据按托码记录出库明细，因此 `outbound_detail.case_id` 为空时，必须通过 `case_pallet_relation` 反查箱码。
5. 返回简洁结果，包含匹配的标识、路径或汇总、观察到的状态/时间及数据局限。不得仅基于图路径宣称存在因果关系。

## 输出格式

输出分析结果时使用以下结构：

1. 一句话结论。
2. 证据：标识、时间戳、状态以及匹配的路径/记录。
3. 异常或限制：包括缺失边、缺失扫码记录或模拟数据范围。
4. 使用的只读 SQL；用户明确要求只输出业务结论时除外。

## 图查询结果约定

用于图形展示时，返回 `SOURCE_ID`、`TARGET_ID` 和 `RELATION_NAME`。为 ID 加上顶点类型前缀，例如 `BOTTLE:1`、`CASE:1`，避免不同顶点表的数值主键相同而发生错误合并。
