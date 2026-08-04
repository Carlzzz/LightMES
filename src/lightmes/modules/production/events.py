from dataclasses import dataclass
from lightmes.shared.events import Event


@dataclass
class StationPassed(Event):
    serial_unit_id: int
    sn: str
    work_order_id: int
    routing_step_id: int
    station_id: int


@dataclass
class SerialUnitFinished(Event):
    serial_unit_id: int
    sn: str
    work_order_id: int
