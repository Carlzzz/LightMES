import os


def test_clean_test_database_sets_truncation_marker():
    assert os.environ.get("LIGHTMES_TEST_DB_TRUNCATED") == "1"
