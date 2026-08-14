from dataclasses import dataclass


@dataclass(frozen=True)
class ShapeDefinition:
    table: str
    columns: tuple[str, ...]
    where: str | None = None


class RealtimeShapeRegistry:
    """Server-defined allowlist for future live-sync / polling endpoints."""

    def __init__(self) -> None:
        self._shapes: dict[str, ShapeDefinition] = {
            "work_orders_active": ShapeDefinition(
                "work_orders",
                (
                    "id",
                    "code",
                    "product_id",
                    "routing_id",
                    "line_id",
                    "status",
                    "qty",
                    "produced_qty",
                    "priority",
                    "planned_start",
                    "planned_end",
                ),
                where="status IN ('released', 'in_process')",
            ),
            "serial_units_active": ShapeDefinition(
                "serial_units",
                (
                    "id",
                    "sn",
                    "work_order_id",
                    "product_id",
                    "status",
                    "current_operation_seq",
                    "batch_id",
                    "carrier_code",
                ),
                where="status NOT IN ('finished', 'scrapped')",
            ),
            "defects_open": ShapeDefinition(
                "defect_records",
                (
                    "id",
                    "defect_type_code",
                    "defect_type_name",
                    "severity",
                    "serial_unit_id",
                    "work_order_id",
                    "handling_status",
                    "discovered_at",
                ),
                where="handling_status = 'pending'",
            ),
        }

    def find(self, name: str) -> ShapeDefinition | None:
        return self._shapes.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._shapes)


realtime_shape_registry = RealtimeShapeRegistry()
