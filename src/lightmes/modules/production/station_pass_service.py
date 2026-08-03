from sqlalchemy import update
from sqlalchemy.orm import Session

from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.production.models import SerialUnit, StationPass
from lightmes.modules.production.repository import (
    SerialUnitRepository, StationPassRepository, SnRuleRepository,
    WorkOrderRepository,
)
from lightmes.modules.production.schemas import (
    StationPassInput, StationPassResult, StepInfo,
)
from lightmes.modules.production.sn_generator import SnGenerator
from lightmes.shared.errors import NotFoundError, BusinessRuleError, ConflictError


class StationPassService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.query = MasterDataQueryService(db)
        self.serial_units = SerialUnitRepository(db)
        self.passes = StationPassRepository(db)
        self.work_orders = WorkOrderRepository(db)
        self.sn_rules = SnRuleRepository(db)
        self.sn_gen = SnGenerator(db)

    def pass_station(self, data: StationPassInput) -> StationPassResult:
        # 1+3. 定位工单与 SN
        if data.sn is not None:
            su = self.serial_units.get_by_sn(data.sn)
            if su is None:
                raise NotFoundError(f"SN 不存在: {data.sn}")
            if su.status in ("finished", "scrapped"):
                raise BusinessRuleError(f"SN 已{su.status}，不可过站: {data.sn}")
            wo = self.work_orders.get(su.work_order_id)
        else:
            if data.work_order_code is None:
                raise BusinessRuleError("首站过站需提供工单号")
            wo = self.work_orders.get_by_code(data.work_order_code)
            if wo is None:
                raise NotFoundError(f"工单不存在: {data.work_order_code}")
            su = None

        # 2. 工单状态
        if wo is None:
            raise NotFoundError("工单不存在")
        if wo.status not in ("released", "in_process"):
            raise BusinessRuleError(f"工单状态不允许过站: {wo.status}")

        # 读有序工序
        steps = self.query.get_ordered_steps(wo.routing_id)
        if not steps:
            raise BusinessRuleError("工艺路线无工序")

        # 3(续). 首站生成 SN
        if su is None:
            if wo.sn_rule_id is None:
                raise BusinessRuleError("工单未配置 SN 规则，无法生成 SN")
            rule = self.sn_rules.get(wo.sn_rule_id)
            if rule is None:
                raise BusinessRuleError("SN 规则不存在")
            new_sn = self.sn_gen.next_sn(rule)
            su = self.serial_units.add(SerialUnit(
                sn=new_sn, work_order_id=wo.id, product_id=wo.product_id,
                status="in_process", current_step_seq=0,
            ))

        # 4. 期望下一工序
        next_steps = [s for s in steps if s.seq > su.current_step_seq]
        if not next_steps:
            raise BusinessRuleError("已完工，无后续工序")
        expected = next_steps[0]

        # 5. 防跳站
        if data.station_id != expected.station_id:
            raise BusinessRuleError(
                f"应到工位(工序 {expected.seq} {expected.name})，当前工位不符"
            )

        # 6. 防重复（保险）
        if self.passes.exists_pass(su.id, expected.id):
            raise BusinessRuleError(f"该工序已过站: 工序 {expected.seq}")

        # 7. 写过站 + 乐观锁更新 serial_unit
        self.passes.add(StationPass(
            serial_unit_id=su.id, work_order_id=wo.id,
            routing_step_id=expected.id, station_id=data.station_id,
            operator_id=data.operator_id, result="pass",
        ))
        prev_version = su.version
        result = self.db.execute(
            update(SerialUnit)
            .where(SerialUnit.id == su.id, SerialUnit.version == prev_version)
            .values(
                current_step_seq=expected.seq,
                current_station_id=data.station_id,
                version=prev_version + 1,
            )
        )
        if result.rowcount == 0:
            raise ConflictError("该产品正被其他工位处理，请重试")
        self.db.refresh(su)

        # 8. 末站完工
        is_last = expected.seq == steps[-1].seq
        if is_last:
            su.status = "finished"
            wo.produced_qty += 1
            if wo.produced_qty >= wo.qty:
                wo.status = "completed"

        # 9. 翻转工单为在制
        if wo.status == "released":
            wo.status = "in_process"

        self.db.flush()

        remaining = [s for s in steps if s.seq > expected.seq]
        next_info = (
            StepInfo(seq=remaining[0].seq, name=remaining[0].name,
                     station_id=remaining[0].station_id)
            if remaining else None
        )
        return StationPassResult(
            sn=su.sn,
            passed_step=StepInfo(seq=expected.seq, name=expected.name,
                                 station_id=expected.station_id),
            next_step=next_info,
            is_finished=su.status == "finished",
            work_order_status=wo.status,
        )
