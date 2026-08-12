"""OPC-UA client task for one connection — connect, poll node values, reconnect.

Polls each active MachineTopic.topic_pattern (treated as an OPC-UA NodeID like
``ns=2;s=Temperature``) every ``config.poll_interval_seconds`` and persists the
value as a JSON payload via ``persist_message``.

V1 only supports ``security_mode == "none"`` (anonymous or username/password).
Certificate-based security is deferred to V6 per spec §7.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

from lightmes.modules.connectivity.mqtt_listener.message_service import persist_message
from lightmes.modules.connectivity.mqtt_listener.supervisor import (
    ResolvedConnectionConfig,
    mark_status,
)

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 300  # 5 min cap


async def run_opcua_client(
    config: ResolvedConnectionConfig,
    stop_event: asyncio.Event,
) -> None:
    """Connect to OPC-UA server → poll node values → persist_message → reconnect.

    Exits cleanly when ``stop_event`` is set (supervisor cancelled this task).
    """
    # Lazy import — avoid loading asyncua on listener startup if not needed
    from asyncua import Client

    backoff = config.reconnect_delay_seconds
    while not stop_event.is_set():
        client: Client | None = None
        try:
            mark_status(config.connection_id, "connecting")
            client = Client(config.server_url or "", timeout=config.connect_timeout_seconds)
            # asyncua 2.x — set_user / set_password pre-connect; server uses them
            # during session activation. V1 supports security_mode="none" only.
            if config.username:
                client.set_user(config.username)
            if config.password:
                client.set_password(config.password)

            await client.connect()
            mark_status(config.connection_id, "connected")
            logger.info(
                "[conn %s] OPC-UA 已连接 %s，轮询 %d 个 node",
                config.connection_id,
                config.server_url,
                len(config.topics),
            )

            while not stop_event.is_set():
                for node_spec in config.topics:
                    try:
                        node = client.get_node(node_spec.topic_pattern)
                        value = await node.read_value()
                        payload = json.dumps({
                            "value": str(value),
                            "node": node_spec.topic_pattern,
                            "source_timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                        result = persist_message(
                            config.connection_id,
                            node_spec.topic_pattern,
                            payload.encode(),
                            datetime.now(timezone.utc),
                        )
                        if result.status == "error":
                            logger.warning(
                                "[conn %s] persist 失败: node=%s err=%s",
                                config.connection_id,
                                node_spec.topic_pattern,
                                result.error,
                            )
                    except Exception as e:  # noqa: BLE001
                        # 单个 node 读失败不应终止整个轮询
                        logger.warning(
                            "[conn %s] 读 node 失败 %s: %s",
                            config.connection_id, node_spec.topic_pattern, e,
                        )
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=config.poll_interval_seconds,
                    )
                    return  # stop signaled
                except asyncio.TimeoutError:
                    pass

            # stop_event set — clean shutdown
            mark_status(config.connection_id, "disconnected")
            return
        except asyncio.CancelledError:
            mark_status(config.connection_id, "disconnected")
            raise
        except Exception as e:  # noqa: BLE001
            mark_status(config.connection_id, "error", message=str(e))
            logger.warning(
                "[conn %s] OPC-UA 错误: %s；%ss 后重连",
                config.connection_id, e, backoff,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception as e:  # noqa: BLE001
                    logger.debug(
                        "[conn %s] OPC-UA disconnect 清理: %s",
                        config.connection_id, e,
                    )
