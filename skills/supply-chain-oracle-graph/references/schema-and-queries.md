# Schema and query reference

## Required database assets

Run `database/oracle/gyl_Oracle26ai_DDL.sql`, then `database/oracle/gyl_Oracle26ai_seed_1000.sql`, then `database/oracle/gyl_onto_ddl.sql`. The graph is `PG_JDXQ_SUPPLY_TRACE`.

| Business area | Tables / graph labels |
|---|---|
| Five-code hierarchy | `bottle_code` → `pack_code` → `case_code` → `pallet_code` → `stack_code`; graph labels `BOTTLECODE`, `PACKCODE`, `CASECODE`, `PALLETCODE`, `STACKCODE` |
| Hierarchy links | `bottle_pack_relation`, `pack_case_relation`, `case_pallet_relation`, `pallet_stack_relation` |
| Production and quality | `production_batch`, `production_line`, `factory`, `quality_inspection`; labels `PRODUCTIONBATCH`, `PRODUCTIONLINE`, `FACTORY`, `QUALITYINSPECTION` |
| Fulfilment | `outbound_order`, `outbound_detail`, `transport_order`, `distributor_inbound`; labels `OUTBOUNDORDER`, `TRANSPORTORDER`, `DISTRIBUTORINBOUND` |
| Channel | `distributor`, `retail_store`; labels `DISTRIBUTOR`, `RETAILSTORE` |

The seed contains three simulated outbound orders. Each uses `outbound_detail.level_type = 'PALLET'`, so `outbound_detail.case_id` is intentionally null. Derive the boxes under a pallet through `case_pallet_relation`.

## Preflight SQL

```sql
SELECT object_name, object_type
FROM user_objects
WHERE object_name IN ('PG_JDXQ_SUPPLY_TRACE', 'BOTTLE_CODE', 'OUTBOUND_DETAIL')
   OR object_name LIKE 'ONTO_NODE_%'
ORDER BY object_type, object_name
FETCH FIRST 50 ROWS ONLY;
```

## Exact bottle-code upstream evidence

```sql
SELECT b.bottle_code, b.code_status, b.production_time,
       p.sku_code, p.product_name, pb.batch_no, pb.production_date,
       f.factory_code, f.factory_name, qi.inspection_result, qi.inspection_time
FROM bottle_code b
JOIN product p ON p.product_id = b.product_id
JOIN production_batch pb ON pb.batch_id = b.batch_id
JOIN factory f ON f.factory_id = pb.factory_id
LEFT JOIN quality_inspection qi ON qi.batch_id = pb.batch_id
WHERE b.bottle_code = :bottle_code
ORDER BY qi.inspection_time DESC
FETCH FIRST 20 ROWS ONLY;
```

## Five-code graph path

```sql
WITH trace_path AS (
  SELECT * FROM GRAPH_TABLE(
    PG_JDXQ_SUPPLY_TRACE
    MATCH (b IS BOTTLECODE)-[e1 IS GRAPH_LABEL]->(p IS PACKCODE)
          -[e2 IS GRAPH_LABEL]->(c IS CASECODE)
          -[e3 IS GRAPH_LABEL]->(pal IS PALLETCODE)
          -[e4 IS GRAPH_LABEL]->(s IS STACKCODE)
    COLUMNS (
      b.BOTTLE_ID AS bottle_id, b.BOTTLE_CODE AS bottle_code,
      p.PACK_ID AS pack_id, p.PACK_CODE AS pack_code,
      c.CASE_ID AS case_id, c.CASE_CODE AS case_code,
      pal.PALLET_ID AS pallet_id, pal.PALLET_CODE AS pallet_code,
      s.STACK_ID AS stack_id, s.STACK_CODE AS stack_code,
      e1.RELATION_NAME AS bottle_pack_relation,
      e2.RELATION_NAME AS pack_case_relation,
      e3.RELATION_NAME AS case_pallet_relation,
      e4.RELATION_NAME AS pallet_stack_relation
    )
  )
)
SELECT 'BOTTLE:' || bottle_id AS source_id, bottle_code AS source_label,
       'PACK:' || pack_id AS target_id, pack_code AS target_label,
       bottle_pack_relation AS relation_name
FROM trace_path WHERE bottle_code = :bottle_code
UNION ALL
SELECT 'PACK:' || pack_id, pack_code, 'CASE:' || case_id, case_code, pack_case_relation
FROM trace_path WHERE bottle_code = :bottle_code
UNION ALL
SELECT 'CASE:' || case_id, case_code, 'PALLET:' || pallet_id, pallet_code, case_pallet_relation
FROM trace_path WHERE bottle_code = :bottle_code
UNION ALL
SELECT 'PALLET:' || pallet_id, pallet_code, 'STACK:' || stack_id, stack_code, pallet_stack_relation
FROM trace_path WHERE bottle_code = :bottle_code;
```

## Downstream fulfilment for a bottle

```sql
WITH code_chain AS (
  SELECT bc.bottle_code, cc.case_id, plc.pallet_id
  FROM bottle_code bc
  JOIN bottle_pack_relation bpr ON bpr.bottle_id = bc.bottle_id
  JOIN pack_case_relation pcr ON pcr.pack_id = bpr.pack_id
  JOIN case_code cc ON cc.case_id = pcr.case_id
  LEFT JOIN case_pallet_relation cpr ON cpr.case_id = cc.case_id
  LEFT JOIN pallet_code plc ON plc.pallet_id = cpr.pallet_id
  WHERE bc.bottle_code = :bottle_code
)
SELECT cc.bottle_code, oo.outbound_no, oo.outbound_time, too.transport_no,
       too.status AS transport_status, di.inbound_no, di.inbound_time,
       d.distributor_code, d.distributor_name
FROM code_chain cc
LEFT JOIN outbound_detail obd
  ON (obd.case_id = cc.case_id AND obd.level_type = 'CASE')
  OR (obd.pallet_id = cc.pallet_id AND obd.level_type = 'PALLET')
LEFT JOIN outbound_order oo ON oo.outbound_id = obd.outbound_id
LEFT JOIN transport_order too ON too.outbound_id = oo.outbound_id
LEFT JOIN distributor_inbound di ON di.transport_id = too.transport_id
LEFT JOIN distributor d ON d.distributor_id = di.distributor_id
ORDER BY oo.outbound_time DESC
FETCH FIRST 50 ROWS ONLY;
```

## SQLcl MCP setup

SQLcl 25.2+ and JRE 17/21 are required. Save a dedicated read-only connection first; never put a password in an MCP configuration:

```sql
conn -save nfsq_trace_readonly -savepwd user/password@//host:1521/service
```

Start the MCP server with an absolute SQLcl path and the default restrictive mode:

```sh
/absolute/path/to/sql -mcp
```

The Agent calls `list-connections`, `connect` with `nfsq_trace_readonly`, then `run-sql`. Grant only `CREATE SESSION` and `SELECT` on the required source, node, edge and graph objects. Do not grant `DBA`, `SELECT ANY TABLE`, DML, DDL, or credentials to the Agent.
