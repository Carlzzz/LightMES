from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "LightMES"
    database_url: str = "sqlite:///./lightmes.db"
    test_database_url: str = ""
    mqtt_url: str = "mqtt://localhost:1883"
    secret_key: str = "change-me-in-prod"
    environment: str = "development"
    admin_initial_password: str = ""
    max_import_bytes: int = 10 * 1024 * 1024
    max_import_rows: int = 100_000
    allowed_hosts: list[str] = ["*"]
    session_max_age_seconds: int = 14 * 24 * 60 * 60
    login_rate_limit: int = 5
    api_rate_limit: int = 300
    rate_limit_window_seconds: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
