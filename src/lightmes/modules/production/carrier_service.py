from sqlalchemy.orm import Session

from lightmes.modules.production.models import CarrierBinding, SerialUnit
from lightmes.modules.production.repository import (
    SerialUnitRepository, WorkOrderRepository, CarrierBindingRepository,
)
from lightmes.modules.production.schemas import OperationPassInput, OperationPassResult
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.shared.errors import BusinessRuleError, NotFoundError


class CarrierService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.serial_units = SerialUnitRepository(db)
        self.work_orders = WorkOrderRepository(db)
        self.bindings = CarrierBindingRepository(db)

    def bind_and_pass_first(
        self, work_order_id: int, carrier_code: str, work_station_id: int,
        operator_id: int | None, components=None, params=None,
    ) -> OperationPassResult:
        su = self.serial_units.first_pending_by_work_order(work_order_id)
        if su is None:
            raise BusinessRuleError("工单 SN 已全部投产，请选择新工单")
        if self.serial_units.get_active_by_carrier(carrier_code) is not None:
            raise BusinessRuleError(f"载体码已绑定其他产品，请先解绑: {carrier_code}")
        su.carrier_code = carrier_code
        self.bindings.add(CarrierBinding(
            serial_unit_id=su.id, carrier_code=carrier_code, operator_id=operator_id))
        # 过首工序（pass_operation 内 pending→in_process）
        return OperationPassService(self.db).pass_operation(OperationPassInput(
            work_station_id=work_station_id, sn=su.sn, operator_id=operator_id,
            components=components or [], params=params or []))

    def unbind(self, scan: str, operator_id: int | None) -> SerialUnit:
        # 权限校验钩子（P2e 预留；后续角色管理模块在此接入）：
        # 目前任何登录用户可解绑，暂不做角色判断。
        su = self.serial_units.get_by_sn(scan)
        if su is None:
            su = self.serial_units.get_active_by_carrier(scan)
        if su is None:
            raise NotFoundError(f"未找到 SN 或载体码: {scan}")
        binding = self.bindings.active_by_serial_unit(su.id)
        if binding is not None:
            from datetime import datetime
            binding.unbound_at = datetime.now()
        su.carrier_code = None
        self.db.flush()
        return su
