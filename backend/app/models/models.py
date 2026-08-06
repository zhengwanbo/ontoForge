from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, Text, DateTime, Boolean, ForeignKey, CHAR
from sqlalchemy.orm import relationship
from app.core.database import Base


def generate_id(prefix: str) -> str:
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class SysDomain(Base):
    __tablename__ = "sys_domain"

    domain_id = Column(String(50), primary_key=True, default=lambda: generate_id("dm"))
    domain_name = Column(String(200), nullable=False)
    domain_type = Column(String(50), default="BUSINESS")
    domain_desc = Column(String(1000))
    status = Column(String(20), default="ACTIVE")
    created_by = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    entities = relationship("SysOntologyEntity", back_populates="domain", cascade="all, delete-orphan")
    relations = relationship("SysOntologyRelation", back_populates="domain", cascade="all, delete-orphan")
    processes = relationship("SysProcessDef", back_populates="domain", cascade="all, delete-orphan")
    data_sources = relationship("SysDataSource", back_populates="business_domain")


class SysOntologyEntity(Base):
    __tablename__ = "sys_ontology_entity"

    entity_id = Column(String(50), primary_key=True, default=lambda: generate_id("ent"))
    domain_id = Column(String(50), ForeignKey("sys_domain.domain_id"), nullable=False)
    entity_name = Column(String(100), nullable=False)
    entity_display_name = Column(String(200))
    entity_desc = Column(String(1000))
    build_type = Column(String(20), default="TABLE")  # VIEW / TABLE
    table_name = Column(String(100))
    status = Column(String(20), default="DRAFT")  # DRAFT/MAPPED/DDL_GENERATED/DEPLOYED
    icon = Column(String(50))
    color = Column(String(20))
    graph_position = Column(Text)  # JSON
    created_by = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    domain = relationship("SysDomain", back_populates="entities")
    properties = relationship("SysOntologyProperty", back_populates="entity", cascade="all, delete-orphan")
    entity_mapping = relationship("SysEntityMapping", back_populates="entity", uselist=False, cascade="all, delete-orphan")


class SysOntologyProperty(Base):
    __tablename__ = "sys_ontology_property"

    property_id = Column(String(50), primary_key=True, default=lambda: generate_id("prop"))
    entity_id = Column(String(50), ForeignKey("sys_ontology_entity.entity_id"), nullable=False)
    property_name = Column(String(100), nullable=False)
    property_display_name = Column(String(200))
    data_type = Column(String(50))
    is_primary_key = Column(CHAR(1), default="N")
    is_nullable = Column(CHAR(1), default="Y")
    property_desc = Column(String(500))
    order_num = Column(Integer, default=0)
    source_mark = Column(String(20), default="PENDING")  # PENDING/MAPPED
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    entity = relationship("SysOntologyEntity", back_populates="properties")
    mapping = relationship("SysPropertyMapping", back_populates="property", uselist=False, cascade="all, delete-orphan")


class SysOntologyRelation(Base):
    __tablename__ = "sys_ontology_relation"

    relation_id = Column(String(50), primary_key=True, default=lambda: generate_id("rel"))
    domain_id = Column(String(50), ForeignKey("sys_domain.domain_id"), nullable=False)
    source_entity_id = Column(String(50), ForeignKey("sys_ontology_entity.entity_id"), nullable=False)
    target_entity_id = Column(String(50), ForeignKey("sys_ontology_entity.entity_id"), nullable=False)
    relation_name = Column(String(100), nullable=False)
    relation_type = Column(String(50), nullable=False)  # ONE_TO_ONE/ONE_TO_MANY/MANY_TO_MANY/INHERITANCE/ASSOCIATION
    relation_desc = Column(String(1000))
    relation_table_name = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    domain = relationship("SysDomain", back_populates="relations")
    source_entity = relationship("SysOntologyEntity", foreign_keys=[source_entity_id])
    target_entity = relationship("SysOntologyEntity", foreign_keys=[target_entity_id])
    relation_mapping = relationship("SysRelationMapping", back_populates="relation", uselist=False, cascade="all, delete-orphan")


class SysEntityMapping(Base):
    __tablename__ = "sys_entity_mapping"

    mapping_id = Column(String(50), primary_key=True, default=lambda: generate_id("emap"))
    entity_id = Column(String(50), ForeignKey("sys_ontology_entity.entity_id"), nullable=False)
    build_type = Column(String(20))  # VIEW / TABLE
    view_sql = Column(Text)  # For VIEW type
    mapping_status = Column(String(20), default="PENDING")  # PENDING/IN_PROGRESS/CONFIRMED
    mapped_by = Column(String(50))
    mapped_at = Column(DateTime)

    entity = relationship("SysOntologyEntity", back_populates="entity_mapping")


