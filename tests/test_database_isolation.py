from sqlalchemy import func, select

from lightmes.modules.production.models import WorkOrder


def test_database_starts_empty(db_session):
    count = db_session.execute(select(func.count()).select_from(WorkOrder)).scalar_one()
    assert count == 0
