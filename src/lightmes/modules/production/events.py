from dataclasses import dataclass
from lightmes.shared.events import Event


@dataclass
class OperationPassed(Event):
    serial_unit_id: int
    sn: str
    work_order_id: int
    operation_id: int
    work_station_id: int
    line_id: int


@dataclass
class SerialUnitFinished(Event):
    serial_unit_id: int
    sn: str
    work_order_id: int
