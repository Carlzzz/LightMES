from sqlalchemy.orm import Session

from lightmes.modules.auth.repository import UserRepository
from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.production.repository import (
    SerialUnitRepository, WorkOrderRepository,
)
from lightmes.modules.production.schemas import (
    StationOpView, StationComponentView, StationView,
)
from lightmes.shared.errors import NotFoundError, BusinessRuleError


class StationService:
    """只读：扫码组装工位作业主界面读模型，不写库。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.query = MasterDataQueryService(db)
        self.skills = SkillService(db)
        self.users = UserRepository(db)
        self.serial_units = SerialUnitRepository(db)
        self.work_orders = WorkOrderRepository(db)

    def load(self, scan: str, work_station_id: int,
             operator_id: int | None) -> StationView:
        # 定位：先按 SN，再按工单号（首件 su=None）
        su = self.serial_units.get_by_sn(scan)
        if su is not None:
            wo = self.work_orders.get(su.work_order_id)
        else:
            wo = self.work_orders.get_by_code(scan)
            if wo is None:
                raise NotFoundError(f"未找到 SN 或工单: {scan}")

        product = self.query.get_product(wo.product_id)
        operations = self.query.get_operations(wo.routing_id)
        if not operations:
            raise BusinessRuleError("工艺路径无工序")

        current_seq = su.current_operation_seq if su is not None else 0
        expected = next((o for o in operations if o.seq > current_seq), None)

        op_views: list[StationOpView] = []
        for o in operations:
            if o.seq <= current_seq:
                st = "done"
            elif expected is not None and o.id == expected.id:
                st = "current"
            else:
                st = "future"
            op_views.append(StationOpView(
                seq=o.seq, name=o.name, code=o.code,
                work_station_id=o.default_work_station_id, status=st))

        current_op = next((v for v in op_views if v.status == "current"), None)

        # 技能预判
        operator_skill_level: int | None = None
        required_level: int | None = None
        skill_ok = True
        is_off_station = False
        components: list[StationComponentView] = []
        if expected is not None:
            is_off_station = expected.default_work_station_id != work_station_id
            if expected.required_skill_id is not None:
                required_level = expected.required_level
                operator_skill_level = (
                    self.skills.get_operator_level(
                        operator_id, expected.required_skill_id)
                    if operator_id else None)
                skill_ok = (operator_skill_level is not None
                            and operator_skill_level >= (required_level or 0))
            for item in self.query.get_active_bom_items(product.id):
                comp = self.query.get_product(item.component_product_id)
                components.append(StationComponentView(
                    component_product_id=item.component_product_id,
                    component_code=comp.code if comp else str(item.component_product_id),
                    component_name=comp.name if comp else "",
                    qty=float(item.qty)))

        operator = self.users.get(operator_id) if operator_id else None
        return StationView(
            sn=su.sn if su is not None else "",
            work_order_code=wo.code,
            product_code=product.code if product else "",
            product_name=product.name if product else "",
            operator_name=operator.display_name if operator else "",
            operator_skill_level=operator_skill_level,
            required_level=required_level,
            skill_ok=skill_ok,
            is_off_station=is_off_station,
            is_finished=expected is None,
            operations=op_views,
            current_op=current_op,
            components=components,
            sop_placeholder=True,
        )
