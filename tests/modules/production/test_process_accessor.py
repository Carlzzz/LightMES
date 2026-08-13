from lightmes.modules.production.process_snapshot import get_work_order_process


def test_process_accessor_prefers_snapshot(db_session, full_station_setup):
    wo = full_station_setup.work_order
    wo.process_snapshot = {
        "operations": [{
            "id": 999,
            "seq": 1,
            "code": "SNAP-OP",
            "name": "Snapshot Op",
            "default_work_station_id": full_station_setup.work_station_id,
            "allowed_work_station_ids": [full_station_setup.work_station_id],
            "required_skill_id": None,
            "required_level": None,
            "sop_text": None,
            "sop_url": None,
        }],
        "bom_items": [],
    }
    db_session.flush()

    process = get_work_order_process(db_session, wo)

    assert process.operations[0].code == "SNAP-OP"
