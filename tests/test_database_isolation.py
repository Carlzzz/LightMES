import os

from sqlalchemy.engine import make_url

import lightmes.database as db_module
from lightmes.config import get_settings


def test_clean_test_database_sets_truncation_marker():
    assert os.environ.get("LIGHTMES_TEST_DB_TRUNCATED") == "1"


def test_dedicated_test_database_url_is_used_when_configured():
    settings = get_settings()
    expected = (
        settings.test_database_url
        if settings.test_database_url and settings.environment != "production"
        else settings.database_url
    )
    assert make_url(db_module.engine.url).render_as_string(hide_password=False) == make_url(expected).render_as_string(hide_password=False)
