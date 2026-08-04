from sqlalchemy import update
from sqlalchemy.orm import Session

from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.production.models import SerialUnit, StationPass, WorkOrder
from lightmes.modules.production.repository import (
    SerialUnitRepository, StationPassRepository, SnRuleRepository,
    WorkOrderRepository,
)
from lightmes.modules.production.schemas import (
    StationPassInput, StationPassResult, StepInfo,
)
from lightmes.modules.production.sn_generator import SnGenerator
from lightmes.modules.trace.genealogy_service import GenealogyService
from lightmes.modules.trace.schemas import ComponentBind
from lightmes.shared.events import event_bus
from lightmes.modules.production.events import StationPassed, SerialUnitFinished
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

        # 6. 防重复：由"期望下一工序 = 第一个 seq > current_step_seq"天然保证——
        #    正常流程过完某工序后 current_step_seq 即推进，该工序不会再被选为 expected；
        #    返工回退后旧的 pass 记录保留但不阻挡（§5.4）。无需额外 exists_pass 守卫。

        # 7. 写过站 + 乐观锁更新 serial_unit
        station_pass = self.passes.add(StationPass(
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

        # 7b. 组件绑定（同事务；失败则回滚整个过站，含已生成的 SN）
        bound_count = 0
        if data.components:
            try:
                binds = GenealogyService(self.db).bind_components(
                    su,
                    [ComponentBind(
                        component_product_id=c.component_product_id,
                        component_sn=c.component_sn,
                        component_batch_no=c.component_batch_no,
                        qty=c.qty,
                    ) for c in data.components],
                    operator_id=data.operator_id,
                    station_pass_id=station_pass.id,
                )
            except Exception:
                self.db.rollback()
                raise
            bound_count = len(binds)

        # 8. 末站完工（produced_qty 用原子 UPDATE，避免两个不同 SN 并发完工丢更新）
        is_last = expected.seq == steps[-1].seq
        if is_last:
            su.status = "finished"
            event_bus.publish(SerialUnitFinished(
                serial_unit_id=su.id, sn=su.sn, work_order_id=wo.id,
            ))
            new_qty = self.db.execute(
                update(WorkOrder)
                .where(WorkOrder.id == wo.id)
                .values(produced_qty=WorkOrder.produced_qty + 1)
                .returning(WorkOrder.produced_qty)
            ).scalar_one()
            if new_qty >= wo.qty:
                self.db.execute(
                    update(WorkOrder).where(WorkOrder.id == wo.id).values(status="completed")
                )
            self.db.refresh(wo)

        # 9. 翻转工单为在制 + 返工件复位
        if wo.status == "released":
            wo.status = "in_process"
        if su.status == "reworking":
            su.status = "in_process"

        self.db.flush()

        event_bus.publish(StationPassed(
            serial_unit_id=su.id, sn=su.sn, work_order_id=wo.id,
            routing_step_id=expected.id, station_id=data.station_id,
        ))

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
            bound_count=bound_count,
        )
