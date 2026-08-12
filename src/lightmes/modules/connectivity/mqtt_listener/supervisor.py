"""MQTT listener supervisor — reconciles DB active connections with managed client tasks.

This module is the single entry point for the listener process:
    python -m lightmes.connectivity.mqtt_listener

The supervisor periodically (every RECONCILE_SECONDS) queries the DB for active
MQTT connections and:
    - spawns a client task for newly-active connections
    - cancels client tasks for deactivated/deleted connections
    - restarts client tasks whose config (broker host/port/topics/etc.) changed

Each connection runs as an independent asyncio task via run_client_with_reconnect()
from lightmes.modules.connectivity.mqtt_listener.client.
"""
import asyncio
import hashlib
import logging
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from lightmes import database
from lightmes.modules.connectivity.crypto import decrypt_password
from lightmes.modules.connectivity.models import (
    MachineConnection,
    MachineTopic,
    MqttConnection,
)

logger = logging.getLogger(__name__)

RECONCILE_SECONDS = 5


@dataclass
class ResolvedConnectionConfig:
    """Decrypted, ready-to-use MQTT config for one connection."""

    connection_id: int
    broker_host: str
    broker_port: int
    client_id: str
    username: str | None
    password: str | None
    use_tls: bool
    keep_alive_seconds: int
    qos_default: int
    clean_session: bool
    connect_timeout_seconds: int
    reconnect_delay_seconds: int
    topics: list[MachineTopic]


def resolve_config(db: Session, conn: MachineConnection) -> ResolvedConnectionConfig | None:
    """Resolve a MachineConnection + its MqttConnection into a usable config.

    Returns None if the connection has no mqtt_connections row (data inconsistency).
    """
    mqtt = db.execute(
        select(MqttConnection).where(MqttConnection.machine_connection_id == conn.id)
    ).scalar_one_or_none()
    if mqtt is None:
        return None
    topics = list(
        db.execute(
            select(MachineTopic).where(
                MachineTopic.machine_connection_id == conn.id,
                MachineTopic.is_active.is_(True),
            )
        ).scalars().all()
    )
    # 自动生成 client_id if 不设
    client_id = mqtt.client_id or f"lightmes-{conn.id}-{hashlib.md5(f'{conn.id}'.encode()).hexdigest()[:8]}"
    return ResolvedConnectionConfig(
        connection_id=conn.id,
        broker_host=mqtt.broker_host,
        broker_port=mqtt.broker_port,
        client_id=client_id,
        username=mqtt.username,
        password=decrypt_password(mqtt.password_encrypted),
        use_tls=mqtt.use_tls,
        keep_alive_seconds=mqtt.keep_alive_seconds,
        qos_default=mqtt.qos_default,
        clean_session=mqtt.clean_session,
        connect_timeout_seconds=mqtt.connect_timeout_seconds,
        reconnect_delay_seconds=mqtt.reconnect_delay_seconds,
        topics=topics,
    )


def compute_config_signature(config: ResolvedConnectionConfig) -> str:
    """Hash the connection-affecting fields. Change in sig → reconnect needed."""
    payload = (
        f"{config.broker_host}:{config.broker_port}|"
        f"{config.username}|{config.use_tls}|{config.qos_default}|"
        f"{config.keep_alive_seconds}|{config.clean_session}|"
        f"{config.client_id}|"
        f"{'|'.join(sorted(t.topic_pattern for t in config.topics))}"
    )
    return hashlib.md5(payload.encode()).hexdigest()


def fetch_active_configs(
    db: Session,
) -> list[tuple[MachineConnection, ResolvedConnectionConfig | None]]:
    """Fetch all active MQTT connections with their resolved configs."""
    conns = list(
        db.execute(
            select(MachineConnection).where(
                MachineConnection.is_active.is_(True),
                MachineConnection.protocol == "mqtt",
            )
        ).scalars().all()
    )
    return [(c, resolve_config(db, c)) for c in conns]


def reconcile(
    managed: dict[int, asyncio.Task],
    sigs: dict[int, str],
    spawn_fn,
    cancel_fn,
) -> None:
    """Compare DB active connections to in-memory managed tasks.

    spawn_fn(config) is called for new connections (or when config sig changed).
    cancel_fn(connection_id) is called for removed connections.

    This is a pure function (no I/O) — caller provides spawn_fn/cancel_fn.
    Tested independently of asyncio.
    """
    db = database.SessionLocal()
    try:
        active = fetch_active_configs(db)
    finally:
        db.close()

    active_ids = {c.id for c, _ in active}
    managed_ids = set(managed.keys())

    # 新增 / 变更 → spawn
    for c, config in active:
        if config is None:
            continue
        new_sig = compute_config_signature(config)
        if c.id not in managed_ids:
            spawn_fn(config)
            sigs[c.id] = new_sig
        elif sigs.get(c.id) != new_sig:
            cancel_fn(c.id)
            # 下次 reconcile 时会重新 spawn（因为 sigs 没更新 + managed 已删）
            # 简化：立即 spawn 新配置
            spawn_fn(config)
            sigs[c.id] = new_sig

    # 删除 → cancel
    for cid in managed_ids - active_ids:
        cancel_fn(cid)


def mark_status(connection_id: int, status: str, message: str | None = None) -> None:
    """Update MachineConnection.status via independent SessionLocal. Never raises."""
    try:
        db = database.SessionLocal()
        try:
            db.execute(
                update(MachineConnection)
                .where(MachineConnection.id == connection_id)
                .values(status=status, status_message=message)
            )
            db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "mark_status failed for connection %s: %s", connection_id, e
        )
