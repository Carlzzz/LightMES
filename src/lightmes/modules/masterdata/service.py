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
            self.boms.delete_items(existing.id)
            for comp, qty in resolved:
                self.db.add(BomItem(bom_id=existing.id,
                    component_product_id=comp.id, qty=qty, track_mode=comp.track_mode))
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
