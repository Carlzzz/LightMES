from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.auth.repository import UserRepository
from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.production.repository import (
    SerialUnitRepository, WorkOrderRepository,
)
from lightmes.modules.production.schemas import (
    StationOpView, StationComponentView, StationView,
    FirstInspectionStationView, TestDataStationView,
    FirstInspectionCheckItemRead, TestDataFieldRead,
)
from lightmes.modules.production.quality_service import (
    FirstInspectionService, TestDataService,
)
from lightmes.modules.production.models import (
    FirstInspectionConfig, TestDataTemplate, OperationRecord,
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
        # 定位：SN → 载体码（活跃单元） → 工单号（首件 su=None）
        su = self.serial_units.get_by_sn(scan)
        if su is None:
            su = self.serial_units.get_active_by_carrier(scan)
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

        # 批量查询所有工序的 allowed work stations（1 次查询替代 N 次）
        all_op_ids = [o.id for o in operations]
        op_ws_map = self.query.batch_allowed_work_stations(all_op_ids)

        # 取该 SN 全部 operation_records，按 operation_id 分组取 end_time 最新的 result
        latest_result_by_op: dict[int, str] = {}
        if su is not None:
            all_records = list(self.db.execute(
                select(OperationRecord)
                .where(OperationRecord.serial_unit_id == su.id)
                .order_by(OperationRecord.operation_id, OperationRecord.end_time.desc())
            ).scalars().all())
            for r in all_records:
                if r.operation_id not in latest_result_by_op:
                    latest_result_by_op[r.operation_id] = r.result  # 第一条 = 最新

        op_views: list[StationOpView] = []
        for o in operations:
            if expected is not None and o.id == expected.id and (su is None or su.status != "finished"):
                st = "current"
            elif o.seq > current_seq:
                st = "future"
            elif latest_result_by_op.get(o.id) == "skip":
                st = "skipped"
            else:
                st = "done"
            op_allowed = op_ws_map.get(o.id, [])
            allowed_names = [w.name for w in op_allowed]
            if not allowed_names:
                ws = self.query.get_work_station(o.default_work_station_id)
                allowed_names = [ws.name if ws else f"#{o.default_work_station_id}"]
            op_views.append(StationOpView(
                operation_id=o.id,
                seq=o.seq, name=o.name, code=o.code,
                work_station_id=o.default_work_station_id, status=st,
                allowed_work_stations=allowed_names,
            ))

        # Layer 2：本作业站 allowed 子集
        station_op_views = [
            v for v in op_views
            if work_station_id in [w.id for w in op_ws_map.get(v.operation_id, [])]
        ]

        current_op = next((v for v in op_views if v.status == "current"), None)

        # 技能预判
        operator_skill_level: int | None = None
        required_level: int | None = None
        skill_ok = True
        is_off_station = False
        components: list[StationComponentView] = []
        if expected is not None:
            allowed = op_ws_map.get(expected.id, [])
            allowed_ids = [w.id for w in allowed]
            if not allowed_ids:
                allowed_ids = [expected.default_work_station_id]
            is_off_station = work_station_id not in allowed_ids
            if is_off_station:
                # 不在当站直接拦截：不进入富界面，弹错误片段
                # 列出该 SN 当前工序允许的作业站名供操作员参考
                names = "、".join(w.name for w in allowed) or f"作业站 #{expected.default_work_station_id}"
                raise BusinessRuleError(
                    f"该 SN 当前工序 {expected.seq} {expected.name} "
                    f"应在【{names}】之一作业站做，当前作业站不符")
            if expected.required_skill_id is not None:
                required_level = expected.required_level
                operator_skill_level = (
                    self.skills.get_operator_level(
                        operator_id, expected.required_skill_id)
                    if operator_id else None)
                skill_ok = (operator_skill_level is not None
                            and operator_skill_level >= (required_level or 0))
            for item in self.query.get_active_bom_items(product.id):
                # 只显示本工序应装件 + NULL 兼容件
                if (item.consume_at_operation_seq is not None
                        and item.consume_at_operation_seq != expected.seq):
                    continue
                comp = self.query.get_product(item.component_product_id)
                components.append(StationComponentView(
                    component_product_id=item.component_product_id,
                    component_code=comp.code if comp else str(item.component_product_id),
                    component_name=comp.name if comp else "",
                    qty=float(item.qty),
                    track_mode=item.track_mode))

        operator = self.users.get(operator_id) if operator_id else None
        # SOP 内容来自当前工序
        sop_text = None
        sop_url = None
        if expected is not None:
            sop_text = expected.sop_text
            sop_url = expected.sop_url

        # 加载首检和测试数据信息
        first_inspection_view = None
        test_data_view = None
        if expected is not None:
            fi_svc = FirstInspectionService(self.db)
            fi_config = fi_svc.get_config_by_operation(expected.id, work_station_id)
            if fi_config and fi_config.is_enabled:
                # 检查是否需要首检
                needs_inspection = False
                trigger_reason = None
                if su is not None:
                    needs_inspection, trigger_reason, _ = fi_svc.check_needs_inspection(fi_config, wo.id, expected.id)
                else:
                    # 首件总是需要首检（如果配置了）
                    needs_inspection, trigger_reason = True, "new_order"

                check_items = [FirstInspectionCheckItemRead.model_validate(item) for item in fi_svc.list_check_items(fi_config.id)]

                first_inspection_view = FirstInspectionStationView(
                    needs_inspection=needs_inspection,
                    trigger_reason=trigger_reason,
                    config_id=fi_config.id,
                    config_name=fi_config.name,
                    check_items=check_items
                )

            # 测试数据
            td_svc = TestDataService(self.db)
            td_template = td_svc.get_template_by_operation(expected.id, work_station_id)
            if td_template and td_template.is_enabled:
                fields = [TestDataFieldRead.model_validate(f) for f in td_svc.list_fields(td_template.id)]
                test_data_view = TestDataStationView(
                    needs_test_data=True,
                    template_id=td_template.id,
                    template_name=td_template.name,
                    fields=fields
                )
            else:
                test_data_view = TestDataStationView(
                    needs_test_data=False
                )

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
            station_operations=station_op_views,
            current_op=current_op,
            components=components,
            sop_text=sop_text,
            sop_url=sop_url,
            first_inspection=first_inspection_view,
            test_data=test_data_view,
        )
