"""演示环境：开启装配工序的强制绑料/强制参数校验。

- RT-SHELL-A 工序 seq=2（装配）：require_material_binding / require_param_collection = True
- NBK-SHELL-A 的 BOM 项 consume_at_operation_seq = 2（主板+螺丝都在装配站装）
- 重建该路线所有已下达工单的 process_snapshot（快照在下达时冻结，需刷新才带开关）
"""
from sqlalchemy import select

from lightmes.database import SessionLocal
from lightmes.modules.masterdata.models import BomItem, Operation, Product, Routing
from lightmes.modules.production.models import WorkOrder
from lightmes.modules.production.process_snapshot import build_process_snapshot

db = SessionLocal()
try:
    routing = db.execute(
        select(Routing).where(Routing.code == "RT-SHELL-A")).scalars().one()
    op2 = db.execute(
        select(Operation).where(
            Operation.routing_id == routing.id, Operation.seq == 2)
    ).scalars().one()
    op2.require_material_binding = True
    op2.require_param_collection = True

    shell = db.execute(
        select(Product).where(Product.code == "NBK-SHELL-A")).scalars().one()
    from lightmes.modules.masterdata.query_service import MasterDataQueryService
    bom = MasterDataQueryService(db).get_active_bom(shell.id)
    for item in db.execute(
            select(BomItem).where(BomItem.bom_id == bom.id)).scalars().all():
        item.consume_at_operation_seq = 2

    for wo in db.execute(select(WorkOrder).where(
            WorkOrder.routing_id == routing.id,
            WorkOrder.status.in_(("released", "in_process")))).scalars().all():
        wo.process_snapshot = build_process_snapshot(db, wo)
        print(f"snapshot rebuilt: {wo.code}")

    db.commit()
    print(f"装配工序({op2.code}) 强制绑料+强制参数 已开启")
finally:
    db.close()
