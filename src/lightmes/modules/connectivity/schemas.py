from pydantic import BaseModel, ConfigDict, Field


class ConnectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    broker_host: str = Field(..., min_length=1, max_length=255)
    broker_port: int = Field(default=1883, ge=1, le=65535)
    username: str | None = None
    password: str | None = None  # 明文，service 层加密
    use_tls: bool = False
    keep_alive_seconds: int = 60
    qos_default: int = Field(default=0, ge=0, le=2)
    clean_session: bool = True
    connect_timeout_seconds: int = 10
    reconnect_delay_seconds: int = 5


class TopicCreate(BaseModel):
    topic_pattern: str = Field(..., min_length=1, max_length=500)
    payload_format: str = "json"  # json / plain / csv / hex
    description: str | None = None
