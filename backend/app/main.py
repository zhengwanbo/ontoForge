from fastapi import FastAPI
from starlette.requests import Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from app.core.config import settings
from app.core.logging import get_logger
from app.core.database import engine, Base
from app.api.domains import router as domains_router
from app.api.ontology import router as ontology_router
from app.api.mapping import router as mapping_router
from app.api.ddl import router as ddl_router
from app.api.ontology_browse import router as ontology_browse_router
from app.api.source_data import router as source_data_router
from app.api.system import router as system_router
from app.api.process import router as process_router
from app.api.datasource import router as datasource_router
from app.api.business_rules import router as business_rules_router
from app.api.agent import router as agent_router
from app.schemas.schemas import ApiResponse

logger = get_logger(__name__)

# Create tables
Base.metadata.create_all(bind=engine)


def ensure_agent_skill_columns():
    inspector = inspect(engine)
    try:
        columns = {item["name"].lower() for item in inspector.get_columns("sys_agent_skill")}
    except Exception:
        columns = set()
    if not columns:
        return

    with engine.begin() as connection:
        if "llm_config_id" not in columns:
            connection.execute(text("ALTER TABLE sys_agent_skill ADD llm_config_id VARCHAR2(50)"))
        if "source_id" not in columns:
            connection.execute(text("ALTER TABLE sys_agent_skill ADD source_id VARCHAR2(50)"))
        if "property_graph_name" not in columns:
            connection.execute(text("ALTER TABLE sys_agent_skill ADD property_graph_name VARCHAR2(128)"))


ensure_agent_skill_columns()


def ensure_llm_config_columns():
    inspector = inspect(engine)
    try:
        columns = {item["name"].lower() for item in inspector.get_columns("sys_llm_config")}
    except Exception:
        columns = set()
    if not columns:
        return

    with engine.begin() as connection:
        if "context_window_tokens" not in columns:
            connection.execute(text("ALTER TABLE sys_llm_config ADD context_window_tokens NUMBER(10)"))


ensure_llm_config_columns()


def ensure_relation_mapping_columns():
    inspector = inspect(engine)
    try:
        columns = {item["name"].lower() for item in inspector.get_columns("sys_relation_mapping")}
    except Exception:
        columns = set()
    if not columns:
        return
    additions = {
        "mapping_mode": "VARCHAR2(30)",
        "relation_table": "VARCHAR2(100)",
        "relation_source_column": "VARCHAR2(100)",
        "relation_target_column": "VARCHAR2(100)",
        "edge_property_columns_json": "CLOB",
    }
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE sys_relation_mapping ADD {name} {definition}"))


ensure_relation_mapping_columns()

# Check if admin user exists, create if not
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.models import SysUser
db_session = SessionLocal()
admin_user = db_session.query(SysUser).filter(SysUser.username == "admin").first()
if not admin_user:
    admin_user = SysUser(
        user_id="usr_admin_default",
        username="admin",
        display_name="系统管理员",
        email="admin@ontology-platform.com",
        password_hash=get_password_hash("Welcome##131"),
        role="admin",
        status="ACTIVE"
    )
    db_session.add(admin_user)
    db_session.commit()
db_session.close()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="本体构建平台API",
    openapi_url=f"{settings.API_PREFIX}/openapi.json"
)

logger.info(
    "Application bootstrapped: app=%s version=%s log_level=%s sql_echo=%s debug=%s",
    settings.APP_NAME,
    settings.APP_VERSION,
    settings.LOG_LEVEL,
    settings.SQL_ECHO,
    settings.DEBUG,
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    logger.info("HTTP request started: %s %s", request.method, request.url.path)
    response = await call_next(request)
    logger.info(
        "HTTP request finished: %s %s -> %s",
        request.method,
        request.url.path,
        response.status_code,
    )
    return response

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers with API prefix
app.include_router(domains_router, prefix=settings.API_PREFIX)
app.include_router(ontology_router, prefix=settings.API_PREFIX)
app.include_router(mapping_router, prefix=settings.API_PREFIX)
app.include_router(ddl_router, prefix=settings.API_PREFIX)
app.include_router(ontology_browse_router, prefix=settings.API_PREFIX)
app.include_router(source_data_router, prefix=settings.API_PREFIX)
app.include_router(system_router, prefix=settings.API_PREFIX)
app.include_router(process_router, prefix=settings.API_PREFIX)
app.include_router(datasource_router, prefix=settings.API_PREFIX)
app.include_router(business_rules_router, prefix=settings.API_PREFIX)
app.include_router(agent_router, prefix=settings.API_PREFIX)


@app.get("/", response_model=ApiResponse)
async def root():
    logger.debug("Root health endpoint requested")
    return ApiResponse(data={"name": settings.APP_NAME, "version": settings.APP_VERSION, "status": "running"})


@app.get("/health", response_model=ApiResponse)
async def health_check():
    logger.debug("Health endpoint requested")
    return ApiResponse(data={"status": "healthy"})
