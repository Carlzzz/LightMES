from lightmes.modules.production.defect_service import DefectService
from lightmes.modules.issue.models import Issue, IssueType


def test_log_defect_with_create_issue_true_links(
        db_session, sample_user, full_station_setup):
    """log_defect(create_issue=True) 同时建 issue + defect_id 关联。"""
    from lightmes.modules.production.models import DefectType
    quality_type = db_session.query(IssueType).filter(
        IssueType.code == "quality").one_or_none()
    if quality_type is None:
        quality_type = IssueType(code="quality", name="质量异常", severity="major")
        db_session.add(quality_type)
        db_session.flush()
    dt = DefectType(code="DT", name="DT", category="质量", severity="major", is_active=True)
    db_session.add(dt); db_session.flush()
    su = full_station_setup.serial_unit

    svc = DefectService(db_session)
    defect = svc.log_defect(
        defect_type_id=dt.id, sn=su.sn, discovered_by=sample_user.id,
        create_issue=True)
    db_session.flush()

    issue = db_session.query(Issue).filter(Issue.defect_id == defect.id).one_or_none()
    assert issue is not None
    assert issue.source == "defect_linked"
    assert issue.serial_unit_id == su.id
    assert issue.severity == "major"


def test_log_defect_without_create_issue_no_link(
        db_session, sample_user, full_station_setup):
    """默认 create_issue=False 不联动。"""
    from lightmes.modules.production.models import DefectType, DefectRecord
    dt = DefectType(code="DT2", name="DT2", category="质量", severity="major", is_active=True)
    db_session.add(dt); db_session.flush()
    su = full_station_setup.serial_unit

    svc = DefectService(db_session)
    defect = svc.log_defect(
        defect_type_id=dt.id, sn=su.sn, discovered_by=sample_user.id)
    db_session.flush()

    issues = db_session.query(Issue).filter(Issue.defect_id == defect.id).all()
    assert len(issues) == 0
