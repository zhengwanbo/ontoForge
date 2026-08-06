from typing import Any, Dict, List


def build_canonical_model(analysis_context: Dict[str, Any]) -> Dict[str, Any]:
    focus_scope = analysis_context.get("focus_scope") or {}
    schema_analysis = analysis_context.get("schema_analysis") or {}
    selected_table_schema = analysis_context.get("selected_table_schema") or {}
    metric_semantics = analysis_context.get("metric_semantics") or {}
    rule_analysis = analysis_context.get("rule_analysis") or {}
    key_tables = schema_analysis.get("key_tables") or {}
    product_index_table = key_tables.get("product_index_table") or ""
    process_table = key_tables.get("process_table") or ""
    test_tables = list(key_tables.get("test_tables") or [])
    rule_tables = list(key_tables.get("rule_tables") or [])
    aa_tables = list(key_tables.get("aa_feature_tables") or [])
    alarm_tables = list(key_tables.get("alarm_tables") or [])
    history_tables = list(key_tables.get("history_case_tables") or [])
    focus_stations = list(focus_scope.get("focus_stations") or [])

    table_columns = {
        (table.get("table_name") or "").upper(): {
            (column.get("column_name") or "").upper()
            for column in (table.get("columns") or [])
            if column.get("column_name")
        }
        for table in (selected_table_schema.get("tables") or [])
        if table.get("table_name")
    }

    def has_column(table_name: str, column_name: str) -> bool:
        return column_name.upper() in table_columns.get((table_name or "").upper(), set())

    def make_property(
        property_name: str,
        display_name: str,
        desc: str,
        source_table: str,
        source_column: str,
        *,
        data_type: str = "VARCHAR2",
        primary_key: bool = False,
        nullable: bool = True,
        mapping_type: str = "DIRECT",
        formula: str = "",
    ) -> Dict[str, Any]:
        return {
            "propertyName": property_name,
            "propertyDisplayName": display_name,
            "propertyDesc": desc,
            "dataType": data_type,
            "isPrimaryKey": "Y" if primary_key else "N",
            "isNullable": "N" if not nullable else "Y",
            "sourceTable": source_table,
            "sourceColumn": source_column,
            "sourceDataType": data_type,
            "mappingType": mapping_type,
            "formula": formula or None,
        }

    entities: List[Dict[str, Any]] = []

    if product_index_table:
        product_props = []
        for column_name, display_name in [
            ("VCM_ID", "VCM_ID"),
            ("MODULE_ID", "模组ID"),
            ("LOT", "批次"),
            ("SENSOR_ID", "SensorID"),
            ("LENS_ID", "LensID"),
            ("FLEX_ID", "FlexID"),
            ("DEFECT_CODE", "缺陷代码"),
            ("MODEL", "机种"),
            ("CONFIG", "配置"),
        ]:
            if has_column(product_index_table, column_name):
                product_props.append(make_property(
                    property_name=column_name.lower(),
                    display_name=display_name,
                    desc=f"来自 {product_index_table} 的 {column_name}",
                    source_table=product_index_table,
                    source_column=column_name,
                    primary_key=(column_name == "VCM_ID"),
                    nullable=(column_name != "VCM_ID"),
                ))
        entities.append({
            "entityName": "ProductUnit",
            "entityDisplayName": "产品",
            "entityDesc": "以 VCM_ID / MODULE_ID 等标识关联测试、过程、设备与缺陷证据的产品主对象。",
            "buildType": "VIEW",
            "sourceHints": [product_index_table],
            "properties": product_props,
        })
        if has_column(product_index_table, "MODEL"):
            entities.append({
                "entityName": "ProductModel",
                "entityDisplayName": "机种",
                "entityDesc": "产品所属机种或配置模型，用于机种维度的归因与影响分析。",
                "buildType": "VIEW",
                "sourceHints": [product_index_table],
                "properties": [
                    make_property("model_code", "机种编码", "产品机种标识。", product_index_table, "MODEL", primary_key=True, nullable=False),
                    make_property("config_code", "配置编码", "产品配置标识。", product_index_table, "CONFIG" if has_column(product_index_table, "CONFIG") else "MODEL"),
                ],
            })

    if test_tables:
        primary_test_table = test_tables[0]
        entities.append({
            "entityName": "TestRun",
            "entityDisplayName": "测试事件",
            "entityDesc": "一次具体测试执行事件，承接产品、测试家族、PASS/FAIL 与测试上下文。",
            "buildType": "VIEW",
            "sourceHints": test_tables,
            "properties": [
                make_property("test_run_id", "测试事件ID", "测试事件唯一标识，建议由产品标识与测试上下文组合生成。", primary_test_table, "VCM_ID", primary_key=True, nullable=False, mapping_type="COMPUTED", formula="VCM_ID || ':' || NVL(DCKEY,'TEST')"),
                make_property("vcm_id", "VCM_ID", "测试对应产品标识。", primary_test_table, "VCM_ID", nullable=False),
                make_property("pass_fail_description", "测试结论", "测试通过/失败描述。", primary_test_table, "PASS_FAIL_DESCRIPTION" if has_column(primary_test_table, "PASS_FAIL_DESCRIPTION") else "VCM_ID"),
                make_property("socket_id", "SocketID", "测试使用的 socket。", primary_test_table, "SOCKETID" if has_column(primary_test_table, "SOCKETID") else "VCM_ID"),
                make_property("dc_key", "测试上下文键", "测试上下文或配方键。", primary_test_table, "DCKEY" if has_column(primary_test_table, "DCKEY") else "VCM_ID"),
            ],
        })
        entities.append({
            "entityName": "MetricResult",
            "entityDisplayName": "测项结果",
            "entityDesc": "从 SFR / Dark-B 等宽表拆出的单指标测量结果对象。",
            "buildType": "VIEW",
            "sourceHints": test_tables,
            "properties": [
                make_property("metric_result_id", "测项结果ID", "测项结果唯一标识。", primary_test_table, "VCM_ID", primary_key=True, nullable=False, mapping_type="COMPUTED", formula="VCM_ID || ':' || NVL(DCKEY,'TEST') || ':' || metric_name"),
                make_property("test_run_id", "测试事件ID", "所属测试事件。", primary_test_table, "VCM_ID", nullable=False),
                make_property("metric_name", "测项名", "拆分后的指标名称。", primary_test_table, "VCM_ID", nullable=False, mapping_type="COMPUTED", formula="metric_name"),
                make_property("metric_value", "测量值", "拆分后的指标值。", primary_test_table, "VCM_ID", data_type="NUMBER", mapping_type="COMPUTED", formula="metric_value"),
                make_property("spec_family", "规格族", "指标所属规格族。", primary_test_table, "DCKEY" if has_column(primary_test_table, "DCKEY") else "VCM_ID"),
            ],
        })

    if rule_tables:
        primary_rule_table = rule_tables[0]
        entities.append({
            "entityName": "MetricSpec",
            "entityDisplayName": "指标规格",
            "entityDesc": "由 SPEC_LIMIT 规则表抽出的指标规格定义及其上下限。",
            "buildType": "VIEW",
            "sourceHints": rule_tables,
            "properties": [
                make_property("metric_spec_id", "规格ID", "规格记录唯一标识。", primary_rule_table, "DB_NAME", primary_key=True, nullable=False, mapping_type="COMPUTED", formula="SPEC_FAMILY || ':' || DB_NAME"),
                make_property("spec_family", "规格族", "规格族名称。", primary_rule_table, "SPEC_FAMILY", nullable=False),
                make_property("metric_name", "指标名", "规格对应的指标名。", primary_rule_table, "DB_NAME", nullable=False),
                make_property("lsl_value", "LSL", "指标下限。", primary_rule_table, "LSL", data_type="NUMBER"),
                make_property("usl_value", "USL", "指标上限。", primary_rule_table, "USL", data_type="NUMBER"),
            ],
        })

    entities.append({
        "entityName": "DefectType",
        "entityDisplayName": "缺陷类型",
        "entityDesc": "由 SFR 语义分类归纳出的缺陷类型或缺陷表型。",
        "buildType": "VIEW",
        "sourceHints": rule_tables or test_tables,
        "properties": [
            {
                "propertyName": "defect_type_code",
                "propertyDisplayName": "缺陷类型编码",
                "propertyDesc": "缺陷类型唯一标识。",
                "dataType": "VARCHAR2",
                "isPrimaryKey": "Y",
                "isNullable": "N",
                "sourceTable": (rule_tables or test_tables or [""])[0],
                "sourceColumn": "DB_NAME" if rule_tables else "VCM_ID",
                "sourceDataType": "VARCHAR2",
                "mappingType": "COMPUTED",
                "formula": "semantic_defect_code",
            },
            {
                "propertyName": "defect_type_name",
                "propertyDisplayName": "缺陷类型名",
                "propertyDesc": "缺陷语义类别名称。",
                "dataType": "VARCHAR2",
                "isPrimaryKey": "N",
                "isNullable": "N",
                "sourceTable": (rule_tables or test_tables or [""])[0],
                "sourceColumn": "DB_NAME" if rule_tables else "VCM_ID",
                "sourceDataType": "VARCHAR2",
                "mappingType": "COMPUTED",
                "formula": "semantic_defect_name",
            },
        ],
    })

    if process_table:
        entities.extend([
            {
                "entityName": "ProcessEvent",
                "entityDisplayName": "过程事件",
                "entityDesc": "由过程宽表拆出的站位级过程事件，承接设备、治具、物料和时间信息。",
                "buildType": "VIEW",
                "sourceHints": [process_table],
                "properties": [
                    make_property("process_event_id", "过程事件ID", "过程事件唯一标识。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), primary_key=True, nullable=False, mapping_type="COMPUTED", formula="product_id || ':' || station_code || ':' || process_time"),
                    make_property("product_unit_id", "产品ID", "过程事件关联的产品。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), nullable=False),
                    make_property("station_code", "站位编码", "过程站位编码。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), mapping_type="COMPUTED", formula="station_code"),
                    make_property("process_start_time", "开始时间", "过程开始时间。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), mapping_type="COMPUTED", formula="process_start_time"),
                    make_property("process_end_time", "结束时间", "过程结束时间。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), mapping_type="COMPUTED", formula="process_end_time"),
                    make_property("equipment_code", "设备编码", "过程事件执行设备的标准编码。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), mapping_type="COMPUTED", formula="equipment_code"),
                    make_property("tooling_id", "治具载具编码", "过程事件使用的治具、载具或 socket 标识。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), mapping_type="COMPUTED", formula="tooling_id"),
                    make_property("material_lot_id", "物料批次ID", "过程事件关联或消耗的物料批次标识。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), mapping_type="COMPUTED", formula="material_lot_id"),
                ],
            },
            {
                "entityName": "Station",
                "entityDisplayName": "站位",
                "entityDesc": "制造流程中的关键站位或工序节点。",
                "buildType": "VIEW",
                "sourceHints": [process_table],
                "properties": [
                    make_property("station_code", "站位编码", "站位唯一编码。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), primary_key=True, nullable=False, mapping_type="COMPUTED", formula="station_code"),
                    make_property("station_group", "站位组", "站位所属工序组，例如 LBI / AA / FTU / FTD。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), mapping_type="COMPUTED", formula="station_group"),
                ],
            },
            {
                "entityName": "Equipment",
                "entityDisplayName": "设备",
                "entityDesc": "过程事件运行设备对象。",
                "buildType": "VIEW",
                "sourceHints": [process_table],
                "properties": [
                    make_property("equipment_id", "设备ID", "设备唯一标识。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), primary_key=True, nullable=False, mapping_type="COMPUTED", formula="equipment_id"),
                    make_property("station_code", "站位编码", "设备所属站位。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), mapping_type="COMPUTED", formula="station_code"),
                ],
            },
            {
                "entityName": "ToolingCarrier",
                "entityDisplayName": "治具载具",
                "entityDesc": "过程事件中使用的治具、socket、carrier 或 tooling 对象。",
                "buildType": "VIEW",
                "sourceHints": [process_table] + test_tables,
                "properties": [
                    make_property("tooling_id", "治具载具ID", "治具/载具唯一标识。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), primary_key=True, nullable=False, mapping_type="COMPUTED", formula="tooling_id"),
                    make_property("tooling_type", "治具类型", "socket/carrier/tooling 类型。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), mapping_type="COMPUTED", formula="tooling_type"),
                ],
            },
            {
                "entityName": "MaterialLot",
                "entityDisplayName": "物料批次",
                "entityDesc": "过程事件消耗的原材料批次、镜头批次等物料对象。",
                "buildType": "VIEW",
                "sourceHints": [process_table, product_index_table] if product_index_table else [process_table],
                "properties": [
                    make_property("material_lot_id", "物料批次ID", "物料批次唯一标识。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), primary_key=True, nullable=False, mapping_type="COMPUTED", formula="material_lot_id"),
                    make_property("material_type", "物料类型", "物料类别。", process_table, "VCM_ID" if has_column(process_table, "VCM_ID") else next(iter(table_columns.get(process_table, {"VCM_ID"}))), mapping_type="COMPUTED", formula="material_type"),
                ],
            },
        ])

    if alarm_tables:
        alarm_table = alarm_tables[0]
        entities.append({
            "entityName": "AlarmEvent",
            "entityDisplayName": "报警事件",
            "entityDesc": "与过程或测试时间窗相关联的报警证据对象。",
            "buildType": "VIEW",
            "sourceHints": alarm_tables,
            "properties": [
                make_property("alarm_event_id", "报警事件ID", "报警事件唯一标识。", alarm_table, next(iter(table_columns.get(alarm_table, {"VCM_ID"}))), primary_key=True, nullable=False, mapping_type="COMPUTED", formula="alarm_event_id"),
                make_property("product_unit_id", "产品ID", "报警事件关联的产品单元标识，来源于报警记录的 VCM_ID。", alarm_table, "VCM_ID" if has_column(alarm_table, "VCM_ID") else next(iter(table_columns.get(alarm_table, {"VCM_ID"}))), nullable=False),
                make_property("station_code", "站位编码", "报警所属站位。", alarm_table, next(iter(table_columns.get(alarm_table, {"VCM_ID"}))), mapping_type="COMPUTED", formula="station_code"),
                make_property("alarm_code", "报警码", "报警标识。", alarm_table, next(iter(table_columns.get(alarm_table, {"VCM_ID"}))), mapping_type="COMPUTED", formula="alarm_code"),
            ],
        })

    if aa_tables:
        aa_table = aa_tables[0]
        entities.append({
            "entityName": "AALogFeature",
            "entityDisplayName": "AA特征",
            "entityDesc": "来自 AA 扫描、焦点补偿等日志的辅助证据对象。",
            "buildType": "VIEW",
            "sourceHints": aa_tables,
            "properties": [
                make_property("aa_feature_id", "AA特征ID", "AA 特征唯一标识。", aa_table, next(iter(table_columns.get(aa_table, {"VCM_ID"}))), primary_key=True, nullable=False, mapping_type="COMPUTED", formula="aa_feature_id"),
                make_property("product_unit_id", "产品ID", "AA 特征对应的产品。", aa_table, "VCM_ID" if has_column(aa_table, "VCM_ID") else next(iter(table_columns.get(aa_table, {"VCM_ID"}))), nullable=False),
                make_property("feature_name", "特征名", "AA 特征名称。", aa_table, next(iter(table_columns.get(aa_table, {"VCM_ID"}))), mapping_type="COMPUTED", formula="feature_name"),
                make_property("feature_value", "特征值", "AA 特征值。", aa_table, next(iter(table_columns.get(aa_table, {"VCM_ID"}))), mapping_type="COMPUTED", formula="feature_value"),
            ],
        })

    if history_tables:
        history_table = history_tables[0]
        entities.extend([
            {
                "entityName": "HistoricalCase",
                "entityDisplayName": "历史案例",
                "entityDesc": "历史 FACA 或根因案例对象，用于相似案例复用。",
                "buildType": "VIEW",
                "sourceHints": history_tables,
                "properties": [
                    make_property("historical_case_id", "历史案例ID", "历史案例唯一标识。", history_table, next(iter(table_columns.get(history_table, {"CASE_ID"}))), primary_key=True, nullable=False),
                    make_property("case_title", "案例标题", "历史案例标题或摘要。", history_table, next(iter(table_columns.get(history_table, {"CASE_ID"})))),
                ],
            },
            {
                "entityName": "RootCausePattern",
                "entityDisplayName": "根因模式",
                "entityDesc": "可被多个缺陷实例复用的候选根因模式。",
                "buildType": "VIEW",
                "sourceHints": history_tables,
                "properties": [
                    make_property("root_cause_pattern_id", "根因模式ID", "根因模式唯一标识。", history_table, next(iter(table_columns.get(history_table, {"CASE_ID"}))), primary_key=True, nullable=False, mapping_type="COMPUTED", formula="root_cause_pattern_id"),
                    make_property("pattern_name", "根因模式名", "根因模式名称。", history_table, next(iter(table_columns.get(history_table, {"CASE_ID"}))), mapping_type="COMPUTED", formula="pattern_name"),
                ],
            },
            {
                "entityName": "CorrectiveAction",
                "entityDisplayName": "改善措施",
                "entityDesc": "针对根因模式的改善或纠正动作。",
                "buildType": "VIEW",
                "sourceHints": history_tables,
                "properties": [
                    make_property("corrective_action_id", "改善措施ID", "改善措施唯一标识。", history_table, next(iter(table_columns.get(history_table, {"CASE_ID"}))), primary_key=True, nullable=False, mapping_type="COMPUTED", formula="corrective_action_id"),
                    make_property("action_name", "改善措施名", "改善措施名称。", history_table, next(iter(table_columns.get(history_table, {"CASE_ID"}))), mapping_type="COMPUTED", formula="action_name"),
                ],
            },
        ])

    entities.append({
        "entityName": "ImpactScope",
        "entityDisplayName": "影响范围",
        "entityDesc": "缺陷影响分析中的范围对象，覆盖产品、批次、机种、设备、治具、物料与时间窗。",
        "buildType": "VIEW",
        "sourceHints": [item for item in [product_index_table, process_table] if item] or test_tables[:1],
        "properties": [
            {
                "propertyName": "impact_scope_id",
                "propertyDisplayName": "影响范围ID",
                "propertyDesc": "影响范围唯一标识。",
                "dataType": "VARCHAR2",
                "isPrimaryKey": "Y",
                "isNullable": "N",
                "sourceTable": product_index_table or process_table or (test_tables[0] if test_tables else ""),
                "sourceColumn": "VCM_ID",
                "sourceDataType": "VARCHAR2",
                "mappingType": "COMPUTED",
                "formula": "impact_scope_id",
            },
            {
                "propertyName": "scope_type",
                "propertyDisplayName": "范围类型",
                "propertyDesc": "影响范围类型，如产品/批次/设备/时间窗。",
                "dataType": "VARCHAR2",
                "isPrimaryKey": "N",
                "isNullable": "N",
                "sourceTable": product_index_table or process_table or (test_tables[0] if test_tables else ""),
                "sourceColumn": "VCM_ID",
                "sourceDataType": "VARCHAR2",
                "mappingType": "COMPUTED",
                "formula": "scope_type",
            },
        ],
    })

    relations = [
        _relation("ProductUnit", "ProductModel", "属于", "产品属于某一机种或配置模型。", [product_index_table] if product_index_table else []),
        _relation("ProductUnit", "TestRun", "有测试", "产品发生测试事件。", test_tables, source_table=product_index_table, target_table=test_tables[0] if test_tables else ""),
        _relation("TestRun", "MetricResult", "产生", "测试事件产生拆分后的测项结果。", test_tables),
        _relation("MetricResult", "MetricSpec", "对照", "测项结果对照规格上下限进行判定。", rule_tables or test_tables, source_table=test_tables[0] if test_tables else "", target_table=rule_tables[0] if rule_tables else ""),
        _relation("MetricResult", "DefectType", "指向", "超差指标指向缺陷类型或缺陷表型。", rule_tables or test_tables),
        _relation("ProductUnit", "ProcessEvent", "经过", "产品经过若干过程事件。", [process_table] if process_table else []),
        _relation("ProcessEvent", "Station", "发生于", "过程事件发生在某站位。", [process_table] if process_table else []),
        _relation("ProcessEvent", "Equipment", "运行于", "过程事件运行于某设备。", [process_table] if process_table else []),
        _relation("ProcessEvent", "ToolingCarrier", "使用", "过程事件使用某治具、载具或 socket。", [process_table] + test_tables if process_table else test_tables),
        _relation("ProcessEvent", "MaterialLot", "消耗", "过程事件消耗或关联某物料批次。", [process_table] if process_table else []),
        _relation("ProductUnit", "AlarmEvent", "关联报警", "产品在相关时间窗内关联到报警事件证据。", alarm_tables),
        _relation("ProductUnit", "AALogFeature", "具有AA特征", "产品关联到 AA 扫描或焦点特征。", aa_tables),
        _relation("DefectType", "HistoricalCase", "相似于", "当前缺陷类型可关联相似历史案例。", history_tables),
        _relation("HistoricalCase", "RootCausePattern", "支持", "历史案例支持某类候选根因模式。", history_tables),
        _relation("RootCausePattern", "CorrectiveAction", "解决", "根因模式由改善措施进行缓解或解决。", history_tables),
        _relation("DefectType", "ImpactScope", "影响", "缺陷类型可映射到一个或多个影响范围。", [product_index_table, process_table] if product_index_table or process_table else []),
    ]
    existing_entities = {
        item.get("entityName")
        for item in entities
        if item.get("entityName")
    }
    relations = [
        item for item in relations
        if item.get("sourceEntityName") in existing_entities
        and item.get("targetEntityName") in existing_entities
    ]

    entity_groups = [
        {"group_name": "core", "entities": ["ProductUnit", "ProductModel", "DefectType"]},
        {"group_name": "test", "entities": ["TestRun", "MetricResult", "MetricSpec"]},
        {"group_name": "process", "entities": ["ProcessEvent", "Station", "Equipment", "ToolingCarrier", "MaterialLot"]},
        {"group_name": "evidence", "entities": ["AlarmEvent", "AALogFeature"]},
        {"group_name": "knowledge", "entities": ["HistoricalCase", "RootCausePattern", "CorrectiveAction"]},
        {"group_name": "impact", "entities": ["ImpactScope"]},
    ]
    relation_groups = [
        {"group_name": "product_test", "relations": ["属于", "有测试", "产生", "对照", "指向"]},
        {"group_name": "process_trace", "relations": ["经过", "发生于", "运行于", "使用", "消耗"]},
        {"group_name": "evidence", "relations": ["关联报警", "具有AA特征"]},
        {"group_name": "knowledge", "relations": ["相似于", "支持", "解决"]},
        {"group_name": "impact", "relations": ["影响"]},
    ]
    mapping_hints = {
        "decomposition_rules": [
            "PDX25_TAMS_UNIT 作为产品主索引来源。",
            "PROCESS 宽表拆为 ProcessEvent。",
            "SFR 测试宽表拆为 TestRun + MetricResult。",
            "SPEC_LIMIT 拆为 MetricSpec。",
        ],
        "focus_metric_families": list(focus_scope.get("focus_metric_families") or []),
        "focus_stations": focus_stations,
        "metric_semantics": metric_semantics.get("semantic_categories") or [],
        "rule_source_table": rule_tables[0] if rule_tables else "",
    }

    return {
        "entities": entities,
        "relations": relations,
        "entity_groups": entity_groups,
        "relation_groups": relation_groups,
        "mapping_hints": mapping_hints,
        "focus_metric_families": focus_scope.get("focus_metric_families") or rule_analysis.get("primary_metric_families") or [],
    }


def build_view_plan(analysis_context: Dict[str, Any], canonical_model: Dict[str, Any]) -> Dict[str, Any]:
    schema_analysis = analysis_context.get("schema_analysis") or {}
    focus_scope = analysis_context.get("focus_scope") or {}
    key_tables = schema_analysis.get("key_tables") or {}
    product_index_table = key_tables.get("product_index_table") or ""
    process_table = key_tables.get("process_table") or ""
    test_tables = list(key_tables.get("test_tables") or [])
    rule_tables = list(key_tables.get("rule_tables") or [])
    aa_tables = list(key_tables.get("aa_feature_tables") or [])
    alarm_tables = list(key_tables.get("alarm_tables") or [])
    history_tables = list(key_tables.get("history_case_tables") or [])

    standardized_views = [
        _view("V_UNIT_BASE", [product_index_table], "产品主索引标准化视图", ["ProductUnit", "ProductModel"], deploy=True),
        _view("V_PROCESS_EVENT", [process_table], "过程宽表拆分为站位级过程事件", ["ProcessEvent", "Station", "Equipment", "ToolingCarrier", "MaterialLot"], deploy=True),
        _view("V_TEST_RUN", test_tables, "测试宽表拆分为测试事件", ["TestRun"], deploy=True),
        _view("V_METRIC_RESULT", test_tables, "测试宽表拆分为单指标测项结果", ["MetricResult"], deploy=True),
        _view("V_METRIC_SPEC", rule_tables, "规则表标准化为指标规格定义", ["MetricSpec"], deploy=True),
        _view("V_METRIC_OOS", test_tables + rule_tables, "基于测项结果与规格判断超差事件", ["DefectType"], deploy=True),
        _view("V_ALARM_EVENT", alarm_tables, "报警表标准化为报警事件证据", ["AlarmEvent"], deploy=bool(alarm_tables)),
        _view("V_RECIPE_FEATURE", test_tables + [process_table], "保留测试和过程中的配方特征", ["TestRun"], deploy=False),
        _view("V_AA_FEATURE", aa_tables, "AA 日志标准化为辅助特征", ["AALogFeature"], deploy=bool(aa_tables)),
        _view("V_HISTORY_CASE", history_tables, "历史案例知识视图", ["HistoricalCase"], deploy=bool(history_tables)),
        _view("V_ROOT_CAUSE_PATTERN", history_tables, "根因模式与改善措施知识视图", ["RootCausePattern", "CorrectiveAction"], deploy=bool(history_tables)),
    ]
    standardized_views = [item for item in standardized_views if item.get("source_tables")]

    edge_views = [
        _edge_view("VW_E_PRODUCT_TEST", ["V_UNIT_BASE", "V_TEST_RUN"], "产品到测试事件关系"),
        _edge_view("VW_E_TEST_METRIC", ["V_TEST_RUN", "V_METRIC_RESULT"], "测试事件到测项结果关系"),
        _edge_view("VW_E_METRIC_SPEC", ["V_METRIC_RESULT", "V_METRIC_SPEC"], "测项结果到规格关系"),
        _edge_view("VW_E_PRODUCT_PROCESS", ["V_UNIT_BASE", "V_PROCESS_EVENT"], "产品到过程事件关系"),
        _edge_view("VW_E_PROCESS_RESOURCE", ["V_PROCESS_EVENT"], "过程事件到设备/治具/物料关系"),
        _edge_view("VW_E_DEFECT_KNOWLEDGE", ["V_METRIC_OOS", "V_HISTORY_CASE", "V_ROOT_CAUSE_PATTERN"], "缺陷到历史案例/根因知识关系"),
    ]

    return {
        "raw_layer": {
            "source_tables": list(dict.fromkeys([
                product_index_table,
                process_table,
                *test_tables,
                *rule_tables,
                *aa_tables,
                *alarm_tables,
                *history_tables,
            ])),
            "note": "原始层保留 TAMS 宽表和规则表，不直接入图。",
        },
        "standardized_views": standardized_views,
        "edge_views": edge_views,
        "graph_layer": {
            "vertex_entities": [item.get("entityName") for item in canonical_model.get("entities") or [] if item.get("entityName")],
            "edge_relations": [item.get("relationName") for item in canonical_model.get("relations") or [] if item.get("relationName")],
            "focus_metric_families": list(focus_scope.get("focus_metric_families") or []),
            "focus_stations": list(focus_scope.get("focus_stations") or []),
        },
        "view_dependencies": [
            {"view_name": "V_METRIC_OOS", "depends_on": ["V_METRIC_RESULT", "V_METRIC_SPEC"]},
            {"view_name": "VW_E_PRODUCT_TEST", "depends_on": ["V_UNIT_BASE", "V_TEST_RUN"]},
            {"view_name": "VW_E_PRODUCT_PROCESS", "depends_on": ["V_UNIT_BASE", "V_PROCESS_EVENT"]},
            {"view_name": "VW_E_DEFECT_KNOWLEDGE", "depends_on": ["V_METRIC_OOS", "V_HISTORY_CASE", "V_ROOT_CAUSE_PATTERN"]},
        ],
    }


def _relation(
    source_entity_name: str,
    target_entity_name: str,
    relation_name: str,
    relation_desc: str,
    evidence_tables: List[str],
    *,
    relation_type: str = "ASSOCIATION",
    source_table: str = "",
    target_table: str = "",
) -> Dict[str, Any]:
    evidence_tables = [item for item in evidence_tables if item]
    return {
        "sourceEntityName": source_entity_name,
        "targetEntityName": target_entity_name,
        "relationName": relation_name,
        "relationType": relation_type,
        "relationDesc": relation_desc,
        "evidenceTables": evidence_tables,
        "sourceTable": source_table or (evidence_tables[0] if evidence_tables else ""),
        "targetTable": target_table or (evidence_tables[-1] if evidence_tables else ""),
    }


def _view(view_name: str, source_tables: List[str], purpose: str, entity_targets: List[str], *, deploy: bool) -> Dict[str, Any]:
    return {
        "view_name": view_name,
        "view_kind": "standardized",
        "source_tables": [item for item in source_tables if item],
        "purpose": purpose,
        "entity_targets": entity_targets,
        "deploy": deploy,
        "deploy_reason": "已纳入标准化层正式设计" if deploy else "保留为扩展设计，待后续确认是否部署",
        "sql": None,
    }


def _edge_view(view_name: str, source_views: List[str], purpose: str) -> Dict[str, Any]:
    return {
        "view_name": view_name,
        "source_views": source_views,
        "purpose": purpose,
        "deploy": False,
        "deploy_reason": "阶段 3 先固定边视图骨架，后续 DDL 阶段再补 SQL。",
        "sql": None,
    }
