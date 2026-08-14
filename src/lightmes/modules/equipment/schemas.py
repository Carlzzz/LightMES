from pydantic import BaseModel


class TagCreate(BaseModel):
    machine_topic_id: int
    name: str
    field_path: str
    signal_type: str
    data_type: str | None = None
    transform: dict | None = None
    unit: str | None = None


class TagUpdate(BaseModel):
    name: str | None = None
    field_path: str | None = None
    signal_type: str | None = None
    data_type: str | None = None
    transform: dict | None = None
    unit: str | None = None
    is_active: bool | None = None
