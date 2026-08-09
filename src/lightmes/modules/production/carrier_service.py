from datetime import datetime

from sqlalchemy.orm import Session

from lightmes.modules.production.models import CarrierBinding, SerialUnit
from lightmes.modules.production.repository import (
    SerialUnitRepository, CarrierBindingRepository,
)
from lightmes.shared.errors import BusinessRuleError, NotFoundError


class CarrierService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.serial_units = SerialUnitRepository(db)
        self.bindings = CarrierBindingRepository(db)

    def bind_first_carrier(
        self, work_order_id: int, carrier_code: str, operator_id: int | None,
    ) -> SerialUnit:
        """首站扫载体码：按顺序取下一个 pending SN 与载体码绑定。

        只绑、不过站：不调 pass_operation、不写 OperationRecord。
        操作员在富主界面手动按 PASS 才过首工序。
        """
        su = self.serial_units.first_pending_by_work_order(work_order_id)
        if su is None:
            raise BusinessRuleError("工单 SN 已全部投产，请选择新工单")
        if self.serial_units.get_active_by_carrier(carrier_code) is not None:
            raise BusinessRuleError(f"载体码已绑定其他产品，请先解绑: {carrier_code}")
        su.carrier_code = carrier_code
        self.bindings.add(CarrierBinding(
            serial_unit_id=su.id, carrier_code=carrier_code, operator_id=operator_id))
        self.db.flush()
        return su

    def unbind(self, scan: str, operator_id: int | None) -> tuple[SerialUnit, str | None]:
        # 权限校验钩子（P2e 预留；后续角色管理模块在此接入）：
        # 目前任何登录用户可解绑，暂不做角色判断。
        su = self.serial_units.get_by_sn(scan)
        if su is None:
            su = self.serial_units.get_active_by_carrier(scan)
        if su is None:
            raise NotFoundError(f"未找到 SN 或载体码: {scan}")
        carrier_code = su.carrier_code
        binding = self.bindings.active_by_serial_unit(su.id)
        if binding is None and carrier_code is None:
            raise BusinessRuleError("该 SN 无活跃载体码绑定")
        if binding is not None:
            binding.unbound_at = datetime.now()
            binding.unbound_reason = "manual"
        su.carrier_code = None
        self.db.flush()
        return su, carrier_code
