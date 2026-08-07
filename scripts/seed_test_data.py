"""Seed test data for LightMES - 笔记本外壳组装线"""
from lightmes.database import SessionLocal
from lightmes.modules.auth.models import User
from lightmes.modules.masterdata.models import (
    Product, Line, WorkStation, Routing, Operation,
    Bom, BomItem, Skill, OperatorSkill, OperationWorkStation,
)
from lightmes.modules.production.models import SnRule, WorkOrder


def seed():
    db = SessionLocal()

    # ---- 产品 ----
    # 成品
    laptop_cover = Product(
        code="P-NB-A15", name="笔记本A15外壳组件", type="finished", unit="pcs", track_mode="serial")
    db.add(laptop_cover); db.flush()
    # 组件
    top_shell = Product(code="C-TOP-A15", name="A15上盖壳体", type="component", unit="pcs", track_mode="serial")
    bottom_shell = Product(code="C-BOT-A15", name="A15下盖壳体", type="component", unit="pcs", track_mode="serial")
    hinge = Product(code="C-HINGE-01", name="铰链组件", type="component", unit="pcs", track_mode="batch")
    screw = Product(code="C-SCR-M3", name="M3螺丝", type="material", unit="pcs", track_mode="batch")
    db.add_all([top_shell, bottom_shell, hinge, screw]); db.flush()

    # ---- 产线 ----
    line = Line(code="L-ASSY-01", name="组装一线", description="笔记本外壳组装")
    db.add(line); db.flush()

    # ---- 作业站 ----
    ws1 = WorkStation(code="WS-01", name="上盖投料站", line_id=line.id, seq=10)
    ws2 = WorkStation(code="WS-02", name="铰链装配站", line_id=line.id, seq=20)
    ws3 = WorkStation(code="WS-03", name="下盖合装站", line_id=line.id, seq=30)
    ws4 = WorkStation(code="WS-04", name="锁螺丝站", line_id=line.id, seq=40)
    ws5 = WorkStation(code="WS-05", name="终检站", line_id=line.id, seq=50)
    db.add_all([ws1, ws2, ws3, ws4, ws5]); db.flush()

    # ---- 工艺路线 + 工序 ----
    routing = Routing(code="R-ASSY-A15-V1", name="A15外壳组装工艺", product_id=laptop_cover.id, version=1, status="active")
    db.add(routing); db.flush()

    op1 = Operation(routing_id=routing.id, seq=10, code="OP-10", name="上盖投料",
                    default_work_station_id=ws1.id, is_mandatory=True,
                    sop_text="1. 取上盖壳体\n2. 扫描壳体SN\n3. 确认无外观不良\n4. 放入载具")
    op2 = Operation(routing_id=routing.id, seq=20, code="OP-20", name="铰链装配",
                    default_work_station_id=ws2.id, is_mandatory=True,
                    sop_text="1. 取铰链组件\n2. 对位铰链孔位\n3. 预装到上盖\n4. 检查旋转顺畅度")
    op3 = Operation(routing_id=routing.id, seq=30, code="OP-30", name="下盖合装",
                    default_work_station_id=ws3.id, is_mandatory=True,
                    sop_text="1. 取下盖壳体\n2. 与上盖对位\n3. 卡扣预锁\n4. 检查间隙均匀")
    op4 = Operation(routing_id=routing.id, seq=40, code="OP-40", name="锁螺丝",
                    default_work_station_id=ws4.id, is_mandatory=True,
                    sop_text="1. 电动螺丝刀M3\n2. 扭矩 0.8±0.1 N·m\n3. 4颗对称锁付\n4. 检查无滑牙")
    op5 = Operation(routing_id=routing.id, seq=50, code="OP-50", name="终检",
                    default_work_station_id=ws5.id, is_mandatory=True,
                    sop_text="1. 外观全检\n2. 开合测试3次\n3. 间隙检查<0.3mm\n4. 贴合格标签")
    db.add_all([op1, op2, op3, op4, op5]); db.flush()

    # 工序↔作业站关联（每个工序允许的作业站）
    db.add(OperationWorkStation(operation_id=op1.id, work_station_id=ws1.id))
    db.add(OperationWorkStation(operation_id=op2.id, work_station_id=ws2.id))
    db.add(OperationWorkStation(operation_id=op3.id, work_station_id=ws3.id))
    db.add(OperationWorkStation(operation_id=op4.id, work_station_id=ws4.id))
    db.add(OperationWorkStation(operation_id=op5.id, work_station_id=ws5.id))

    # ---- BOM ----
    bom = Bom(product_id=laptop_cover.id, version=1, status="active")
    db.add(bom); db.flush()
    db.add_all([
        BomItem(bom_id=bom.id, component_product_id=top_shell.id, qty=1, track_mode="serial"),
        BomItem(bom_id=bom.id, component_product_id=bottom_shell.id, qty=1, track_mode="serial"),
        BomItem(bom_id=bom.id, component_product_id=hinge.id, qty=2, track_mode="batch"),
        BomItem(bom_id=bom.id, component_product_id=screw.id, qty=4, track_mode="batch"),
    ])

    # ---- 技能 ----
    skill_assembly = Skill(code="SK-ASSY", name="组装技能", max_level=3, description="基础组装到高级组装")
    skill_inspect = Skill(code="SK-INSPECT", name="检验技能", max_level=3, description="外观与功能检验")
    db.add_all([skill_assembly, skill_inspect]); db.flush()

    # 给 admin 和 operator 加技能
    admin = db.query(User).filter_by(username="admin").first()
    operator = db.query(User).filter_by(username="operator").first()
    if admin:
        db.add(OperatorSkill(user_id=admin.id, skill_id=skill_assembly.id, level=3))
        db.add(OperatorSkill(user_id=admin.id, skill_id=skill_inspect.id, level=3))
    if operator:
        db.add(OperatorSkill(user_id=operator.id, skill_id=skill_assembly.id, level=2))
        db.add(OperatorSkill(user_id=operator.id, skill_id=skill_inspect.id, level=1))

    # ---- SN 规则 ----
    sn_rule = SnRule(
        code="SN-NB-A15", name="A15序列号规则",
        pattern="{PREFIX}{YYYY}{SEQ:5}",
        seq_reset="year",
        product_id=laptop_cover.id,
    )
    db.add(sn_rule); db.flush()

    # ---- 工单 ----
    wo = WorkOrder(
        code="WO-2026-0807-001",
        product_id=laptop_cover.id,
        routing_id=routing.id,
        line_id=line.id,
        qty=50,
        status="released",
        sn_rule_id=sn_rule.id,
    )
    db.add(wo); db.flush()

    db.commit()
    db.close()

    print("✅ 测试数据创建完成:")
    print(f"   产品: {laptop_cover.code} {laptop_cover.name}")
    print(f"   产线: {line.code} {line.name}")
    print(f"   作业站: {ws1.name}, {ws2.name}, {ws3.name}, {ws4.name}, {ws5.name}")
    print(f"   工艺路线: {routing.code} ({op1.name}→{op2.name}→{op3.name}→{op4.name}→{op5.name})")
    print(f"   BOM: 上盖×1 + 下盖×1 + 铰链×2 + M3螺丝×4")
    print(f"   SN规则: {sn_rule.pattern} (年重置)")
    print(f"   工单: {wo.code} (qty={wo.qty}, status=released)")
    print(f"   技能: {skill_assembly.name}(L3), {skill_inspect.name}(L3)")
    print()
    print("   测试流程:")
    print("   1. 进入 工位作业 -> 选 [上盖投料站] -> 选工单 WO-2026-0807-001")
    print("   2. 扫一个载体码 (如 TRAY-001) -> 首件自动绑定SN -> 进入主界面")
    print("   3. 在主界面确认过站 -> PASS")
    print("   4. 切到铰链装配站继续过站")


if __name__ == "__main__":
    seed()
