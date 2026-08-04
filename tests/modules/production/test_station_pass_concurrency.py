"""过站并发测试：证明 serial_unit 乐观锁守卫真实生效。

两个真实连接并发对同一个 SN 过同一道工序，必须恰好一个成功，
另一个抛 ConflictError（spec §5/§9 防重复扫描保证）。

不用 db_session（回滚 fixture 是单连接，看不到并发），直接开真实连接
并各自提交。结束后按 FK 安全顺序清理。
"""
import threading
import uuid

from sqlalchemy import delete, select

from lightmes.database import SessionLocal
from lightmes.modules.masterdata.models import (
    Line, Operation, Product, Routing, RoutingStep, Station, WorkStation,
)
from lightmes.modules.masterdata.schemas import (
    ProductCreate,
    RoutingCreate,
    OperationCreate,
    StationCreate,
    LineCreate,
    WorkStationCreate,
)
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.production.models import SerialUnit, SnRule, StationPass, WorkOrder
from lightmes.modules.production.schemas import (
    SnRuleCreate,
    StationPassInput,
    StationPassResult,
    WorkOrderCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.station_pass_service import StationPassService
from lightmes.shared.errors import ConflictError


def test_double_scan_same_sn_raises_conflict():
    """同一 SN 同一工序被两个线程并发过站：恰好一个成功，一个 ConflictError。"""
    tag = uuid.uuid4().hex[:8]
    sn = f"CCSN-{tag}"
    su_id = wo_id = rule_id = routing_id = station_id = product_id = None
    line_id = work_station_id = operation_id = None

    # --- setup：真实会话，提交后关闭 ---
    setup = SessionLocal()
    try:
        md = MasterDataService(setup)
        p = md.create_product(
            ProductCreate(code=f"CC-P-{tag}", name="壳", type="finished")
        )
        line = md.create_line(LineCreate(code=f"CC-LN-{tag}", name="线"))
        s1 = md.create_station(StationCreate(code=f"CC-S-{tag}", name="上料"))
        w1 = md.create_work_station(WorkStationCreate(
            code=f"CC-W-{tag}", name="上料站", line_id=line.id, seq=1,
        ))
        r = md.create_routing(RoutingCreate(
            code=f"CC-R-{tag}", name="路线", product_id=p.id,
            operations=[OperationCreate(
                seq=1, code="OP1", name="上料", default_work_station_id=w1.id,
            )],
        ))
        # 旧生产层 StationPassService 仍读 routing_steps —— 补建
        setup.add(RoutingStep(routing_id=r.id, seq=1, station_id=s1.id, name="上料"))
        setup.flush()
        prod = ProductionService(setup)
        rule = prod.create_sn_rule(SnRuleCreate(
            code=f"CC-L-{tag}", name="r", pattern=f"CC{tag}{{SEQ:4}}"
        ))
        wo = prod.create_work_order(WorkOrderCreate(
            code=f"CC-W-{tag}", product_id=p.id, routing_id=r.id,
            line_id=line.id, qty=10, sn_rule_id=rule.id,
        ))
        prod.release_work_order(wo.id)
        # 手动创建唯一一个 SU：status=in_process, current_operation_seq=0, version=0
        # 两个线程都瞄着同一个 SN、同一道工序。
        su = SerialUnit(
            sn=sn, work_order_id=wo.id, product_id=p.id,
            status="in_process", current_operation_seq=0, version=0,
        )
        setup.add(su)
        setup.commit()
        su_id = su.id
        wo_id = wo.id
        rule_id = rule.id
        routing_id = r.id
        station_id = s1.id
        product_id = p.id
        line_id = line.id
        work_station_id = w1.id
        operation_id = md.routings.operations_of(r.id)[0].id
    finally:
        setup.close()

    barrier = threading.Barrier(2)
    results: list[object] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker() -> None:
        s = SessionLocal()
        try:
            # 先加载 SU 到本会话 identity map（缓存 version=0），
            # 再同时冲过 barrier，确保两个事务基于同一个旧版本开抢。
            if s.get(SerialUnit, su_id) is None:
                raise RuntimeError("serial unit not found")
            barrier.wait(timeout=15)
            try:
                res = StationPassService(s).pass_station(
                    StationPassInput(station_id=station_id, sn=sn)
                )
                s.commit()
                with lock:
                    results.append(res)
            except ConflictError as e:
                s.rollback()
                with lock:
                    results.append(e)
        except Exception as e:  # noqa: BLE001 - barrier 异常等
            with lock:
                errors.append(e)
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # --- 断言：一个成功 + 一个 ConflictError ---
    assert not errors, f"并发出错: {errors}"
    ok = [r for r in results if isinstance(r, StationPassResult)]
    conflicts = [r for r in results if isinstance(r, ConflictError)]
    assert len(results) == 2, f"期望 2 个结果, 实际 {results}"
    assert len(ok) == 1, f"期望恰好 1 个成功, 实际 {len(ok)}"
    assert len(conflicts) == 1, f"期望恰好 1 个 ConflictError, 实际 {len(conflicts)}"

    # 末道工序：produced_qty 只能 +1，不能被并发重复累加
    check = SessionLocal()
    try:
        wo = check.get(WorkOrder, wo_id)
        assert wo is not None
        assert wo.produced_qty == 1, (
            f"末道工序 produced_qty 被重复累加: {wo.produced_qty}"
        )
    finally:
        check.close()

    # --- 清理：按 FK 依赖顺序直接 DELETE，避免 ORM 单位工作排序问题 ---
    cleanup = SessionLocal()
    try:
        cleanup.execute(
            delete(StationPass).where(StationPass.serial_unit_id == su_id)
        )
        cleanup.execute(delete(SerialUnit).where(SerialUnit.id == su_id))
        cleanup.execute(delete(WorkOrder).where(WorkOrder.id == wo_id))
        cleanup.execute(delete(SnRule).where(SnRule.id == rule_id))
        cleanup.execute(
            delete(RoutingStep).where(RoutingStep.routing_id == routing_id)
        )
        cleanup.execute(
            delete(Operation).where(Operation.routing_id == routing_id)
        )
        cleanup.execute(delete(Routing).where(Routing.id == routing_id))
        cleanup.execute(delete(Station).where(Station.id == station_id))
        cleanup.execute(delete(WorkStation).where(WorkStation.id == work_station_id))
        cleanup.execute(delete(Line).where(Line.id == line_id))
        cleanup.execute(delete(Product).where(Product.id == product_id))
        cleanup.commit()
    finally:
        cleanup.close()


def test_two_different_units_complete_same_wo_atomically():
    """两个不同 SN 并发完工同一工单（单工序路线，首站即末站）。

    每个线程对同一 work_order 用 work_order_code 做首站过站，各自生成新 SN 并
    立即完工。produced_qty 必须用原子 UPDATE，避免 ORM read-modify-write 丢更新：
    并发下最终恰好 produced_qty==2，工单转 completed。
    """
    tag = uuid.uuid4().hex[:8]
    wo_id = wo_code = rule_id = routing_id = station_id = product_id = None
    line_id = work_station_id = operation_id = None

    # --- setup：真实会话，提交后关闭 ---
    setup = SessionLocal()
    try:
        md = MasterDataService(setup)
        p = md.create_product(
            ProductCreate(code=f"CC2-P-{tag}", name="壳", type="finished")
        )
        line = md.create_line(LineCreate(code=f"CC2-LN-{tag}", name="线"))
        s1 = md.create_station(StationCreate(code=f"CC2-S-{tag}", name="上料"))
        w1 = md.create_work_station(WorkStationCreate(
            code=f"CC2-W-{tag}", name="上料站", line_id=line.id, seq=1,
        ))
        r = md.create_routing(RoutingCreate(
            code=f"CC2-R-{tag}", name="路线", product_id=p.id,
            operations=[OperationCreate(
                seq=1, code="OP1", name="上料", default_work_station_id=w1.id,
            )],
        ))
        setup.add(RoutingStep(routing_id=r.id, seq=1, station_id=s1.id, name="上料"))
        setup.flush()
        prod = ProductionService(setup)
        rule = prod.create_sn_rule(SnRuleCreate(
            code=f"CC2-L-{tag}", name="r", pattern=f"CC2{tag}{{SEQ:4}}"
        ))
        wo = prod.create_work_order(WorkOrderCreate(
            code=f"CC2-W-{tag}", product_id=p.id, routing_id=r.id,
            line_id=line.id, qty=2, sn_rule_id=rule.id,
        ))
        prod.release_work_order(wo.id)
        setup.commit()
        wo_id = wo.id
        wo_code = wo.code
        rule_id = rule.id
        routing_id = r.id
        station_id = s1.id
        product_id = p.id
        line_id = line.id
        work_station_id = w1.id
        operation_id = md.routings.operations_of(r.id)[0].id
    finally:
        setup.close()

    barrier = threading.Barrier(2)
    results: list[object] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker() -> None:
        s = SessionLocal()
        try:
            barrier.wait(timeout=15)
            res = StationPassService(s).pass_station(
                StationPassInput(station_id=station_id, work_order_code=wo_code)
            )
            s.commit()
            with lock:
                results.append(res)
        except Exception as e:  # noqa: BLE001 - barrier 异常等
            s.rollback()
            with lock:
                errors.append(e)
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # --- 断言：两个不同 SN 都成功完工 ---
    assert not errors, f"并发出错: {errors}"
    assert len(results) == 2, f"期望 2 个成功, 实际 {results}"
    sns = {r.sn for r in results}
    assert len(sns) == 2, f"两个线程应产出不同 SN, 实际 {sns}"

    # --- produced_qty 原子累加：恰好 2，工单 completed ---
    check = SessionLocal()
    try:
        wo = check.get(WorkOrder, wo_id)
        assert wo is not None
        assert wo.produced_qty == 2, (
            f"并发完工 produced_qty 丢失更新: {wo.produced_qty}"
        )
        assert wo.status == "completed", (
            f"工单应转 completed, 实际 {wo.status}"
        )
    finally:
        check.close()

    # --- 清理：按 FK 依赖顺序直接 DELETE ---
    cleanup = SessionLocal()
    try:
        su_ids = list(cleanup.execute(
            select(SerialUnit.id).where(SerialUnit.work_order_id == wo_id)
        ).scalars())
        for su_id in su_ids:
            cleanup.execute(
                delete(StationPass).where(StationPass.serial_unit_id == su_id)
            )
        if su_ids:
            cleanup.execute(delete(SerialUnit).where(SerialUnit.id.in_(su_ids)))
        cleanup.execute(delete(WorkOrder).where(WorkOrder.id == wo_id))
        cleanup.execute(delete(SnRule).where(SnRule.id == rule_id))
        cleanup.execute(
            delete(RoutingStep).where(RoutingStep.routing_id == routing_id)
        )
        cleanup.execute(
            delete(Operation).where(Operation.routing_id == routing_id)
        )
        cleanup.execute(delete(Routing).where(Routing.id == routing_id))
        cleanup.execute(delete(Station).where(Station.id == station_id))
        cleanup.execute(delete(WorkStation).where(WorkStation.id == work_station_id))
        cleanup.execute(delete(Line).where(Line.id == line_id))
        cleanup.execute(delete(Product).where(Product.id == product_id))
        cleanup.commit()
    finally:
        cleanup.close()
