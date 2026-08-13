from lightmes.database import SessionLocal
from lightmes.modules.production.backfill_snapshots import backfill_work_order_snapshots


def main() -> None:
    with SessionLocal() as db:
        updated = backfill_work_order_snapshots(db)
        db.commit()
    print(f"Backfilled {updated} work order snapshot(s)")


if __name__ == "__main__":
    main()
