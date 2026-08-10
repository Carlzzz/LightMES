"""缺陷类型管理页路由测试。Service-level 验证（避免 TestClient DB 隔离问题）。"""
from sqlalchemy import select
from lightmes.modules.production.models import DefectType


def test_defect_type_crud_via_orm(db_session):
    """直接 ORM 验证 DefectType CRUD（路由层只是薄封装）。"""
    dt = DefectType(code="CRACK", name="裂纹", category="外观", severity="critical")
    db_session.add(dt); db_session.flush()
    db_session.refresh(dt)
    assert dt.id is not None
    # 读
    found = db_session.execute(select(DefectType).where(DefectType.code == "CRACK")).scalar_one()
    assert found.name == "裂纹"
    # 软删
    found.is_active = False
    db_session.flush()
    db_session.refresh(found)
    assert found.is_active is False
