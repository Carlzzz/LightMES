"""简易测试数据脚本，适配新的权限系统"""

from lightmes.database import SessionLocal, engine
from lightmes.shared.base import Base
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    BomCreate, BomItemCreate, SkillCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate


def main() -> None:
    # 创建表（如果不存在）
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # 初始化默认角色和admin用户
        auth = AuthService(db)
        auth.initialize_default_roles()
        auth.ensure_admin_user()
        db.flush()

        # 获取或创建操作员角色
        operator_role = auth.role_repo.get_by_name("operator")

        # 操作员（登录用）：demo / demo12345
        user_repo = auth.user_repo
        if user_repo.get_by_username("demo") is None:
            auth.create_user(UserCreate(
                username="demo", password="demo12345",
                display_name="演示操作员", role_id=operator_role.id if operator_role else None))
            db.flush()
        operator = user_repo.get_by_username("demo")

        # 检查演示工单是否已存在
        prod = ProductionService(db)
        if prod.work_orders.get_by_code("WO-DEMO") is not None:
            print("演示数据已存在（工单 WO-DEMO），跳过。")
            return

        md = MasterDataService(db)
        sk = SkillService(db)

        # 产线 + 3 作业站
        line = md.create_line(LineCreate(code="LINE-DEMO", name="演示装配线"))
        ws1 = md.create_work_station(WorkStationCreate(
            code="WS-01", name="上线工位", line_id=line.id, seq=1))
        ws2 = md.create_work_station(WorkStationCreate(
            code="WS-02", name="核心装配工位", line_id=line.id, seq=2))
        ws3 = md.create_work_station(WorkStationCreate(
            code="WS-03", name="测试包装工位", line_id=line.id, seq=3))

        # 成品 + 2 组件
        product = md.create_product(ProductCreate(
            code="PN-DEMO", name="智能控制器", type="finished"))
        comp_a = md.create_product(ProductCreate(
            code="C-SENSOR", name="传感器模组", type="component"))
        comp_b = md.create_product(ProductCreate(
            code="C-RING", name="密封圈", type="component"))

        # BOM（成品 = 1×传感器 + 2×密封圈）
        md.create_bom(BomCreate(product_id=product.id, items=[
            BomItemCreate(component_product_id=comp_a.id, qty=1),
            BomItemCreate(component_product_id=comp_b.id, qty=2),
        ]))

        # 技能：装配技能，最高 L3；核心工序要求 L2
        skill = sk.create_skill(SkillCreate(
            code="ASSY-DEMO", name="装配技能(演示)", max_level=3,
            description="核心部件装配资格"))
        sk.set_operator_skill(operator.id, skill.id, 3)

        # 路线：4 工序，工序2（核心装配）设技能要求 L2
        routing = md.create_routing(RoutingCreate(
            code="RT-DEMO", name="演示工艺路线", product_id=product.id,
            operations=[
                OperationCreate(seq=10, code="OP10", name="上线准备",
                                default_work_station_id=ws1.id,
                                allowed_work_station_ids=[ws1.id]),
                OperationCreate(seq=20, code="OP20", name="核心部件装配",
                                default_work_station_id=ws2.id,
                                required_skill_id=skill.id, required_level=2,
                                allowed_work_station_ids=[ws2.id]),
                OperationCreate(seq=30, code="OP30", name="性能测试",
                                default_work_station_id=ws3.id,
                                allowed_work_station_ids=[ws3.id]),
                OperationCreate(seq=40, code="OP40", name="包装入库",
                                default_work_station_id=ws3.id,
                                allowed_work_station_ids=[ws3.id]),
            ]))

        # SN 规则 + 工单 + 下达（预生成 qty 个 pending SN）
        rule = prod.create_sn_rule(SnRuleCreate(
            code="SR-DEMO", name="演示SN规则", pattern="SN-DEMO{SEQ:5}",
            seq_reset="never", product_id=product.id))
        wo = prod.create_work_order(WorkOrderCreate(
            code="WO-DEMO", product_id=product.id, routing_id=routing.id,
            line_id=line.id, qty=5, sn_rule_id=rule.id))
        prod.release_work_order(wo.id)

        db.commit()
        print("\n✅ 演示数据已创建！")
        print("┌─────────────────────────────────────────────────────┐")
        print("  登录账号:    demo / demo12345")
        print("  管理员账号:  admin / admin123")
        print(f"  工单号:      WO-DEMO (qty=5)")
        print(f"  SN 号段:     SN-DEMO00001 ~ SN-DEMO00005")
        print(f"  产线:        LINE-DEMO")
        print(f"  作业站:      WS-01 上线准备 / WS-02 核心装配 / WS-03 测试包装")
        print("└─────────────────────────────────────────────────────┘")
        print("\n📌 访问 http://localhost:8000 开始测试")
    except Exception as e:
        db.rollback()
        print(f"❌ 错误: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
