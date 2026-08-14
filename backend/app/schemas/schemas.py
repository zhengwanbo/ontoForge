from datetime import datetime
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, Field


# ====== 统一响应 ======
class ApiResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: Any = None
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# ====== 认证 ======
class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    display_name: Optional[str] = None
    role: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


# ====== 用户 ======
class UserCreate(BaseModel):
    username: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    password: str
    role: str = "analyst"


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None


class UserResponse(BaseModel):
    user_id: str
    username: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    role: str
    status: str
    created_at: Optional[datetime] = None


# ====== 业务分析域 ======
class DomainCreate(BaseModel):
    domain_name: str
    domain_type: str = "BUSINESS"
    domain_desc: Optional[str] = None


class DomainUpdate(BaseModel):
    domain_name: Optional[str] = None
    domain_type: Optional[str] = None
    domain_desc: Optional[str] = None
    status: Optional[str] = None


class DomainResponse(BaseModel):
    domain_id: str
    domain_name: str
    domain_type: str
    domain_desc: Optional[str] = None
    status: str
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ====== 业务类型语义 ======
class BusinessSemanticPattern(BaseModel):
    pattern_code: str
    pattern_name: str
    description: Optional[str] = None


class BusinessTypeCreate(BaseModel):
    type_code: str
    type_name: str
    semantic_desc: Optional[str] = None
    semantic_patterns: List[BusinessSemanticPattern] = []
    status: str = "ACTIVE"


class BusinessTypeUpdate(BaseModel):
    type_name: Optional[str] = None
    semantic_desc: Optional[str] = None
    semantic_patterns: Optional[List[BusinessSemanticPattern]] = None
    status: Optional[str] = None


class BusinessTypeResponse(BaseModel):
    type_id: str
    type_code: str
    type_name: str
    semantic_desc: Optional[str] = None
    semantic_patterns: List[Dict[str, Any]] = []
    status: str
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ====== 本体实体 ======
class EntityCreate(BaseModel):
    entity_name: str
    entity_display_name: Optional[str] = None
    entity_desc: Optional[str] = None
    build_type: str = "TABLE"
    icon: Optional[str] = None
    color: Optional[str] = None


class EntityUpdate(BaseModel):
    entity_name: Optional[str] = None
    entity_display_name: Optional[str] = None
    entity_desc: Optional[str] = None
    build_type: Optional[str] = None
    status: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    table_name: Optional[str] = None


class EntityResponse(BaseModel):
    entity_id: str
    domain_id: str
    entity_name: str
    entity_display_name: Optional[str] = None
    entity_desc: Optional[str] = None
    build_type: str
    table_name: Optional[str] = None
    status: str
    icon: Optional[str] = None
    color: Optional[str] = None
    graph_position: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    properties: Optional[List] = None


# ====== 本体属性 ======
class PropertyCreate(BaseModel):
    property_name: str
    property_display_name: Optional[str] = None
    data_type: str = "VARCHAR2"
    is_primary_key: str = "N"
    is_nullable: str = "Y"
    property_desc: Optional[str] = None
    order_num: int = 0


class PropertyUpdate(BaseModel):
    property_name: Optional[str] = None
    property_display_name: Optional[str] = None
    data_type: Optional[str] = None
    is_primary_key: Optional[str] = None
    is_nullable: Optional[str] = None
    property_desc: Optional[str] = None
    order_num: Optional[int] = None


class PropertyResponse(BaseModel):
    property_id: str
    entity_id: str
    property_name: str
    property_display_name: Optional[str] = None
    data_type: Optional[str] = None
    is_primary_key: str
    is_nullable: str
    property_desc: Optional[str] = None
    order_num: int
    source_mark: str
    created_at: Optional[datetime] = None
    mapping: Optional[Any] = None


