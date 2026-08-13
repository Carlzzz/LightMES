from datetime import datetime

from lightmes.modules.issue.models import Issue, IssueAction, IssueType


def test_issue_type_defaults(db_session):
    """新建 IssueType 默认 is_blocking=False / is_active=True。"""
    it = IssueType(code="X", name="X", severity="minor")
    db_session.add(it)
    db_session.flush()
    assert it.is_blocking is False
    assert it.is_active is True


def test_issue_defaults(db_session, sample_user):
    """新建 Issue 默认 status=open / source=manual。"""
    it = IssueType(code="X", name="X", severity="minor")
    db_session.add(it)
    db_session.flush()
    issue = Issue(
        issue_type_id=it.id, title="t", severity="minor",
        reported_by_id=sample_user.id,
    )
    db_session.add(issue)
    db_session.flush()
    assert issue.status == "open"
    assert issue.source == "manual"
    assert issue.reported_at is not None


def test_issue_action_defaults(db_session, sample_user):
    """新建 IssueAction 默认 status=open。"""
    it = IssueType(code="X", name="X", severity="minor")
    db_session.add(it); db_session.flush()
    issue = Issue(issue_type_id=it.id, title="t", severity="minor",
                  reported_by_id=sample_user.id)
    db_session.add(issue); db_session.flush()
    action = IssueAction(issue_id=issue.id, type="corrective", title="a")
    db_session.add(action); db_session.flush()
    assert action.status == "open"