class SysPropertyMapping(Base):
    __tablename__ = "sys_property_mapping"

    mapping_id = Column(String(50), primary_key=True, default=lambda: generate_id("pmap"))
    property_id = Column(String(50), ForeignKey("sys_ontology_property.property_id"), nullable=False)
    source_table = Column(String(100))
    source_column = Column(String(100))
    mapping_type = Column(String(20), default="DIRECT")  # DIRECT/COMPUTED/CONSTANT/LLM_DERIVED
    formula_expr = Column(String(2000))
    formula_desc = Column(String(1000))
    confidence = Column(String(10))  # HIGH/MEDIUM/LOW
    mapping_status = Column(String(20), default="PENDING")  # PENDING/CONFIRMED/REJECTED
    mapped_by = Column(String(50))
    mapped_at = Column(DateTime)

    property = relationship("SysOntologyProperty", back_populates="mapping")


class SysRelationMapping(Base):
    __tablename__ = "sys_relation_mapping"

    mapping_id = Column(String(50), primary_key=True, default=lambda: generate_id("rmap"))
    relation_id = Column(String(50), ForeignKey("sys_ontology_relation.relation_id"), nullable=False)
    source_table = Column(String(100))
    target_table = Column(String(100))
    join_condition = Column(String(500))
    edge_sql = Column(Text)
    mapping_mode = Column(String(30), default="DIRECT")  # DIRECT / RELATION_TABLE
    relation_table = Column(String(100))
    relation_source_column = Column(String(100))
    relation_target_column = Column(String(100))
    edge_property_columns_json = Column(Text)
    mapping_status = Column(String(20), default="PENDING")
    mapped_by = Column(String(50))
    mapped_at = Column(DateTime)

    relation = relationship("SysOntologyRelation", back_populates="relation_mapping")


class SysProcessDef(Base):
    __tablename__ = "sys_process_def"

    process_id = Column(String(50), primary_key=True, default=lambda: generate_id("proc"))
    domain_id = Column(String(50), ForeignKey("sys_domain.domain_id"), nullable=False)
    process_name = Column(String(200), nullable=False)
    process_desc = Column(String(1000))
    process_json = Column(Text)  # JSON string
    version = Column(String(20), default="1.0")
    status = Column(String(20), default="DRAFT")  # DRAFT/PUBLISHED
    created_by = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    domain = relationship("SysDomain", back_populates="processes")


class SysAgentSkill(Base):
    __tablename__ = "sys_agent_skill"

    skill_id = Column(String(50), primary_key=True, default=lambda: generate_id("skill"))
    domain_id = Column(String(50), ForeignKey("sys_domain.domain_id"), nullable=False)
    llm_config_id = Column(String(50))
    process_id = Column(String(50), nullable=False)
    entity_id = Column(String(50), nullable=False)
    source_id = Column(String(50))
    property_graph_name = Column(String(128))
    skill_name = Column(String(200), nullable=False)
    skill_desc = Column(String(1000))
    analysis_goal = Column(String(1000))
    execution_rules = Column(Text)
    output_requirements = Column(Text)
    prompt_template = Column(Text)
    context_json = Column(Text)
    status = Column(String(20), default="ACTIVE")  # DRAFT/ACTIVE/INACTIVE
    created_by = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SysBusinessActivity(Base):
    """业务规则触发后的可配置活动。"""
    __tablename__ = "sys_business_activity"

    activity_id = Column(String(50), primary_key=True, default=lambda: generate_id("act"))
    domain_id = Column(String(50), ForeignKey("sys_domain.domain_id"), nullable=False)
    activity_name = Column(String(200), nullable=False)
    activity_type = Column(String(30), nullable=False)  # NOTIFY / CREATE_TASK / CALL_PROCESS / DATA_ACTION / MANUAL_REVIEW
    activity_desc = Column(String(1000))
    process_id = Column(String(50))  # 可选，引用 SysProcessDef；不设外键以允许流程独立演进
    config_json = Column(Text)  # 通知对象、任务参数、接口/数据更新参数等
    status = Column(String(20), default="ACTIVE")
    created_by = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SysBusinessRule(Base):
    """基于本体对象、关系和属性定义的业务规则。"""
    __tablename__ = "sys_business_rule"

    rule_id = Column(String(50), primary_key=True, default=lambda: generate_id("rule"))
    domain_id = Column(String(50), ForeignKey("sys_domain.domain_id"), nullable=False)
    rule_name = Column(String(200), nullable=False)
    rule_category = Column(String(30), default="VALIDATION")  # VALIDATION / DECISION / DERIVATION / ALERT
    rule_desc = Column(String(1000))
    trigger_event = Column(String(50), default="DATA_CHANGED")  # DATA_CREATED / DATA_CHANGED / FLOW_NODE_COMPLETED / MANUAL
    scope_entity_id = Column(String(50))
    scope_relation_id = Column(String(50))
    condition_json = Column(Text)  # 规则条件：属性、操作符、阈值、组合逻辑
    activity_id = Column(String(50))  # 触发的业务活动
    priority = Column(Integer, default=50)
    status = Column(String(20), default="DRAFT")
    created_by = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SysLLMConfig(Base):
    __tablename__ = "sys_llm_config"

    config_id = Column(String(50), primary_key=True, default=lambda: generate_id("llm"))
    config_name = Column(String(100), nullable=False)
    api_base_url = Column(String(500), nullable=False)
    api_key_enc = Column(String(500), nullable=False)
    model_name = Column(String(100), nullable=False)
    temperature = Column(Float, default=0.7)
    max_tokens = Column(Integer, default=4096)
    context_window_tokens = Column(Integer)
    timeout = Column(Integer, default=60)
    is_active = Column(CHAR(1), default="Y")
    is_default = Column(CHAR(1), default="N")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SysOntologyBlueprint(Base):
    __tablename__ = "sys_ontology_blueprint"

    blueprint_id = Column(String(50), primary_key=True, default=lambda: generate_id("bp"))
    domain_id = Column(String(50), nullable=False)
    source_id = Column(String(50))
    schema_name = Column(String(100))
    version_no = Column(Integer, default=1)
    status = Column(String(20), default="GENERATED")  # GENERATED/APPLIED/ARCHIVED
    blueprint_json = Column(Text)
    summary_json = Column(Text)
    created_by = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SysUser(Base):
    __tablename__ = "sys_user"

    user_id = Column(String(50), primary_key=True, default=lambda: generate_id("usr"))
    username = Column(String(50), unique=True, nullable=False)
    display_name = Column(String(100))
    email = Column(String(200))
    password_hash = Column(String(200), nullable=False)
    role = Column(String(20), default="analyst")  # admin/analyst/viewer
    status = Column(String(20), default="ACTIVE")  # ACTIVE/INACTIVE
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SysDDLLog(Base):
    __tablename__ = "sys_ddl_log"

    log_id = Column(String(50), primary_key=True, default=lambda: generate_id("ddl"))
    domain_id = Column(String(50))
    ddl_content = Column(Text)
    execution_result = Column(String(20))  # SUCCESS/FAILED
    error_message = Column(Text)
    executed_by = Column(String(50))
    executed_at = Column(DateTime, default=datetime.utcnow)
    execution_duration = Column(Float)  # seconds


