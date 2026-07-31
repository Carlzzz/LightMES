from sqlalchemy import text
from lightmes.database import engine


def test_engine_connects_and_timescaledb_available():
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        row = conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname='timescaledb'")
        ).fetchone()
        assert row is not None
        assert row[0] == "timescaledb"