# ====== 本体关系 ======
class RelationCreate(BaseModel):
    source_entity_id: str
    target_entity_id: str
    relation_name: str
    relation_type: str = "ASSOCIATION"
    relation_desc: Optional[str] = None
    relation_table_name: Optional[str] = None


class RelationUpdate(BaseModel):
    source_entity_id: Optional[str] = None
    target_entity_id: Optional[str] = None
    relation_name: Optional[str] = None
    relation_type: Optional[str] = None
    relation_desc: Optional[str] = None
    relation_table_name: Optional[str] = None


class RelationResponse(BaseModel):
    relation_id: str
    domain_id: str
    source_entity_id: str
    target_entity_id: str
    relation_name: str
    relation_type: str
    relation_desc: Optional[str] = None
    relation_table_name: Optional[str] = None
    created_at: Optional[datetime] = None


# ====== 属性映射 ======
class PropertyMappingCreate(BaseModel):
    property_id: str
    entity_id: str
    source_table: Optional[str] = None
    source_column: Optional[str] = None
    mapping_type: str = "DIRECT"
    formula_expr: Optional[str] = None
    formula_desc: Optional[str] = None
    confidence: Optional[str] = None


class PropertyMappingUpdate(BaseModel):
    source_table: Optional[str] = None
    source_column: Optional[str] = None
    mapping_type: Optional[str] = None
    formula_expr: Optional[str] = None
    formula_desc: Optional[str] = None
    confidence: Optional[str] = None
    mapping_status: Optional[str] = None


class PropertyMappingResponse(BaseModel):
    mapping_id: str
    property_id: str
    entity_id: str
    source_table: Optional[str] = None
    source_column: Optional[str] = None
    mapping_type: str
    formula_expr: Optional[str] = None
    formula_desc: Optional[str] = None
    confidence: Optional[str] = None
    mapping_status: str
    mapped_by: Optional[str] = None
    mapped_at: Optional[datetime] = None


# ====== 实体映射 ======
class EntityMappingUpdate(BaseModel):
    build_type: str
    view_sql: Optional[str] = None
    mapping_status: Optional[str] = None


# ====== 关系映射 ======
class RelationMappingCreate(BaseModel):
    relation_id: str
    edge_table_name: Optional[str] = None
    source_table: Optional[str] = None
    target_table: Optional[str] = None
    join_condition: Optional[str] = None
    edge_sql: Optional[str] = None
    mapping_mode: str = "DIRECT"
    relation_table: Optional[str] = None
    relation_source_column: Optional[str] = None
    relation_target_column: Optional[str] = None
    edge_property_columns_json: Optional[str] = None


class RelationMappingUpdate(BaseModel):
    edge_table_name: Optional[str] = None
    source_table: Optional[str] = None
    target_table: Optional[str] = None
    join_condition: Optional[str] = None
    edge_sql: Optional[str] = None
    mapping_status: Optional[str] = None
    mapping_mode: Optional[str] = None
    relation_table: Optional[str] = None
    relation_source_column: Optional[str] = None
    relation_target_column: Optional[str] = None
    edge_property_columns_json: Optional[str] = None


class EdgeSqlPreviewRequest(BaseModel):
    source_id: str
    schema: Optional[str] = None
    edge_sql: str
    sample_limit: int = 5


class RelationJoinAnalyzeRequest(BaseModel):
    source_id: str
    schema: Optional[str] = None
    source_table: Optional[str] = None
    target_table: Optional[str] = None
    join_condition: Optional[str] = None
    max_candidates: int = Field(default=8, ge=1, le=20)


# ====== 分析流程 ======
class ProcessCreate(BaseModel):
    process_name: str
    process_desc: Optional[str] = None
    process_json: str  # JSON string
    version: str = "1.0"


class ProcessUpdate(BaseModel):
    process_name: Optional[str] = None
    process_desc: Optional[str] = None
    process_json: Optional[str] = None
    version: Optional[str] = None
    status: Optional[str] = None


