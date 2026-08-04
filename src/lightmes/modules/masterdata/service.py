from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import (
    Bom,
    BomItem,
    Line,
    Product,
    Routing,
    RoutingStep,
    Station,
    WorkStation,
)
from lightmes.modules.masterdata.repository import (
    BomRepository,
    LineRepository,
    ProductRepository,
    RoutingRepository,
    StationRepository,
    WorkStationRepository,
)
from lightmes.modules.masterdata.schemas import (
    BomCreate,
    LineCreate,
    ProductCreate,
    RoutingCreate,
    StationCreate,
    WorkStationCreate,
)


class MasterDataService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = ProductRepository(db)
        self.stations = StationRepository(db)
        self.routings = RoutingRepository(db)
        self.boms = BomRepository(db)
        self.lines = LineRepository(db)
        self.work_stations = WorkStationRepository(db)

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

    def create_station(self, data: StationCreate) -> Station:
        if self.stations.get_by_code(data.code) is not None:
            raise ValueError(f"工位编码已存在: {data.code}")
        station = Station(
            code=data.code,
            name=data.name,
            description=data.description,
            location=data.location,
        )
        return self.stations.add(station)

    def create_routing(self, data: RoutingCreate) -> Routing:
        if self.routings.get_by_code(data.code) is not None:
            raise ValueError(f"路线编码已存在: {data.code}")
        if self.products.get(data.product_id) is None:
            raise ValueError(f"产品不存在: {data.product_id}")
        seqs = [s.seq for s in data.steps]
        if len(seqs) != len(set(seqs)):
            raise ValueError("工序 seq 不能重复")
        for step in data.steps:
            if self.stations.get(step.station_id) is None:
                raise ValueError(f"工位不存在: {step.station_id}")
        has_active = self.routings.get_active_by_product(data.product_id) is not None
        routing = Routing(
            code=data.code,
            name=data.name,
            product_id=data.product_id,
            version=data.version,
            status="inactive" if has_active else "active",
        )
        self.routings.add(routing)
        for step in data.steps:
            self.db.add(RoutingStep(
                routing_id=routing.id,
                seq=step.seq,
                station_id=step.station_id,
                name=step.name,
                is_mandatory=step.is_mandatory,
            ))
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