class SysDDLStatementLog(Base):
    """One persisted result for each statement in a DDL execution."""
    __tablename__ = "sys_ddl_statement_log"

    statement_log_id = Column(String(50), primary_key=True, default=lambda: generate_id("ddls"))
    log_id = Column(String(50), ForeignKey("sys_ddl_log.log_id"), nullable=False, index=True)
    sequence_no = Column(Integer, nullable=False)
    statement = Column(Text, nullable=False)
    status = Column(String(20), nullable=False)  # success / failed / skipped
    object_type = Column(String(100))
    object_name = Column(String(200))
    message = Column(Text)
    error_message = Column(Text)


class SysOperationLog(Base):
    __tablename__ = "sys_operation_log"

    log_id = Column(String(50), primary_key=True, default=lambda: generate_id("op"))
    operator = Column(String(50))
    operation_type = Column(String(50))
    operation_target = Column(String(200))
    operation_detail = Column(Text)
    before_value = Column(Text)
    after_value = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class SysDataSource(Base):
    """Oracle 26ai 数据源配置"""
    __tablename__ = "sys_data_source"

    source_id = Column(String(50), primary_key=True, default=lambda: generate_id("ds"))
    source_name = Column(String(100), nullable=False)          # 配置名称，如"生产环境Oracle"
    source_desc = Column(String(500))                          # 描述
    db_type = Column(String(50), default="oracle")             # 数据库类型（oracle/mysql/pg等）
    host = Column(String(200), nullable=False)                 # 主机地址
    port = Column(Integer, default=1521)                       # 端口
    service_name = Column(String(200))                         # Oracle Service Name
    sid = Column(String(100))                                  # Oracle SID
    username = Column(String(100), nullable=False)             # 用户名
    password_enc = Column(String(500), nullable=False)         # 加密密码
    schema_name = Column(String(100))                          # Schema名称
    business_domain_id = Column(String(50), ForeignKey("sys_domain.domain_id"))
    is_active = Column(CHAR(1), default="Y")                   # 是否启用
    is_default = Column(CHAR(1), default="N")                  # 是否默认数据源
    connection_status = Column(String(20), default="UNKNOWN")  # CONNECTED/DISCONNECTED/UNKNOWN
    last_test_time = Column(DateTime)                          # 最后测试时间
    created_by = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    business_domain = relationship("SysDomain", back_populates="data_sources")


class SysMappingTask(Base):
    __tablename__ = "sys_mapping_task"

    task_id = Column(String(50), primary_key=True, default=lambda: generate_id("mtask"))
    domain_id = Column(String(50))
    source_id = Column(String(50))
    model_config_id = Column(String(50))
    task_type = Column(String(30), default="BULK_GENERATE")  # BULK_GENERATE/BULK_APPLY/ENTITY_RERUN
    status = Column(String(20), default="SUCCESS")  # SUCCESS/FAILED
    request_json = Column(Text)
    result_json = Column(Text)
    summary_json = Column(Text)
    created_by = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
