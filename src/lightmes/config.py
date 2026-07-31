from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "LightMES"
    database_url: str = "postgresql+psycopg://mes:mes@localhost:5432/lightmes"
    mqtt_url: str = "mqtt://localhost:1883"
    secret_key: str = "change-me-in-prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
