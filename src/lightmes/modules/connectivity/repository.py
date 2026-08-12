from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.connectivity.models import (
    MachineConnection, MachineMessage, MachineTopic, MqttConnection,
)


class MachineConnectionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, conn: MachineConnection) -> MachineConnection:
        self.db.add(conn); self.db.flush()
        return conn

    def get(self, conn_id: int) -> MachineConnection | None:
        return self.db.get(MachineConnection, conn_id)

    def get_by_name(self, name: str) -> MachineConnection | None:
        return self.db.execute(
            select(MachineConnection).where(MachineConnection.name == name)
        ).scalar_one_or_none()

    def list_all(self) -> list[MachineConnection]:
        return list(self.db.execute(
            select(MachineConnection).order_by(MachineConnection.id.desc())
        ).scalars().all())

    def list_active(self) -> list[MachineConnection]:
        return list(self.db.execute(
            select(MachineConnection).where(
                MachineConnection.is_active.is_(True),
                MachineConnection.protocol == "mqtt",
            )
        ).scalars().all())

    def delete(self, conn_id: int) -> None:
        c = self.get(conn_id)
        if c is not None:
            self.db.delete(c)
            self.db.flush()


class MqttConnectionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, mqtt: MqttConnection) -> MqttConnection:
        self.db.add(mqtt); self.db.flush()
        return mqtt

    def get_by_machine_connection(self, machine_conn_id: int) -> MqttConnection | None:
        return self.db.execute(
            select(MqttConnection).where(MqttConnection.machine_connection_id == machine_conn_id)
        ).scalar_one_or_none()


class MachineTopicRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, t: MachineTopic) -> MachineTopic:
        self.db.add(t); self.db.flush()
        return t

    def get(self, topic_id: int) -> MachineTopic | None:
        return self.db.get(MachineTopic, topic_id)

    def list_for_connection(self, conn_id: int) -> list[MachineTopic]:
        return list(self.db.execute(
            select(MachineTopic).where(MachineTopic.machine_connection_id == conn_id)
            .order_by(MachineTopic.id)
        ).scalars().all())

    def list_active_for_connection(self, conn_id: int) -> list[MachineTopic]:
        return list(self.db.execute(
            select(MachineTopic).where(
                MachineTopic.machine_connection_id == conn_id,
                MachineTopic.is_active.is_(True),
            )
        ).scalars().all())

    def find_active_duplicate(self, conn_id: int, pattern: str) -> MachineTopic | None:
        return self.db.execute(
            select(MachineTopic).where(
                MachineTopic.machine_connection_id == conn_id,
                MachineTopic.topic_pattern == pattern,
                MachineTopic.is_active.is_(True),
            )
        ).scalar_one_or_none()

    def delete(self, topic_id: int) -> None:
        t = self.get(topic_id)
        if t is not None:
            self.db.delete(t)
            self.db.flush()


class MachineMessageRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, m: MachineMessage) -> MachineMessage:
        self.db.add(m); self.db.flush()
        return m

    def list_recent_for_connection(self, conn_id: int, limit: int = 100) -> list[MachineMessage]:
        return list(self.db.execute(
            select(MachineMessage)
            .where(MachineMessage.machine_connection_id == conn_id)
            .order_by(MachineMessage.id.desc())
            .limit(limit)
        ).scalars().all())
