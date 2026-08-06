import time
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import settings
from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger("app.sql")

if "sqlite" in settings.DATABASE_URL.lower():
    raise RuntimeError("SQLite 已禁用。请将 DATABASE_URL 配置为 Oracle 连接串。")

engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.SQL_ECHO,
    pool_pre_ping=True,
)


@event.listens_for(engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    context._query_start_time = time.time()
    if settings.SQL_ECHO:
        logger.info("SQL execute: %s", statement)
        logger.debug("SQL params: %s", parameters)


@event.listens_for(engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    elapsed_ms = round((time.time() - getattr(context, "_query_start_time", time.time())) * 1000, 2)
    if settings.SQL_ECHO:
        logger.info("SQL finished: rowcount=%s elapsed_ms=%s", cursor.rowcount, elapsed_ms)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
