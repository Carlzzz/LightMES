from datetime import datetime, timezone
from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import (
    Bom,
    BomItem,
    Line,
    Operation,
    Product,
    Routing,
    WorkStation,
)
from lightmes.modules.masterdata.repository import (
    BomRepository,
    LineRepository,
    OperationWorkStationRepository,
    ProductRepository,
    RoutingRepository,
    SkillRepository,
    WorkStationRepository,
)
from lightmes.modules.masterdata.schemas import (
    BomCreate,
    BomUpsert,
    LineCreate,
    ProductCreate,
    ProductUpsert,
    RoutingCreate,
    WorkStationCreate,
)
from lightmes.modules.production.repository import WorkOrderRepository


class MasterDataService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = ProductRepository(db)
        self.routings = RoutingRepository(db)
        self.boms = BomRepository(db)
        self.lines = LineRepository(db)
        self.work_stations = WorkStationRepository(db)
        self.skills = SkillRepository(db)
        self.op_work_stations = OperationWorkStationRepository(db)
        self.wo_repo = WorkOrderRepository(db)  # 工单引用校验用

    def create_product(self, data: ProductCreate) -> Product:
        if self.products.get_by_code(data.code) is not None:
            raise ValueError(f"产品编码已存在: {data.code}")
        product = Product(
            code=data.code,
            name=data.name,
            type=data.type,
            unit=data.unit,
            track_mode=data.track_mode,
            spec=data.spec,
        )
        return self.products.add(product)

    def create_routing(self, data: RoutingCreate) -> Routing:
        if self.routings.get_by_code(data.code) is not None:
            raise ValueError(f"路线编码已存在: {data.code}")
        if self.products.get(data.product_id) is None:
            raise ValueError(f"产品不存在: {data.product_id}")
        seqs = [o.seq for o in data.operations]
        if len(seqs) != len(set(seqs)):
            raise ValueError("工序 seq 不能重复")
        for op in data.operations:
            if self.work_stations.get(op.default_work_station_id) is None:
                raise ValueError(f"作业站不存在: {op.default_work_station_id}")
            # 多对多校验：allowed 非空 + default ∈ allowed + 每个 ws 存在
            if not op.allowed_work_station_ids:
                raise ValueError(f"工序 {op.seq} 必须至少指定一个允许作业站")
            # dedup check: 重复 ws_id 会撞 DB unique 约束 → 抛 clean ValueError（API→400）
            if len(set(op.allowed_work_station_ids)) != len(op.allowed_work_station_ids):
                raise ValueError(f"工序 {op.seq} 允许作业站列表存在重复")
            if op.default_work_station_id not in op.allowed_work_station_ids:
                raise ValueError(
                    f"工序 {op.seq} 默认作业站必须在允许作业站列表内")
            for ws_id in op.allowed_work_station_ids:
                if self.work_stations.get(ws_id) is None:
                    raise ValueError(f"作业站不存在: {ws_id}")
            if op.required_skill_id is not None:
                skill = self.skills.get(op.required_skill_id)
                if skill is None:
                    raise ValueError(f"技能不存在: {op.required_skill_id}")
                if op.required_level is None or op.required_level < 1:
                    raise ValueError(f"工序 {op.seq} 设置了技能要求，必须填写要求等级(>=1)")
                if op.required_level > skill.max_level:
                    raise ValueError(
                        f"工序 {op.seq} 要求等级 L{op.required_level} 超过技能『{skill.name}』最高等级 L{skill.max_level}")
        has_active = self.routings.get_active_by_product(data.product_id) is not None
        routing = Routing(
            code=data.code,
            name=data.name,
            product_id=data.product_id,
            version=data.version,
            status="inactive" if has_active else "active",
        )
        self.routings.add(routing)
        for op in data.operations:
            operation = Operation(
                routing_id=routing.id,
                seq=op.seq,
                code=op.code,
                name=op.name,
                default_work_station_id=op.default_work_station_id,
                is_mandatory=op.is_mandatory,
                required_skill_id=op.required_skill_id,
                required_level=op.required_level,
            )
            self.db.add(operation); self.db.flush()  # 拿到 operation.id
            for ws_id in op.allowed_work_station_ids:
                self.op_work_stations.add(operation.id, ws_id)
        self.db.flush()
        return routing

    def _check_no_work_order(self, routing_id: int) -> None:
        n = self.wo_repo.count_by_routing(routing_id)
        if n > 0:
            raise ValueError(f"该路线已被 {n} 个工单引用，请先处理工单")

    def update_routing_head(self, routing_id: int, name: str) -> Routing:
        routing = self.routings.get(routing_id)
        if routing is None:
            raise ValueError(f"路线不存在: {routing_id}")
        if not name.strip():
            raise ValueError("路线名称不能为空")
        self._check_no_work_order(routing_id)
        routing.name = name.strip()
        self.db.flush()
        return routing

    def set_routing_status(self, routing_id: int, status: str) -> Routing:
        if status not in ("active", "inactive"):
            raise ValueError(f"无效状态: {status}")
        routing = self.routings.get(routing_id)
        if routing is None:
            raise ValueError(f"路线不存在: {routing_id}")
        self._check_no_work_order(routing_id)
        if status == "active":
            other = self.routings.get_active_by_product(routing.product_id)
            if other is not None and other.id != routing_id:
                raise ValueError(
                    f"该产品已有 active 路线 #{other.id}（{other.code}），请先设为 inactive")
        routing.status = status
        self.db.flush()
        return routing

    def _validate_op_fields(self, routing_id, seq, code, name,
                            default_work_station_id, allowed_work_station_ids,
                            required_skill_id, required_level, exclude_op_id=None):
        # default 存在 + allowed 非空 + default ∈ allowed + 每个 ws 存在 + 无重复
        if self.work_stations.get(default_work_station_id) is None:
            raise ValueError(f"作业站不存在: {default_work_station_id}")
        if not allowed_work_station_ids:
            raise ValueError(f"工序 {seq} 必须至少指定一个允许作业站")
        if len(set(allowed_work_station_ids)) != len(allowed_work_station_ids):
            raise ValueError(f"工序 {seq} 允许作业站列表存在重复")
        if default_work_station_id not in allowed_work_station_ids:
            raise ValueError(f"工序 {seq} 默认作业站必须在允许作业站列表内")
        for ws_id in allowed_work_station_ids:
            if self.work_stations.get(ws_id) is None:
                raise ValueError(f"作业站不存在: {ws_id}")
        # seq 唯一（同 routing 内）
        existing = self.routings.operations_of(routing_id)
        for o in existing:
            if o.id != exclude_op_id and o.seq == seq:
                raise ValueError(f"工序 seq={seq} 与已有工序冲突")
        # 技能等级校验（沿用 P2c）
        if required_skill_id is not None:
            skill = self.skills.get(required_skill_id)
            if skill is None:
                raise ValueError(f"技能不存在: {required_skill_id}")
            if required_level is None or required_level < 1:
                raise ValueError(f"工序 {seq} 设置了技能要求，必须填写要求等级(>=1)")
            if required_level > skill.max_level:
                raise ValueError(f"工序 {seq} 要求等级超过技能最高等级")
        # code 唯一（同 routing 内，uq_operation_routing_code）
        for o in existing:
            if o.id != exclude_op_id and o.code == code:
                raise ValueError(f"工序码 {code} 与已有工序冲突")

    def update_operation(self, operation_id: int, *, seq, code, name,
                         default_work_station_id, allowed_work_station_ids,
                         required_skill_id, required_level, is_mandatory=True) -> Operation:
        op = self.db.get(Operation, operation_id)
        if op is None:
            raise ValueError(f"工序不存在: {operation_id}")
        self._check_no_work_order(op.routing_id)
        code = code.strip(); name = name.strip()
        self._validate_op_fields(op.routing_id, seq, code, name,
                                 default_work_station_id, allowed_work_station_ids,
                                 required_skill_id, required_level, exclude_op_id=operation_id)
        op.seq = seq; op.code = code; op.name = name
        op.default_work_station_id = default_work_station_id
        op.is_mandatory = is_mandatory
        op.required_skill_id = required_skill_id
        op.required_level = required_level
        # 重写关联表
        self.op_work_stations.delete_by_operation(operation_id)
        for ws_id in allowed_work_station_ids:
            self.op_work_stations.add(operation_id, ws_id)
        self.db.flush()
        return op

    def add_operation(self, routing_id: int, *, seq, code, name,
                      default_work_station_id, allowed_work_station_ids,
                      required_skill_id, required_level, is_mandatory=True) -> Operation:
        routing = self.routings.get(routing_id)
        if routing is None:
            raise ValueError(f"路线不存在: {routing_id}")
        self._check_no_work_order(routing_id)
        code = code.strip(); name = name.strip()
        self._validate_op_fields(routing_id, seq, code, name,
                                 default_work_station_id, allowed_work_station_ids,
                                 required_skill_id, required_level)
        op = Operation(routing_id=routing_id, seq=seq, code=code, name=name,
                       default_work_station_id=default_work_station_id,
                       is_mandatory=is_mandatory,
                       required_skill_id=required_skill_id,
                       required_level=required_level)
        self.db.add(op); self.db.flush()
        for ws_id in allowed_work_station_ids:
            self.op_work_stations.add(op.id, ws_id)
        self.db.flush()
        return op

    def delete_operation(self, operation_id: int) -> None:
        op = self.db.get(Operation, operation_id)
        if op is None:
            raise ValueError(f"工序不存在: {operation_id}")
        self._check_no_work_order(op.routing_id)
        self.op_work_stations.delete_by_operation(operation_id)
        self.db.delete(op); self.db.flush()

    def delete_routing(self, routing_id: int) -> None:
        routing = self.routings.get(routing_id)
        if routing is None:
            raise ValueError(f"路线不存在: {routing_id}")
        self._check_no_work_order(routing_id)
        # 先删 operations（关联表跟随 CASCADE），再删 routing
        for op in self.routings.operations_of(routing_id):
            self.op_work_stations.delete_by_operation(op.id)
            self.db.delete(op)
        self.db.flush()
        self.routings.delete(routing_id)

    def create_bom(self, data: BomCreate) -> Bom:
        if self.products.get(data.product_id) is None:
            raise ValueError(f"产品不存在: {data.product_id}")
        comp_ids = [i.component_product_id for i in data.items]
        if len(comp_ids) != len(set(comp_ids)):
            raise ValueError("BOM 行组件不能重复")
        components = {}
        for item in data.items:
            comp = self.products.get(item.component_product_id)
            if comp is None:
                raise ValueError(f"组件不存在: {item.component_product_id}")
            components[item.component_product_id] = comp
        has_active = self.boms.get_active_by_product(data.product_id) is not None
        bom = Bom(
            product_id=data.product_id,
            version=data.version,
            status="inactive" if has_active else "active",
        )
        self.boms.add(bom)
        for item in data.items:
            self.db.add(BomItem(
                bom_id=bom.id,
                component_product_id=item.component_product_id,
                qty=item.qty,
                track_mode=components[item.component_product_id].track_mode,
                consume_at_operation_seq=item.consume_at_operation_seq,
            ))
        self.db.flush()
        return bom

    def create_line(self, data: LineCreate) -> Line:
        if self.lines.get_by_code(data.code) is not None:
            raise ValueError(f"产线编码已存在: {data.code}")
        line = Line(code=data.code, name=data.name, description=data.description)
        return self.lines.add(line)

    def create_work_station(self, data: WorkStationCreate) -> WorkStation:
        if self.work_stations.get_by_code(data.code) is not None:
            raise ValueError(f"作业站编码已存在: {data.code}")
        if self.lines.get(data.line_id) is None:
            raise ValueError(f"产线不存在: {data.line_id}")
        ws = WorkStation(
            code=data.code, name=data.name, line_id=data.line_id,
            seq=data.seq, description=data.description,
        )
        return self.work_stations.add(ws)

    def upsert_product(self, data: "ProductUpsert") -> tuple[Product, str]:
        existing = self.products.get_by_erp_ref(data.erp_ref)
        if existing is not None:
            existing.code = data.code
            existing.name = data.name
            existing.type = data.type
            existing.unit = data.unit
            existing.track_mode = data.track_mode
            existing.spec = data.spec
            existing.synced_at = datetime.now(timezone.utc)
            self.db.flush()
            return existing, "updated"
        product = Product(
            code=data.code, name=data.name, type=data.type, unit=data.unit,
            track_mode=data.track_mode, spec=data.spec,
            source="erp", erp_ref=data.erp_ref,
            synced_at=datetime.now(timezone.utc),
        )
        return self.products.add(product), "created"

    def upsert_bom(self, data: "BomUpsert") -> tuple[Bom, str]:
        product = self.products.get_by_code(data.product_code)
        if product is None:
            raise ValueError(f"成品不存在: {data.product_code}")
        resolved = []
        for it in data.items:
            comp = self.products.get_by_code(it.component_code)
            if comp is None:
                raise ValueError(f"组件不存在: {it.component_code}")
            resolved.append((comp, it.qty))
        existing = self.boms.get_by_erp_ref(data.erp_ref)
        if existing is not None:
            if product.id != existing.product_id:
                raise ValueError(f"BOM {data.erp_ref} 的成品与已存在记录不一致")
            # preserve admin-configured consume_at_operation_seq before deleting
            old_items = self.boms.items_of(existing.id)
            preserved_consume_op: dict[int, int | None] = {
                i.component_product_id: i.consume_at_operation_seq for i in old_items}
            self.boms.delete_items(existing.id)
            for comp, qty in resolved:
                self.db.add(BomItem(bom_id=existing.id,
                    component_product_id=comp.id, qty=qty, track_mode=comp.track_mode,
                    consume_at_operation_seq=preserved_consume_op.get(comp.id)))
            existing.synced_at = datetime.now(timezone.utc)
            self.db.flush()
            return existing, "updated"
        bom = Bom(product_id=product.id, source="erp", erp_ref=data.erp_ref,
                  synced_at=datetime.now(timezone.utc))
        self.boms.add(bom)
        for comp, qty in resolved:
            self.db.add(BomItem(bom_id=bom.id, component_product_id=comp.id,
                qty=qty, track_mode=comp.track_mode))
        self.db.flush()
        return bom, "created"
