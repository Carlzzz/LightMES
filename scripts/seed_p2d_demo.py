"""P2d/P2e/P2f 工位作业主界面 + 载体码过站演示数据（适配 P2f 一站式入口）。

塞入一条完整链路：产线 → 3 作业站 → 成品+2 组件 → BOM → 4 工序路线
（核心装配工序设技能要求 L2）→ 技能定义 → 操作员+技能档案 → SN 规则 → 工单 → 下达
（release 时按 qty 预生成 pending SerialUnit）。

幂等：若演示工单 WO-DEMO 已存在则跳过。
运行：
  DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python scripts/seed_p2d_demo.py

注意：本脚本会 commit 到共享 dev 库。用完如要跑测试，请清掉演示数据
（skill.code/product.code 等唯一键会与测试 fixture 冲突）。
"""

from lightmes.database import SessionLocal
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.auth.repository import UserRepository
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    BomCreate, BomItemCreate, SkillCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate


def main() -> None:
    db = SessionLocal()
    try:
        prod = ProductionService(db)
        if prod.work_orders.get_by_code("WO-DEMO") is not None:
            print("演示数据已存在（工单 WO-DEMO），跳过。")
            return

        md = MasterDataService(db)
        sk = SkillService(db)
        auth = AuthService(db)
        users = UserRepository(db)

        # 操作员（登录用）：demo / demo12345
        if users.get_by_username("demo") is None:
            auth.create_user(UserCreate(
                username="demo", password="demo12345",
                display_name="演示操作员", role="operator"))
            db.flush()
        operator = users.get_by_username("demo")

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
            description="核心部件装配资格（演示，用 ASSY-DEMO 避免与测试 fixture 冲突）"))
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

        # SN 规则 + 工单 + 下达（P2e: release 时预生成 qty 个 pending SN）
        rule = prod.create_sn_rule(SnRuleCreate(
            code="SR-DEMO", name="演示SN规则", pattern="SN-DEMO{SEQ:5}",
            seq_reset="never", product_id=product.id))
        wo = prod.create_work_order(WorkOrderCreate(
            code="WO-DEMO", product_id=product.id, routing_id=routing.id,
            line_id=line.id, qty=5, sn_rule_id=rule.id))
        prod.release_work_order(wo.id)

        db.commit()
        print("演示数据已创建（P2f 一站式入口）：")
        print(f"  登录账号:    demo / demo12345")
        print(f"  工单号:      WO-DEMO (released, qty=5 → 5 个 pending SN)")
        print(f"  SN 号段:     SN-DEMO00001 ~ SN-DEMO00005 (预生成)")
        print(f"  产线:        LINE-DEMO")
        print(f"  作业站:      WS-01 id={ws1.id} 上线准备 OP10")
        print(f"               WS-02 id={ws2.id} 核心装配 OP20 (需 ASSY-DEMO≥L2)")
        print(f"               WS-03 id={ws3.id} 测试 OP30 / 包装 OP40")
        print(f"  操作员技能:  演示操作员 ASSY-DEMO=L3 (可过核心工序)")
        print(f"  成品 BOM:    1×C-SENSOR 传感器 + 2×C-RING 密封圈")
        print()
        print("P2f 流程体验：")
        print(f"  1. 浏览器开 http://127.0.0.1:8080/  用 demo/demo12345 登录")
        print(f"  2. 首页 → 工位作业（或直接 /production/station）")
        print(f"  3. 就绪页三栏：")
        print(f"     ① 作业站下拉 → 选 WS-01 上线工位")
        print(f"     ② 工单下拉 → 自动列出 WO-DEMO（剩余 5）")
        print(f"     ③ 扫码框 → 输入 PALLET-001 → 进入")
        print(f"     系统自动取 SN-DEMO00001 绑 PALLET-001（只绑、不过首工序）")
        print(f"  4. 富主界面：看工艺路径全景（OP10 当前）+ 物料绑定表（1×传感器+2×密封圈）+ SOP 占位")
        print(f"     → 检查物料后点底部'确认过站(PASS)' → 过 OP10，重置扫下一件")
        print(f"  5. 继续 PALLET-002 / PALLET-003 投产（同工单上下文保留）")
        print(f"  6. 后续工序：换 WS-02（核心装配），扫 PALLET-001（自动识别载体码）→ 富界面 → 确认过站")
        print(f"  7. WS-03 过 OP30、OP40 直至完工（完工自动解绑载体码，托盘可复用）")
        print(f"  拦截体验：")
        print(f"   - 工单 5 件全部投产后再扫载体码 → '已全部投产，请选择新工单'")
        print(f"   - 重复扫同一载体码 → '已绑定，请先解绑'")
        print(f"   - 跨产线作业站选工单 → 工单下拉不显示（异产线工单过滤）")
        print(f"   - /trace/carrier-unbind 可解绑（扫 SN 或载体码）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
