import pytest

from lightmes.modules.masterdata.schemas import (
    LineCreate,
    OperationCreate,
    ProductCreate,
    RoutingCreate,
    WorkStationCreate,
)
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.production.batch_service import BatchService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.service import ProductionService
from lightmes.shared.errors import BusinessRuleError


def _released_work_order(db_session, code: str):
    md = MasterDataService(db_session)
    product = md.create_product(ProductCreate(code=f"BP{code}", name="件", type="finished"))
    line = md.create_line(LineCreate(code=f"BL{code}", name="线"))
    station = md.create_work_station(
        WorkStationCreate(code=f"BW{code}", name="站", line_id=line.id, seq=1)
    )
    routing = md.create_routing(
        RoutingCreate(
            code=f"BR{code}",
            name="路线",
            product_id=product.id,
            operations=[
                OperationCreate(
                    seq=1,
                    code=f"BO{code}",
                    name="工序",
                    default_work_station_id=station.id,
                    allowed_work_station_ids=[station.id],
                )
            ],
        )
    )

    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(
        SnRuleCreate(code=f"BS{code}", name="r", pattern=f"BT{code}{{SEQ:4}}")
    )
    wo = prod.create_work_order(
        WorkOrderCreate(
            code=f"BWO{code}",
            product_id=product.id,
            routing_id=routing.id,
            line_id=line.id,
            qty=5,
            sn_rule_id=rule.id,
        )
    )
    prod.release_work_order(wo.id)
    return wo


def test_release_creates_one_listed_batch(db_session):
    wo = _released_work_order(db_session, "A")

    batches = BatchService(db_session).list_batches(wo.id)

    assert len(batches) == 1
    assert batches[0].batch_number == 1
    assert batches[0].status == "pending"
    assert batches[0].target_qty == 5


def test_cancel_pending_batch(db_session):
    wo = _released_work_order(db_session, "B")
    batch = BatchService(db_session).list_batches(wo.id)[0]

    result = BatchService(db_session).cancel_batch(batch.id)

    assert result.status == "cancelled"
    assert BatchService(db_session).list_batches(wo.id)[0].status == "cancelled"


def test_complete_in_process_batch(db_session):
    wo = _released_work_order(db_session, "C")
    service = BatchService(db_session)
    batch = service.list_batches(wo.id)[0]
    service.start_batch(batch.id)

    result = service.complete_batch(batch.id, produced_qty=5)

    assert result.status == "done"
    assert result.produced_qty == 5
    assert result.completed_at is not None


def test_complete_batch_rejects_negative_qty(db_session):
    wo = _released_work_order(db_session, "D")
    service = BatchService(db_session)
    batch = service.list_batches(wo.id)[0]

    with pytest.raises(BusinessRuleError):
        service.complete_batch(batch.id, produced_qty=-1)
