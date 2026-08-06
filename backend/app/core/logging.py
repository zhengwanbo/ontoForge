import logging
import logging.config
import sys

from app.core.config import settings


_configured = False


def configure_logging():
    global _configured
    if _configured:
        return

    level = (settings.LOG_LEVEL or "INFO").upper()
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "standard",
                "level": level,
            }
        },
        "root": {
            "handlers": ["console"],
            "level": level,
        },
        "loggers": {
            "uvicorn": {"level": level},
            "uvicorn.error": {"level": level},
            "uvicorn.access": {"level": level},
            "app": {"level": level, "propagate": True},
            "sqlalchemy.engine": {"level": "INFO" if settings.SQL_ECHO else "WARNING"},
            "sqlalchemy.pool": {"level": "INFO" if settings.SQL_ECHO else "WARNING"},
        },
    })
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
