"""灌一条示范产线 + 工单，用于本地体验完整过站→绑定→追溯→返工闭环。

幂等：已存在的编码跳过创建，可重复运行。
运行：
  DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" \
    uv run python scripts/seed_demo_line.py
"""

from lightmes.database import SessionLocal
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
    BomCreate, BomItemCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate


def _get_or_create_product(md: MasterDataService, data: ProductCreate):
    existing = md.products.get_by_code(data.code)
    return existing if existing else md.create_product(data)


def _get_or_create_station(md: MasterDataService, data: StationCreate):
    existing = md.stations.get_by_code(data.code)
    return existing if existing else md.create_station(data)


def main() -> None:
    db = SessionLocal()
    try:
        md = MasterDataService(db)
        prod = ProductionService(db)

        # 1) 成品 + 组件（唯一件主板 + 批次件螺丝）
        shell = _get_or_create_product(md, ProductCreate(
            code="NBK-SHELL-A", name="笔记本外壳A", type="finished", unit="pcs",
            track_mode="serial"))
        mainboard = _get_or_create_product(md, ProductCreate(
            code="MB-100", name="主板", type="component", unit="pcs",
            track_mode="serial"))
        screw = _get_or_create_product(md, ProductCreate(
            code="SCREW-M2", name="M2螺丝", type="consumable", unit="pcs",
            track_mode="batch"))

        # 2) 三个工位
        s1 = _get_or_create_station(md, StationCreate(code="ST-上料", name="上料工位"))
        s2 = _get_or_create_station(md, StationCreate(code="ST-装配", name="装配工位"))
        s3 = _get_or_create_station(md, StationCreate(code="ST-检测", name="检测工位"))

        # 3) 工艺路线（3 工序）—— 若成品已有 active 路线则跳过
        routing = md.routings.get_active_by_product(shell.id)
        if routing is None:
            routing = md.create_routing(RoutingCreate(
                code="RT-SHELL-A", name="外壳A主路线", product_id=shell.id,
                steps=[
                    RoutingStepCreate(seq=1, station_id=s1.id, name="上料"),
                    RoutingStepCreate(seq=2, station_id=s2.id, name="装配"),
                    RoutingStepCreate(seq=3, station_id=s3.id, name="检测"),
                ]))

        # 4) BOM（主板 x1 唯一件 + 螺丝 x4 批次件）—— 若已有 active BOM 则跳过
        if md.boms.get_active_by_product(shell.id) is None:
            md.create_bom(BomCreate(product_id=shell.id, items=[
                BomItemCreate(component_product_id=mainboard.id, qty=1),
                BomItemCreate(component_product_id=screw.id, qty=4),
            ]))

        # 5) SN 规则（SN + 日期 + 5 位流水，按日重置）
        rule = prod.sn_rules.get_by_code("SN-SHELL")
        if rule is None:
            rule = prod.create_sn_rule(SnRuleCreate(
                code="SN-SHELL", name="外壳SN规则", pattern="SN{YY}{MM}{DD}{SEQ:5}",
                seq_reset="daily", product_id=shell.id))

        # 6) 工单（qty=10）并下达 —— 若已存在则复用
        wo = prod.work_orders.get_by_code("WO-DEMO-001")
        if wo is None:
            wo = prod.create_work_order(WorkOrderCreate(
                code="WO-DEMO-001", product_id=shell.id, routing_id=routing.id,
                qty=10, sn_rule_id=rule.id))
        if wo.status == "created":
            prod.release_work_order(wo.id)

        db.commit()

        print("=== 示范产线已就绪 ===")
        print(f"成品:   {shell.code} (id={shell.id})")
        print(f"组件:   {mainboard.code} 唯一件 / {screw.code} 批次件")
        print(f"工位:   上料 id={s1.id} | 装配 id={s2.id} | 检测 id={s3.id}")
        print(f"工艺路线: {routing.code} (id={routing.id}) 3 工序")
        print(f"SN规则: {rule.code}  pattern=SN{{YY}}{{MM}}{{DD}}{{SEQ:5}}")
        print(f"工单:   {wo.code} (id={wo.id}) qty=10 status={wo.status}")
        print()
        print("--- 怎么玩（先在 /login 用 admin/admin123 登录）---")
        print(f"1. 首件上料: 打开 /production/scan?station_id={s1.id}")
        print("   在输入框填工单号 WO-DEMO-001 → 提交 → 生成 SN 并过上料站")
        print(f"2. 装配: /production/scan?station_id={s2.id}  输入上一步生成的 SN")
        print(f"3. 检测(末站完工): /production/scan?station_id={s3.id}  输入该 SN")
        print(f"4. WIP 看板: /production/wip?work_order_id={wo.id}")
        print("5. 追溯查询: /trace/query  (输入成品 SN 看履历/正向；输入组件SN/批次看反向)")
        print("6. 返工: /trace/rework  (输入 SN + 回退到的工序序号)")
        print()
        print("提示: 组件绑定目前经过站 API 的 components 参数完成；扫码页暂未加组件输入行，")
        print("      要体验绑定+追溯，用 API 过站（见下）或告诉我补扫码页组件输入。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
