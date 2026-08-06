"""过站并发测试：证明 OperationPassService 的 serial_unit 乐观锁守卫真实生效。

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
    Line, Operation, Product, Routing, WorkStation,
)
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.production.models import (
    OperationParam, OperationRecord, SerialUnit, SnRule, WorkOrder,
)
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput, OperationPassResult,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.trace.models import GenealogyBind
from lightmes.shared.errors import ConflictError


def test_double_scan_same_sn_raises_conflict():
    """同一 SN 同一工序被两个线程并发过站：恰好一个成功，一个 ConflictError。"""
    tag = uuid.uuid4().hex[:8]
    sn = f"CONC-SN-{tag}"
    su_id = wo_id = rule_id = routing_id = product_id = None
    line_id = work_station_id = None

    # --- setup：真实会话，提交后关闭 ---
    setup = SessionLocal()
    try:
        md = MasterDataService(setup)
        p = md.create_product(
            ProductCreate(code=f"CONC-P-{tag}", name="壳", type="finished")
        )
        line = md.create_line(LineCreate(code=f"CONC-LN-{tag}", name="线"))
        w1 = md.create_work_station(WorkStationCreate(
            code=f"CONC-W-{tag}", name="过站", line_id=line.id, seq=1,
        ))
        r = md.create_routing(RoutingCreate(
            code=f"CONC-R-{tag}", name="路线", product_id=p.id,
            operations=[OperationCreate(
                seq=1, code="OP1", name="过站", default_work_station_id=w1.id,
            )],
        ))
        prod = ProductionService(setup)
        rule = prod.create_sn_rule(SnRuleCreate(
            code=f"CONC-SR-{tag}", name="r", pattern=f"CONC{tag}{{SEQ:4}}"
        ))
        wo = prod.create_work_order(WorkOrderCreate(
            code=f"CONC-WO-{tag}", product_id=p.id, routing_id=r.id,
            line_id=line.id, qty=10, sn_rule_id=rule.id,
        ))
        prod.release_work_order(wo.id)
        # 手动创建唯一一个 SU：status=in_process, current_operation_seq=0, version=0。
        # 两个线程都瞄着同一个 SN、同一道工序（seq=1）。
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
        product_id = p.id
        line_id = line.id
        work_station_id = w1.id
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
                res = OperationPassService(s).pass_operation(
                    OperationPassInput(work_station_id=work_station_id, sn=sn)
                )
                s.commit()
                with lock:
                    results.append(res)
            except ConflictError as e:
                s.rollback()
                with lock:
                    results.append(e)
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

    # --- 断言：一个成功 + 一个 ConflictError ---
    assert not errors, f"并发出错: {errors}"
    ok = [r for r in results if isinstance(r, OperationPassResult)]
    conflicts = [r for r in results if isinstance(r, ConflictError)]
    assert len(results) == 2, f"期望 2 个结果, 实际 {results}"
    assert len(ok) == 1, f"期望恰好 1 个成功, 实际 {len(ok)}"
    assert len(conflicts) == 1, f"期望恰好 1 个 ConflictError, 实际 {len(conflicts)}"

    # --- 清理：按 FK 依赖顺序直接 DELETE，避免 ORM 单位工作排序问题 ---
    cleanup = SessionLocal()
    try:
        cleanup.execute(
            delete(GenealogyBind).where(GenealogyBind.parent_sn_id == su_id)
        )
        rec_ids = list(cleanup.execute(
            select(OperationRecord.id).where(OperationRecord.serial_unit_id == su_id)
        ).scalars())
        if rec_ids:
            cleanup.execute(
                delete(OperationParam).where(
                    OperationParam.operation_record_id.in_(rec_ids))
            )
            cleanup.execute(
                delete(OperationRecord).where(OperationRecord.id.in_(rec_ids))
            )
        # release 已批量预生成 pending SerialUnit，按工单整组删除再删工单
        cleanup.execute(delete(SerialUnit).where(SerialUnit.work_order_id == wo_id))
        cleanup.execute(delete(WorkOrder).where(WorkOrder.id == wo_id))
        cleanup.execute(delete(SnRule).where(SnRule.id == rule_id))
        cleanup.execute(
            delete(Operation).where(Operation.routing_id == routing_id)
        )
        cleanup.execute(delete(Routing).where(Routing.id == routing_id))
        cleanup.execute(delete(WorkStation).where(WorkStation.id == work_station_id))
        cleanup.execute(delete(Line).where(Line.id == line_id))
        cleanup.execute(delete(Product).where(Product.id == product_id))
        cleanup.commit()
    finally:
        cleanup.close()
