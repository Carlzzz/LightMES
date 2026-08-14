from sqlalchemy import select

import pytest

from lightmes.modules.masterdata.schemas import (
    LineCreate,
    OperationCreate,
    ProductCreate,
    RoutingCreate,
    WorkStationCreate,
)
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.production.material_lot_service import MaterialLotService
from lightmes.modules.production.models import Batch
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.service import ProductionService
from lightmes.shared.errors import BusinessRuleError, NotFoundError


def _batch(db_session, *, suffix):
    md = MasterDataService(db_session)
    product = md.create_product(
        ProductCreate(
            code=f"MLVP{suffix}",
            name="批次料",
            type="component",
            track_mode="batch",
        )
    )
    line = md.create_line(LineCreate(code=f"MLVL{suffix}", name="线"))
    ws = md.create_work_station(
        WorkStationCreate(
            code=f"MLVW{suffix}",
            name="站",
            line_id=line.id,
            seq=1,
        )
    )
    routing = md.create_routing(
        RoutingCreate(
            code=f"MLVR{suffix}",
            name="线路",
            product_id=product.id,
            operations=[
                OperationCreate(
                    seq=1,
                    code="OP1",
                    name="工序1",
                    default_work_station_id=ws.id,
                    allowed_work_station_ids=[ws.id],
                )
            ],
        )
    )
    production = ProductionService(db_session)
    rule = production.create_sn_rule(
        SnRuleCreate(code=f"MLVS{suffix}", name="r", pattern=f"MLV{suffix}{{SEQ:4}}")
    )
    work_order = production.create_work_order(
        WorkOrderCreate(
            code=f"MLVO{suffix}",
            product_id=product.id,
            routing_id=routing.id,
            line_id=line.id,
            qty=1,
            sn_rule_id=rule.id,
        )
    )
    production.release_work_order(work_order.id)
    batch = db_session.execute(
        select(Batch).where(Batch.work_order_id == work_order.id)
    ).scalar_one()
    return product, batch


@pytest.mark.parametrize("quantity", [0, -1])
def test_consume_rejects_nonpositive_quantity(db_session, quantity):
    product, batch = _batch(db_session, suffix="E")
    service = MaterialLotService(db_session)

    with pytest.raises(BusinessRuleError, match="消耗数量必须大于 0"):
        service.consume(
            batch_id=batch.id,
            operation_record_id=None,
            product_id=product.id,
            lot_code="NO-SUCH-LOT",
            quantity=quantity,
        )


@pytest.mark.parametrize("quantity", [0, -1])
def test_return_consumed_rejects_nonpositive_quantity(db_session, quantity):
    service = MaterialLotService(db_session)

    with pytest.raises(BusinessRuleError, match="回补数量必须大于 0"):
        service.return_consumed(
            material_lot_id=999999,
            quantity=quantity,
            reason="test",
        )


def test_return_consumed_rejects_missing_lot(db_session):
    service = MaterialLotService(db_session)

    with pytest.raises(NotFoundError) as exc:
        service.return_consumed(
            material_lot_id=999999,
            quantity=5,
            reason="test",
        )

    assert "物料批次不存在" in str(exc.value)


def test_return_consumed_rejects_over_return(db_session):
    product, batch = _batch(db_session, suffix="F")
    service = MaterialLotService(db_session)
    lot = service.receive(code="LOT-OVERRETURN", product_id=product.id, quantity=10)
    service.release(lot.code)

    service.consume(
        batch_id=batch.id,
        operation_record_id=None,
        product_id=product.id,
        lot_code=lot.code,
        quantity=3,
    )
    service.return_consumed(material_lot_id=lot.id, quantity=1, reason="第一次回补")

    with pytest.raises(BusinessRuleError, match="超过已消耗数量"):
        service.return_consumed(material_lot_id=lot.id, quantity=3, reason="超退回补")


def test_receive_rejects_missing_product(db_session):
    service = MaterialLotService(db_session)

    with pytest.raises(NotFoundError, match="产品不存在"):
        service.receive(code="LOT-NOP", product_id=999999, quantity=10)


def test_receive_rejects_non_batch_product(db_session):
    md = MasterDataService(db_session)
    product = md.create_product(
        ProductCreate(
            code="MLVNS",
            name="唯一件",
            type="component",
            track_mode="serial",
        )
    )
    service = MaterialLotService(db_session)

    with pytest.raises(BusinessRuleError, match="批次跟踪"):
        service.receive(code="LOT-SER", product_id=product.id, quantity=10)
