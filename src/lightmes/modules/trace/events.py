from dataclasses import dataclass
from lightmes.shared.events import Event


@dataclass
class GenealogyBound(Event):
    parent_sn_id: int
    component_product_id: int
    component_type: str
    component_ref: str  # component_sn 或 component_batch_no


@dataclass
class GenealogyUnbound(Event):
    bind_id: int
    parent_sn_id: int
    reason: str | None


@dataclass
class SerialUnitReworkStarted(Event):
    serial_unit_id: int
    sn: str
    target_seq: int
