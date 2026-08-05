from sqlalchemy import select
from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import (
    Bom,
    BomItem,
    Line,
    Operation,
    OperatorSkill,
    Product,
    Routing,
    Skill,
    WorkStation,
)


class ProductRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, product: Product) -> Product:
        self.db.add(product)
        self.db.flush()
        return product

    def get(self, id: int) -> Product | None:
        return self.db.get(Product, id)

    def get_by_code(self, code: str) -> Product | None:
        return self.db.execute(
            select(Product).where(Product.code == code)
        ).scalar_one_or_none()

    def get_by_erp_ref(self, erp_ref: str) -> Product | None:
        return self.db.execute(
            select(Product).where(Product.erp_ref == erp_ref)
        ).scalar_one_or_none()

    def list_all(self) -> list[Product]:
        return list(self.db.execute(select(Product)).scalars().all())


class RoutingRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, routing: Routing) -> Routing:
        self.db.add(routing)
        self.db.flush()
        return routing

    def get(self, id: int) -> Routing | None:
        return self.db.get(Routing, id)

    def get_by_code(self, code: str) -> Routing | None:
        return self.db.execute(
            select(Routing).where(Routing.code == code)
        ).scalar_one_or_none()

    def get_active_by_product(self, product_id: int) -> Routing | None:
        return self.db.execute(
            select(Routing).where(
                Routing.product_id == product_id, Routing.status == "active"
            )
        ).scalar_one_or_none()

    def list_all(self) -> list[Routing]:
        return list(self.db.execute(select(Routing)).scalars().all())

    def operations_of(self, routing_id: int) -> list[Operation]:
        return list(
            self.db.execute(
                select(Operation)
                .where(Operation.routing_id == routing_id)
                .order_by(Operation.seq)
            ).scalars().all()
        )


class BomRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, bom: Bom) -> Bom:
        self.db.add(bom)
        self.db.flush()
        return bom

    def get(self, id: int) -> Bom | None:
        return self.db.get(Bom, id)

    def get_active_by_product(self, product_id: int) -> Bom | None:
        return self.db.execute(
            select(Bom).where(Bom.product_id == product_id, Bom.status == "active")
        ).scalar_one_or_none()

    def list_all(self) -> list[Bom]:
        return list(self.db.execute(select(Bom)).scalars().all())

    def items_of(self, bom_id: int) -> list[BomItem]:
        return list(
            self.db.execute(
                select(BomItem).where(BomItem.bom_id == bom_id)
            ).scalars().all()
        )

    def get_by_erp_ref(self, erp_ref: str) -> Bom | None:
        return self.db.execute(
            select(Bom).where(Bom.erp_ref == erp_ref)
        ).scalar_one_or_none()

    def delete_items(self, bom_id: int) -> None:
        for it in self.items_of(bom_id):
            self.db.delete(it)
        self.db.flush()


class LineRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, line: Line) -> Line:
        self.db.add(line)
        self.db.flush()
        return line

    def get(self, id: int) -> Line | None:
        return self.db.get(Line, id)

    def get_by_code(self, code: str) -> Line | None:
        return self.db.execute(
            select(Line).where(Line.code == code)
        ).scalar_one_or_none()

    def list_all(self) -> list[Line]:
        return list(self.db.execute(select(Line)).scalars().all())


class WorkStationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, ws: WorkStation) -> WorkStation:
        self.db.add(ws)
        self.db.flush()
        return ws

    def get(self, id: int) -> WorkStation | None:
        return self.db.get(WorkStation, id)

    def get_by_code(self, code: str) -> WorkStation | None:
        return self.db.execute(
            select(WorkStation).where(WorkStation.code == code)
        ).scalar_one_or_none()

    def list_by_line(self, line_id: int) -> list[WorkStation]:
        return list(self.db.execute(
            select(WorkStation)
            .where(WorkStation.line_id == line_id)
            .order_by(WorkStation.seq)
        ).scalars().all())

    def list_all(self) -> list[WorkStation]:
        return list(self.db.execute(
            select(WorkStation).order_by(WorkStation.line_id, WorkStation.seq)
        ).scalars().all())


class SkillRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, skill: Skill) -> Skill:
        self.db.add(skill); self.db.flush(); return skill

    def get(self, skill_id: int) -> Skill | None:
        return self.db.get(Skill, skill_id)

    def get_by_code(self, code: str) -> Skill | None:
        return self.db.execute(
            select(Skill).where(Skill.code == code)).scalar_one_or_none()

    def list_all(self) -> list[Skill]:
        return list(self.db.execute(select(Skill)).scalars().all())


class OperatorSkillRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, os: OperatorSkill) -> OperatorSkill:
        self.db.add(os); self.db.flush(); return os

    def get_by_user_skill(self, user_id: int, skill_id: int) -> OperatorSkill | None:
        return self.db.execute(
            select(OperatorSkill).where(
                OperatorSkill.user_id == user_id,
                OperatorSkill.skill_id == skill_id)).scalar_one_or_none()

    def list_all(self) -> list[OperatorSkill]:
        return list(self.db.execute(select(OperatorSkill)).scalars().all())
