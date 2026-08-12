"""Async MQTT client task for one connection — connect, subscribe, receive, reconnect.

One task per active MachineConnection. Exits cleanly when ``stop_event`` is set
(supervisor cancelled this task because the connection was deactivated/deleted or
its config changed). On broker disconnect / network error, exponentially backs
off (initial = ``config.reconnect_delay_seconds``, capped at MAX_BACKOFF_SECONDS)
and reconnects.
"""
import asyncio
import logging
from datetime import datetime, timezone

from aiomqtt import Client, MqttError

from lightmes.modules.connectivity.mqtt_listener.message_service import persist_message
from lightmes.modules.connectivity.mqtt_listener.supervisor import (
    ResolvedConnectionConfig,
    mark_status,
)

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 300  # 5 min cap


async def run_client_with_reconnect(
    config: ResolvedConnectionConfig,
    stop_event: asyncio.Event,
) -> None:
    """Connect → subscribe → receive → on failure → backoff → retry.

    Exits cleanly when stop_event is set (supervisor cancelled this task).
    """
    backoff = config.reconnect_delay_seconds
    while not stop_event.is_set():
        try:
            mark_status(config.connection_id, "connecting")
            # Build TLS context if needed
            tls_params = None
            if config.use_tls:
                import ssl

                tls_params = ssl.create_default_context()

            client_kwargs = dict(
                hostname=config.broker_host,
                port=config.broker_port,
                identifier=config.client_id,
                keepalive=config.keep_alive_seconds,
                timeout=config.connect_timeout_seconds,
                clean_session=config.clean_session,
            )
            if config.username:
                client_kwargs["username"] = config.username
            if config.password:
                client_kwargs["password"] = config.password
            if tls_params:
                client_kwargs["tls_context"] = tls_params

            async with Client(**client_kwargs) as client:
                # Subscribe to all active topics
                for t in config.topics:
                    await client.subscribe(t.topic_pattern, qos=config.qos_default)
                logger.info(
                    "[conn %s] 已连接 %s:%s，订阅 %d 个 topic",
                    config.connection_id,
                    config.broker_host,
                    config.broker_port,
                    len(config.topics),
                )
                mark_status(config.connection_id, "connected")
                # Receive loop
                async for message in client.messages:
                    if stop_event.is_set():
                        break
                    try:
                        result = persist_message(
                            config.connection_id,
                            str(message.topic),
                            bytes(message.payload),
                            datetime.now(timezone.utc),
                        )
                        if result.status == "error":
                            logger.warning(
                                "[conn %s] persist 失败: topic=%s err=%s",
                                config.connection_id,
                                message.topic,
                                result.error,
                            )
                    except Exception as e:  # noqa: BLE001
                        # persist_message 不应抛，但二次防御
                        logger.exception(
                            "[conn %s] persist_message 未预期异常: %s",
                            config.connection_id,
                            e,
                        )
                # Broker disconnected cleanly (stop_event set or broker closed)
            if stop_event.is_set():
                mark_status(config.connection_id, "disconnected")
                return
            # Broker closed without stop_event → fall through to reconnect
            mark_status(
                config.connection_id, "disconnected", message="broker 主动断开"
            )
        except asyncio.CancelledError:
            # Supervisor cancelled us
            mark_status(config.connection_id, "disconnected")
            raise
        except MqttError as e:
            mark_status(config.connection_id, "error", message=str(e))
            logger.warning(
                "[conn %s] MQTT 错误: %s；%ss 后重连",
                config.connection_id, e, backoff,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                return  # stop signaled
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
        except Exception as e:  # noqa: BLE001
            mark_status(config.connection_id, "error", message=str(e))
            logger.exception(
                "[conn %s] 未预期崩溃: %s；%ss 后重连",
                config.connection_id, e, backoff,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
