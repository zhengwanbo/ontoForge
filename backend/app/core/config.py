from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "本体构建平台"
    APP_VERSION: str = "1.1.0"
    API_PREFIX: str = "/api/v1"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    SQL_ECHO: bool = True

    # Database - Oracle only
    DATABASE_URL: str = "oracle+oracledb://system:oracle@localhost:1521/FREEPDB1"

    # JWT Auth
    JWT_SECRET_KEY: str = "ontology-platform-secret-key-2026"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 hours

    # CORS
    CORS_ORIGINS: list = ["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"]

    # LLM Default
    LLM_DEFAULT_TIMEOUT: int = 60
    LLM_DEFAULT_MAX_TOKENS: int = 4096
    LLM_DEFAULT_TEMPERATURE: float = 0.7

    class Config:
        env_file = ("backend/.env", ".env")
        case_sensitive = True


settings = Settings()