class ProcessResponse(BaseModel):
    process_id: str
    domain_id: str
    process_name: str
    process_desc: Optional[str] = None
    process_json: Optional[str] = None
    version: str
    status: str
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ProcessGuideGenerateRequest(BaseModel):
    process_type: str = "DATA_ANALYSIS"  # DATA_ANALYSIS / BUSINESS_PROCESS / CUSTOM
    process_description: str
    model_config_id: Optional[str] = None


# ====== 智能体技能 ======
class AgentSkillCreate(BaseModel):
    llm_config_id: str
    process_id: str
    source_id: str
    property_graph_name: str
    skill_name: str
    skill_desc: Optional[str] = None
    analysis_goal: Optional[str] = None
    execution_rules: Optional[str] = None
    output_requirements: Optional[str] = None
    status: str = "ACTIVE"


class AgentSkillUpdate(BaseModel):
    llm_config_id: Optional[str] = None
    process_id: Optional[str] = None
    source_id: Optional[str] = None
    property_graph_name: Optional[str] = None
    skill_name: Optional[str] = None
    skill_desc: Optional[str] = None
    analysis_goal: Optional[str] = None
    execution_rules: Optional[str] = None
    output_requirements: Optional[str] = None
    status: Optional[str] = None


class AgentSkillResponse(BaseModel):
    skill_id: str
    domain_id: str
    domain_name: Optional[str] = None
    llm_config_id: Optional[str] = None
    llm_config_name: Optional[str] = None
    llm_model_name: Optional[str] = None
    process_id: str
    process_name: Optional[str] = None
    entity_id: Optional[str] = None
    entity_name: Optional[str] = None
    entity_display_name: Optional[str] = None
    source_id: Optional[str] = None
    source_name: Optional[str] = None
    property_graph_name: Optional[str] = None
    skill_name: str
    skill_desc: Optional[str] = None
    analysis_goal: Optional[str] = None
    execution_rules: Optional[str] = None
    output_requirements: Optional[str] = None
    prompt_template: Optional[str] = None
    context_json: Optional[str] = None
    status: str
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AgentSkillTestRequest(BaseModel):
    llm_config_id: str
    source_id: str
    schema: Optional[str] = None
    graph_table: Optional[str] = None
    test_question: Optional[str] = None
    conversation_history: List[Dict[str, str]] = Field(default_factory=list)
    session_id: Optional[str] = None
    start_session: bool = False
    sample_limit: int = Field(default=100, ge=1, le=100)


# ====== 业务规则与活动 ======
class BusinessActivityCreate(BaseModel):
    activity_name: str
    activity_type: str
    activity_desc: Optional[str] = None
    process_id: Optional[str] = None
    config_json: Optional[str] = None
    status: str = "ACTIVE"


class BusinessActivityUpdate(BusinessActivityCreate):
    pass


class BusinessRuleCreate(BaseModel):
    rule_name: str
    rule_category: str = "VALIDATION"
    rule_desc: Optional[str] = None
    trigger_event: str = "DATA_CHANGED"
    scope_entity_id: Optional[str] = None
    scope_relation_id: Optional[str] = None
    condition_json: Optional[str] = None
    activity_id: Optional[str] = None
    priority: int = 50
    status: str = "DRAFT"


class BusinessRuleUpdate(BusinessRuleCreate):
    pass


# ====== LLM配置 ======
class LLMConfigCreate(BaseModel):
    config_name: str
    api_base_url: str
    api_key: str
    model_name: str
    temperature: float = 0.7
    max_tokens: int = 4096
    context_window_tokens: Optional[int] = None
    timeout: int = 60
    is_default: bool = False


class LLMConfigUpdate(BaseModel):
    config_name: Optional[str] = None
    api_base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    context_window_tokens: Optional[int] = None
    timeout: Optional[int] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


