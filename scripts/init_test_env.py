"""初始化本地测试环境（默认 PostgreSQL，可回退 SQLite）。

用途：快速准备一套可测全流程的数据。
  uv run python scripts/init_test_env.py

- 使用环境/.env 里的 DATABASE_URL（PG 需先 alembic upgrade head）；
  未配置时回退 SQLite lightmes_test.db（--fresh 删除重建 SQLite 库）
- 幂等：按编码去重，可重复运行
- 角色 + 用户：admin/admin123、supervisor1/sup123、operator1/op123、viewer1/view123
- IssueType（quality/equipment/andon）+ 系统缺陷类型 + 一个手工缺陷类型
- 示范产线（复用 seed_demo_line：LINE-A 三站 + RT-SHELL-A 三工序 + BOM + SN 规则
  + WO-DEMO-001 qty=10 released，并预排程到今天 08:00-16:00）
- WO-DEMO-002（created，backlog 待排）+ 螺丝收货批次 LOT-SCREW-001
"""
import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent.parent / "lightmes_test.db"
DB_URL = f"sqlite:///{DB_FILE}"


def _dotenv_has(key: str) -> bool:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return False
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true",
                        help="SQLite 模式下删除重建库文件")
    args = parser.parse_args()

    using_sqlite = "DATABASE_URL" not in os.environ and not _dotenv_has("DATABASE_URL")
    if using_sqlite:
        if args.fresh and DB_FILE.exists():
            DB_FILE.unlink()
        os.environ["DATABASE_URL"] = DB_URL

    from lightmes.database import SessionLocal, engine
    from lightmes.shared.base import Base
    # 注册所有模型
    from lightmes.modules.auth import models as _auth  # noqa: F401
    from lightmes.modules.masterdata import models as _md  # noqa: F401
    from lightmes.modules.production import models as _prod  # noqa: F401
    from lightmes.modules.api_v1 import models as _api  # noqa: F401
    from lightmes.modules.connectivity import models as _conn  # noqa: F401
    from lightmes.modules.equipment import models as _eq  # noqa: F401
    from lightmes.modules.issue import models as _issue  # noqa: F401
    from lightmes.shared.audit import AuditLog  # noqa: F401
    from lightmes.shared.custom_fields import CustomFieldDefinition  # noqa: F401

    Base.metadata.create_all(bind=engine)

    from sqlalchemy import select
    from lightmes.modules.auth.models import Role, User
    from lightmes.modules.auth.schemas import UserCreate
    from lightmes.modules.auth.service import AuthService
    from lightmes.modules.issue.models import IssueType
    from lightmes.modules.production.defect_service import DefectService
    from lightmes.modules.production.models import DefectType, WorkOrder
    from lightmes.modules.production.repository import MaterialLotRepository
    from lightmes.modules.production.material_lot_service import MaterialLotService

    db = SessionLocal()
    try:
        auth = AuthService(db)
        auth.initialize_default_roles()
        if auth.user_repo.get_by_username("admin") is None:
            auth.ensure_admin_user("admin123")
        else:
            auth.ensure_admin_user(None)  # 仅确保存在

        roles = {r.name: r for r in db.execute(select(Role)).scalars().all()}
        for username, password, display, role_name in [
            ("supervisor1", "sup123", "王主管", "supervisor"),
            ("operator1", "op123", "李操作员", "operator"),
            ("viewer1", "view123", "赵查看", "viewer"),
        ]:
            if auth.user_repo.get_by_username(username) is None:
                auth.create_user(UserCreate(
                    username=username, password=password,
                    display_name=display, role_id=roles[role_name].id))

        # IssueType 字典（quality/equipment 联动依赖）
        for code, name, severity, blocking in [
            ("quality", "质量问题", "major", True),
            ("equipment", "设备异常", "critical", True),
            ("andon", "安灯呼叫", "minor", False),
        ]:
            exists = db.execute(
                select(IssueType).where(IssueType.code == code)
            ).scalar_one_or_none()
            if exists is None:
                db.add(IssueType(
                    code=code, name=name, severity=severity,
                    is_blocking=blocking, is_active=True))
        db.flush()

        # 缺陷类型：系统类型 + 手工外观类
        DefectService(db).ensure_system_defect_types()
        if db.execute(select(DefectType).where(DefectType.code == "SCRATCH")
                      ).scalar_one_or_none() is None:
            db.add(DefectType(
                code="SCRATCH", name="外观划伤", category="外观",
                severity="minor", is_active=True))
        db.commit()

        # 示范产线 + 工单（幂等）
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from seed_demo_line import main as seed_demo_line
        seed_demo_line()

        # 预排程 WO-DEMO-001 到今天，planner 周视图可见
        wo1 = db.execute(
            select(WorkOrder).where(WorkOrder.code == "WO-DEMO-001")
        ).scalars().one()
        if wo1.planned_start is None:
            today = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
            wo1.planned_start = today
            wo1.planned_end = today + timedelta(hours=8)

        # WO-DEMO-002：created 状态留在 backlog（测排程/取消/下达）
        from lightmes.modules.production.service import ProductionService
        from lightmes.modules.production.schemas import WorkOrderCreate
        from lightmes.modules.masterdata.models import Product, Routing, Line
        prod_svc = ProductionService(db)
        shell = db.execute(select(Product).where(Product.code == "NBK-SHELL-A")).scalars().one()
        routing = db.execute(select(Routing).where(Routing.code == "RT-SHELL-A")).scalars().one()
        line = db.execute(select(Line).where(Line.code == "LINE-A")).scalars().one()
        rule = prod_svc.sn_rules.get_by_code("SN-SHELL")
        if prod_svc.work_orders.get_by_code("WO-DEMO-002") is None:
            prod_svc.create_work_order(WorkOrderCreate(
                code="WO-DEMO-002", product_id=shell.id, routing_id=routing.id,
                line_id=line.id, qty=5, sn_rule_id=rule.id))

        # 螺丝收货批次（装配站批次消耗用）
        screw = db.execute(select(Product).where(Product.code == "SCREW-M2")).scalars().one()
        from lightmes.modules.production.models import MaterialLot
        if db.execute(select(MaterialLot).where(MaterialLot.code == "LOT-SCREW-001")
                      ).scalar_one_or_none() is None:
            MaterialLotService(db).receive(
                code="LOT-SCREW-001", product_id=screw.id,
                quantity=1000, supplier_lot="SUP-2026-08")

        db.commit()

        # ---- 校验演示配置：装配工序双开关 + BOM 消耗工序 + 组件SN档案 + 批次放行 ----
        from lightmes.modules.masterdata.models import BomItem, Operation as MOp
        from lightmes.modules.masterdata.query_service import MasterDataQueryService
        from lightmes.modules.production.process_snapshot import build_process_snapshot
        routing2 = db.execute(select(Routing).where(Routing.code == "RT-SHELL-A")).scalars().one()
        op2 = db.execute(select(MOp).where(MOp.routing_id == routing2.id, MOp.seq == 2)).scalars().one()
        op2.require_material_binding = True
        op2.require_param_collection = True
        bom2 = MasterDataQueryService(db).get_active_bom(shell.id)
        for item in db.execute(select(BomItem).where(BomItem.bom_id == bom2.id)).scalars().all():
            if item.consume_at_operation_seq is None:
                item.consume_at_operation_seq = 2
        mb2 = db.execute(select(Product).where(Product.code == "MB-100")).scalars().one()
        from lightmes.modules.production.models import SerialUnit as SU
        for i in range(1, 21):
            sn2 = f"MB2608{i:05d}"
            if db.execute(select(SU).where(SU.sn == sn2)).scalar_one_or_none() is None:
                db.add(SU(sn=sn2, work_order_id=None, product_id=mb2.id,
                          status="pending", current_operation_seq=0))
        lot2 = db.execute(select(MaterialLot).where(MaterialLot.code == "LOT-SCREW-001")).scalars().one()
        if lot2.status == "received":
            MaterialLotService(db).release("LOT-SCREW-001")
        for wo2 in db.execute(select(WorkOrder).where(
                WorkOrder.routing_id == routing2.id,
                WorkOrder.status.in_(("released", "in_process")))).scalars().all():
            wo2.process_snapshot = build_process_snapshot(db, wo2)
        db.commit()

        print()
        print("=== 测试环境就绪 ===")
        print(f"DB: {engine.url.render_as_string(hide_password=True)}")
        print("账号: admin/admin123 · supervisor1/sup123 · operator1/op123 · viewer1/view123")
        print("工单: WO-DEMO-001(released, 已排今天) · WO-DEMO-002(created, backlog)")
        print("批次: LOT-SCREW-001 (SCREW-M2 x1000)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
