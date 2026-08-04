from pydantic import BaseModel


class ComponentBind(BaseModel):
    component_product_id: int
    component_sn: str | None = None
    component_batch_no: str | None = None
    qty: float = 1
