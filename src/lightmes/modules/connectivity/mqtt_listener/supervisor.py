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

from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes import database
from lightmes.modules.connectivity.crypto import decrypt_password
from lightmes.modules.connectivity.models import (
    MachineConnection,
    MachineTopic,
    ModbusConnection,
    MqttConnection,
    OpcuaConnection,
)

logger = logging.getLogger(__name__)

RECONCILE_SECONDS = 5


@dataclass
class ResolvedConnectionConfig:
    """Decrypted, ready-to-use config for one connection (any protocol).

    MQTT-specific fields are populated only when protocol == "mqtt";
    OPC-UA fields when protocol == "opcua"; Modbus fields when protocol == "modbus".
    """

    connection_id: int
    protocol: str
    topics: list[MachineTopic]
    # MQTT
    broker_host: str | None = None
    broker_port: int | None = None
    client_id: str | None = None
    username: str | None = None
    password: str | None = None
    use_tls: bool = False
    keep_alive_seconds: int = 60
    qos_default: int = 0
    clean_session: bool = True
    # OPC-UA
    server_url: str | None = None
    security_mode: str = "none"
    # Modbus
    host: str | None = None
    port: int | None = None
    slave_id: int = 1
    # shared
    poll_interval_seconds: int = 5
    connect_timeout_seconds: int = 10
    reconnect_delay_seconds: int = 5


def _fetch_active_topics(db: Session, conn_id: int) -> list[MachineTopic]:
    return list(
        db.execute(
            select(MachineTopic).where(
                MachineTopic.machine_connection_id == conn_id,
                MachineTopic.is_active.is_(True),
            )
        ).scalars().all()
    )


def resolve_config(db: Session, conn: MachineConnection) -> ResolvedConnectionConfig | None:
    """Resolve a MachineConnection into a usable config based on its protocol.

    Returns None if the protocol-specific sub-row is missing (data inconsistency).
    """
    topics = _fetch_active_topics(db, conn.id)

    if conn.protocol == "mqtt":
        mqtt = db.execute(
            select(MqttConnection).where(MqttConnection.machine_connection_id == conn.id)
        ).scalar_one_or_none()
        if mqtt is None:
            return None
        client_id = mqtt.client_id or f"lightmes-{conn.id}-{hashlib.md5(f'{conn.id}'.encode()).hexdigest()[:8]}"
        return ResolvedConnectionConfig(
            connection_id=conn.id,
            protocol="mqtt",
            topics=topics,
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
        )

    if conn.protocol == "opcua":
        opcua = db.execute(
            select(OpcuaConnection).where(OpcuaConnection.machine_connection_id == conn.id)
        ).scalar_one_or_none()
        if opcua is None:
            return None
        return ResolvedConnectionConfig(
            connection_id=conn.id,
            protocol="opcua",
            topics=topics,
            server_url=opcua.server_url,
            security_mode=opcua.security_mode,
            username=opcua.username,
            password=decrypt_password(opcua.password_encrypted),
            poll_interval_seconds=opcua.poll_interval_seconds,
            connect_timeout_seconds=opcua.connect_timeout_seconds,
            reconnect_delay_seconds=opcua.reconnect_delay_seconds,
        )

    if conn.protocol == "modbus":
        modbus = db.execute(
            select(ModbusConnection).where(ModbusConnection.machine_connection_id == conn.id)
        ).scalar_one_or_none()
        if modbus is None:
            return None
        return ResolvedConnectionConfig(
            connection_id=conn.id,
            protocol="modbus",
            topics=topics,
            host=modbus.host,
            port=modbus.port,
            slave_id=modbus.slave_id,
            poll_interval_seconds=modbus.poll_interval_seconds,
            connect_timeout_seconds=modbus.connect_timeout_seconds,
            reconnect_delay_seconds=modbus.reconnect_delay_seconds,
        )

    logger.warning("resolve_config: 未知 protocol %s (conn %s)", conn.protocol, conn.id)
    return None


def compute_config_signature(config: ResolvedConnectionConfig) -> str:
    """Hash the connection-affecting fields. Change in sig → reconnect needed.

    Includes protocol-specific fields so that protocol-affecting changes trigger
    a reconnect (e.g. server_url, host/port, slave_id, poll_interval).
    """
    topic_part = "|".join(sorted(t.topic_pattern for t in config.topics))
    payload = (
        f"proto={config.protocol}|"
        f"to={config.connect_timeout_seconds}|"
        f"rc={config.reconnect_delay_seconds}|"
        f"topics={topic_part}|"
    )
    if config.protocol == "mqtt":
        payload += (
            f"mqtt={config.broker_host}:{config.broker_port}|"
            f"u={config.username}|tls={config.use_tls}|qos={config.qos_default}|"
            f"ka={config.keep_alive_seconds}|cs={config.clean_session}|"
            f"cid={config.client_id}"
        )
    elif config.protocol == "opcua":
        payload += (
            f"opcua={config.server_url}|sec={config.security_mode}|"
            f"u={config.username}|poll={config.poll_interval_seconds}"
        )
    elif config.protocol == "modbus":
        payload += (
            f"modbus={config.host}:{config.port}|slave={config.slave_id}|"
            f"poll={config.poll_interval_seconds}"
        )
    return hashlib.md5(payload.encode()).hexdigest()


def fetch_active_configs(
    db: Session,
) -> list[tuple[MachineConnection, ResolvedConnectionConfig | None]]:
    """Fetch all active connections (any protocol) with their resolved configs."""
    conns = list(
        db.execute(
            select(MachineConnection).where(
                MachineConnection.is_active.is_(True),
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


def mark_status(
    connection_id: int,
    status: str,
    message: str | None = None,
    last_connected_at=None,
) -> None:
    """Update MachineConnection.status. Independent session. Never raises.

    - status="connected": 清空 status_message，记录 last_connected_at=now
        （caller 显式传 last_connected_at 时用 caller 的值）
    - 其他 status: 写 status_message（截断 500 字符）
    """
    from datetime import datetime

    try:
        db = database.SessionLocal()
        try:
            conn = db.get(MachineConnection, connection_id)
            if conn is None:
                return
            conn.status = status
            if message is not None:
                conn.status_message = message[:500]
            elif status == "connected":
                conn.status_message = None  # 成功时清空
            if last_connected_at is not None:
                conn.last_connected_at = last_connected_at
            elif status == "connected":
                conn.last_connected_at = datetime.now()
            db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "mark_status failed for connection %s status=%s: %s",
            connection_id, status, e,
        )
