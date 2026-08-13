from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.production.models import WorkOrder


@dataclass(frozen=True)
class SnapshotOperation:
    id: int
    seq: int
    code: str
    name: str
    default_work_station_id: int
    allowed_work_station_ids: list[int]
    required_skill_id: int | None
    required_level: int | None
    sop_text: str | None
    sop_url: str | None


@dataclass(frozen=True)
class SnapshotBomItem:
    component_product_id: int
    component_code: str
    component_name: str
    qty: float
    track_mode: str
    consume_at_operation_seq: int | None


@dataclass(frozen=True)
class WorkOrderProcess:
    operations: list[SnapshotOperation]
    bom_items: list[SnapshotBomItem]


def build_process_snapshot(db: Session, work_order: WorkOrder) -> dict:
    """Freeze routing operations and active BOM at work-order release time."""
    query = MasterDataQueryService(db)
    routing = query.get_routing(work_order.routing_id)
    operations = query.get_operations(work_order.routing_id)

    operation_views = []
    for op in operations:
        allowed = query.get_allowed_work_stations(op.id)
        allowed_ids = [ws.id for ws in allowed] or [op.default_work_station_id]
        operation_views.append(
            {
                "id": op.id,
                "seq": op.seq,
                "code": op.code,
                "name": op.name,
                "default_work_station_id": op.default_work_station_id,
                "allowed_work_station_ids": allowed_ids,
                "required_skill_id": op.required_skill_id,
                "required_level": op.required_level,
                "sop_text": op.sop_text,
                "sop_url": op.sop_url,
            }
        )

    bom = query.get_active_bom(work_order.product_id)
    bom_views = []
    if bom is not None:
        for item in query.get_active_bom_items(work_order.product_id):
            component = query.get_product(item.component_product_id)
            bom_views.append(
                {
                    "component_product_id": item.component_product_id,
                    "component_code": component.code if component else "",
                    "component_name": component.name if component else "",
                    "qty": float(item.qty),
                    "track_mode": item.track_mode,
                    "consume_at_operation_seq": item.consume_at_operation_seq,
                }
            )

    return {
        "routing": {
            "id": routing.id if routing else None,
            "code": routing.code if routing else None,
            "name": routing.name if routing else None,
            "version": routing.version if routing else None,
        },
        "bom": {
            "id": bom.id if bom else None,
            "version": bom.version if bom else None,
        },
        "operations": operation_views,
        "bom_items": bom_views,
    }


def snapshot_operations(work_order: WorkOrder) -> list[SnapshotOperation]:
    snapshot = work_order.process_snapshot or {}
    return [
        SnapshotOperation(
            id=op["id"],
            seq=op["seq"],
            code=op["code"],
            name=op["name"],
            default_work_station_id=op["default_work_station_id"],
            allowed_work_station_ids=op["allowed_work_station_ids"],
            required_skill_id=op.get("required_skill_id"),
            required_level=op.get("required_level"),
            sop_text=op.get("sop_text"),
            sop_url=op.get("sop_url"),
        )
        for op in snapshot.get("operations", [])
    ]


def snapshot_bom_items(work_order: WorkOrder) -> list[SnapshotBomItem]:
    snapshot = work_order.process_snapshot or {}
    return [
        SnapshotBomItem(
            component_product_id=item["component_product_id"],
            component_code=item.get("component_code", ""),
            component_name=item.get("component_name", ""),
            qty=float(item["qty"]),
            track_mode=item["track_mode"],
            consume_at_operation_seq=item.get("consume_at_operation_seq"),
        )
        for item in snapshot.get("bom_items", [])
    ]


def has_snapshot(work_order: WorkOrder) -> bool:
    return bool(work_order.process_snapshot)


def get_work_order_process(db: Session, work_order: WorkOrder) -> WorkOrderProcess:
    if has_snapshot(work_order):
        return WorkOrderProcess(
            snapshot_operations(work_order), snapshot_bom_items(work_order)
        )

    query = MasterDataQueryService(db)
    operations = [
        SnapshotOperation(
            id=op.id,
            seq=op.seq,
            code=op.code,
            name=op.name,
            default_work_station_id=op.default_work_station_id,
            allowed_work_station_ids=[
                ws.id for ws in query.get_allowed_work_stations(op.id)
            ] or [op.default_work_station_id],
            required_skill_id=op.required_skill_id,
            required_level=op.required_level,
            sop_text=op.sop_text,
            sop_url=op.sop_url,
        )
        for op in query.get_operations(work_order.routing_id)
    ]
    bom_items = [
        SnapshotBomItem(
            component_product_id=item.component_product_id,
            component_code="",
            component_name="",
            qty=float(item.qty),
            track_mode=item.track_mode,
            consume_at_operation_seq=item.consume_at_operation_seq,
        )
        for item in query.get_active_bom_items(work_order.product_id)
    ]
    return WorkOrderProcess(operations, bom_items)