class LLMConfigResponse(BaseModel):
    config_id: str
    config_name: str
    api_base_url: str
    api_key_display: str  # masked
    model_name: str
    temperature: float
    max_tokens: int
    context_window_tokens: Optional[int] = None
    timeout: int
    is_active: str
    is_default: str
    created_at: Optional[datetime] = None


# ====== DDL ======
class DDLGenerateRequest(BaseModel):
    domain_id: str


class DDLExecuteRequest(BaseModel):
    target_source_id: str
    ddl_content: str
    execute_mode: str = "all"  # all / step_by_step
    skip_existing: bool = False


class DDLLogResponse(BaseModel):
    log_id: str
    domain_id: Optional[str] = None
    ddl_content: Optional[str] = None
    execution_result: str
    error_message: Optional[str] = None
    executed_by: Optional[str] = None
    executed_at: Optional[datetime] = None
    execution_duration: Optional[float] = None


class DDLStatementLogResponse(BaseModel):
    sequence_no: int
    statement: str
    status: str
    object_type: Optional[str] = None
    object_name: Optional[str] = None
    message: Optional[str] = None
    error_message: Optional[str] = None


class GraphQueryRequest(BaseModel):
    domain_id: str
    source_id: str
    schema: Optional[str] = None
    graph_sql: str
    row_limit: int = Field(default=200, ge=1, le=1000)


class GraphInstanceQueryRequest(BaseModel):
    domain_id: str
    source_id: str
    graph_name: str
    node_id: str
    property_name: Optional[str] = None
    operator: str = "contains"  # equals / contains / greater_than / less_than
    value: Optional[str] = None
    row_limit: int = Field(default=50, ge=1, le=100)


class GraphInstanceLineageRequest(BaseModel):
    domain_id: str
    source_id: str
    graph_name: str
    node_id: str
    instance_key: str
    max_depth: int = Field(default=12, ge=1, le=20)


# ====== 源数据 ======
class SourceTableResponse(BaseModel):
    table_name: str
    comments: Optional[str] = None
    num_rows: Optional[int] = None


class SourceColumnResponse(BaseModel):
    column_name: str
    data_type: str
    nullable: str
    default_value: Optional[str] = None
    column_id: int
    comments: Optional[str] = None


class CommentsUpdateRequest(BaseModel):
    comments: str


class SourceDataQueryRequest(BaseModel):
    conditions: Optional[List[dict]] = None
    select_columns: Optional[List[str]] = None
    order_by: Optional[str] = None
    page: int = 1
    page_size: int = 100


class SourceDataResponse(BaseModel):
    columns: List[str]
    rows: List[dict]
    total: int
    page: int
    page_size: int


class DataObjectCommentGenerateRequest(BaseModel):
    schema: Optional[str] = None
    sample_limit: int = 5
    primary_model_config_id: Optional[str] = None
    verifier_model_config_id: Optional[str] = None


class DataObjectColumnCommentItem(BaseModel):
    column_name: str
    comments: str = ""


class DataObjectCommentSaveRequest(BaseModel):
    schema: Optional[str] = None
    table_comment: Optional[str] = None
    column_comments: List[DataObjectColumnCommentItem] = []


# ====== 本体 Guide 生成 ======
class OntologyGuideTableBinding(BaseModel):
    table_name: str
    source_role: Optional[str] = None


class OntologyGuideDDLColumn(BaseModel):
    column_name: str
    data_type: Optional[str] = None
    nullable: Optional[str] = None
    comments: Optional[str] = None
    is_primary_key: Optional[str] = None
    column_id: Optional[int] = None


class OntologyGuideDDLTable(BaseModel):
    owner: Optional[str] = None
    table_name: str
    table_comment: Optional[str] = None
    columns: List[OntologyGuideDDLColumn] = []


class OntologyGuideRuleDataset(BaseModel):
    rule_type: str
    table_name: str
    record_count: int = 0
    columns: List[str] = []
    records: List[dict] = []
    summary: dict = {}


