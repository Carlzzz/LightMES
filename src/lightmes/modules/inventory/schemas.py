from datetime import datetime
from pydantic import BaseModel, ConfigDict


class MaterialLotCreate(BaseModel):
    code: str
    product_id: int
    quantity: float
    supplier_lot: str | None = None


class MaterialLotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    product_id: int
    quantity: float
    available_quantity: float
    status: str
    supplier_lot: str | None
    received_at: datetime | None
