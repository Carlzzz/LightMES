from sqlalchemy import select
from sqlalchemy.orm import Session
from lightmes.modules.trace.models import GenealogyBind


class GenealogyBindRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, bind: GenealogyBind) -> GenealogyBind:
        self.db.add(bind)
        self.db.flush()
        return bind

    def get(self, id: int) -> GenealogyBind | None:
        return self.db.get(GenealogyBind, id)

    def list_active_by_parent(self, parent_sn_id: int) -> list[GenealogyBind]:
        return list(self.db.execute(
            select(GenealogyBind).where(
                GenealogyBind.parent_sn_id == parent_sn_id,
                GenealogyBind.status == "active",
            )
        ).scalars().all())

    def list_by_parent(self, parent_sn_id: int) -> list[GenealogyBind]:
        return list(self.db.execute(
            select(GenealogyBind).where(GenealogyBind.parent_sn_id == parent_sn_id)
        ).scalars().all())

    def list_active_by_component_sn(self, component_sn: str) -> list[GenealogyBind]:
        return list(self.db.execute(
            select(GenealogyBind).where(
                GenealogyBind.component_sn == component_sn,
                GenealogyBind.status == "active",
            )
        ).scalars().all())

    def list_by_component_sn(self, component_sn: str) -> list[GenealogyBind]:
        return list(self.db.execute(
            select(GenealogyBind).where(GenealogyBind.component_sn == component_sn)
        ).scalars().all())

    def list_by_component_batch(self, batch_no: str) -> list[GenealogyBind]:
        return list(self.db.execute(
            select(GenealogyBind).where(GenealogyBind.component_batch_no == batch_no)
        ).scalars().all())