class OntologyGuideGenerateRequest(BaseModel):
    source_id: Optional[str] = None
    schema: Optional[str] = None
    table_source_mode: str = "database"
    generation_strategy: Optional[str] = "structured_domain_pipeline"
    business_scenario: Optional[str] = None
    semantic_type_code: Optional[str] = None
    relation_tables: List[str]
    rule_table_name: Optional[str] = None
    table_bindings: List[OntologyGuideTableBinding] = []
    ddl_tables: List[OntologyGuideDDLTable] = []
    rule_datasets: List[OntologyGuideRuleDataset] = []
    focus_metric_families: List[str] = []
    focus_stations: List[str] = []
    history_case_sources: List[str] = []
    enabled_patterns: List[str] = []
    business_document: str
    model_config_id: Optional[str] = None
    sample_limit: int = 3
    auto_apply: bool = False
    overwrite_existing: bool = False


class OntologyGuideApplyRequest(BaseModel):
    blueprint_id: Optional[str] = None
    blueprint: dict
    overwrite_existing: bool = False


class OntologyNaturalAdjustRequest(BaseModel):
    instruction: str
    selected_entity_id: Optional[str] = None
    model_config_id: Optional[str] = None
    auto_apply: bool = False


class OntologyNaturalAdjustApplyRequest(BaseModel):
    plan: dict


# ====== LLM映射 ======
class AutoMappingRequest(BaseModel):
    entity_id: str
    domain_id: str
    source_id: Optional[str] = None
    schema: Optional[str] = None
    sample_limit: int = 3
    mapping_instruction: Optional[str] = None
    model_config_id: Optional[str] = None


class BulkAutoMappingRequest(BaseModel):
    source_id: str
    schema: Optional[str] = None
    model_config_id: Optional[str] = None
    sample_limit: int = 3
    mapping_instruction: Optional[str] = None
    auto_apply: bool = False


class BulkMappingApplyItem(BaseModel):
    entity_id: str
    mappings: List[dict]
    build_type: Optional[str] = None
    table_name: Optional[str] = None
    view_sql: Optional[str] = None


class BulkRelationMappingApplyItem(BaseModel):
    relation_id: str
    edge_table_name: Optional[str] = None
    source_table: Optional[str] = None
    target_table: Optional[str] = None
    join_condition: Optional[str] = None
    edge_sql: Optional[str] = None


class BulkMappingApplyRequest(BaseModel):
    entities: List[BulkMappingApplyItem] = Field(default_factory=list)
    relations: List[BulkRelationMappingApplyItem] = Field(default_factory=list)


class MappingConfirmRequest(BaseModel):
    mappings: List[dict]  # [{mapping_id, action: accept/modify/reject, ...}]


# ====== 数据源配置 ======
class DataSourceCreate(BaseModel):
    source_name: str
    source_desc: Optional[str] = None
    db_type: str = "oracle"
    host: str
    port: int = 1521
    service_name: Optional[str] = None
    sid: Optional[str] = None
    username: str
    password: str
    schema_name: Optional[str] = None
    business_domain_id: Optional[str] = None
    is_default: bool = False


class DataSourceUpdate(BaseModel):
    source_name: Optional[str] = None
    source_desc: Optional[str] = None
    db_type: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    service_name: Optional[str] = None
    sid: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    schema_name: Optional[str] = None
    business_domain_id: Optional[str] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


class DataSourceResponse(BaseModel):
    source_id: str
    source_name: str
    source_desc: Optional[str] = None
    db_type: str
    host: str
    port: int
    service_name: Optional[str] = None
    sid: Optional[str] = None
    username: str
    schema_name: Optional[str] = None
    business_domain_id: Optional[str] = None
    business_domain_name: Optional[str] = None
    is_active: str
    is_default: str
    connection_status: str
    last_test_time: Optional[datetime] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
