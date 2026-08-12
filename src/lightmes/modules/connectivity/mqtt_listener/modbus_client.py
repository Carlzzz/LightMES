"""Modbus TCP client task for one connection — connect, poll registers, reconnect.

Polls each active MachineTopic.topic_pattern (treated as a register spec like
``holding_register:0:10`` → read 10 holding registers starting at address 0)
every ``config.poll_interval_seconds`` and persists the values as a JSON payload
via ``persist_message``.

V1 only reads holding registers. Coil/discrete-input/input-register support and
writes are deferred per spec §7.
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

# register-spec prefix → pymodbus async method name
_REG_TYPE_METHODS = {
    "holding_register": "read_holding_registers",
    "coil": "read_coils",
    "discrete_input": "read_discrete_inputs",
    "input_register": "read_input_registers",
}


def _parse_reg_spec(pattern: str) -> tuple[str, int, int]:
    """``holding_register:0:10`` → ('holding_register', 0, 10). Default count=1."""
    parts = pattern.split(":")
    reg_type = parts[0]
    addr = int(parts[1]) if len(parts) > 1 else 0
    count = int(parts[2]) if len(parts) > 2 else 1
    return reg_type, addr, count


async def run_modbus_client(
    config: ResolvedConnectionConfig,
    stop_event: asyncio.Event,
) -> None:
    """Connect to Modbus TCP → poll registers → persist_message → reconnect.

    Exits cleanly when ``stop_event`` is set (supervisor cancelled this task).
    """
    from pymodbus.client import AsyncModbusTcpClient

    backoff = config.reconnect_delay_seconds
    while not stop_event.is_set():
        client = None
        try:
            mark_status(config.connection_id, "connecting")
            client = AsyncModbusTcpClient(
                config.host or "",
                port=config.port or 502,
                timeout=config.connect_timeout_seconds,
            )
            await client.connect()
            if not client.connected:
                raise ConnectionError(
                    f"Modbus 连接失败: {config.host}:{config.port}"
                )
            mark_status(config.connection_id, "connected")
            logger.info(
                "[conn %s] Modbus 已连接 %s:%s slave=%s，轮询 %d 个寄存器组",
                config.connection_id,
                config.host, config.port, config.slave_id,
                len(config.topics),
            )

            while not stop_event.is_set():
                for reg_spec in config.topics:
                    try:
                        reg_type, addr, count = _parse_reg_spec(reg_spec.topic_pattern)
                        method_name = _REG_TYPE_METHODS.get(reg_type)
                        if method_name is None:
                            logger.warning(
                                "[conn %s] 未知 register 类型 %s (spec=%s)",
                                config.connection_id, reg_type, reg_spec.topic_pattern,
                            )
                            continue
                        method = getattr(client, method_name)
                        # pymodbus 3.7+ uses `slave=`; 3.14+ uses `device_id=`
                        try:
                            result = await method(
                                addr, count=count, slave=config.slave_id,
                            )
                        except TypeError:
                            # newer pymodbus (>=3.8) renamed slave → device_id
                            result = await method(
                                addr, count=count, device_id=config.slave_id,
                            )
                        if result.isError():
                            logger.warning(
                                "[conn %s] Modbus 读错误 spec=%s: %s",
                                config.connection_id, reg_spec.topic_pattern, result,
                            )
                            continue
                        values = list(result.registers) if hasattr(result, "registers") else list(result.bits)
                        payload = json.dumps({
                            "address": addr,
                            "count": count,
                            "type": reg_type,
                            "values": values,
                            "source_timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                        persist_result = persist_message(
                            config.connection_id,
                            reg_spec.topic_pattern,
                            payload.encode(),
                            datetime.now(timezone.utc),
                        )
                        if persist_result.status == "error":
                            logger.warning(
                                "[conn %s] persist 失败: spec=%s err=%s",
                                config.connection_id,
                                reg_spec.topic_pattern,
                                persist_result.error,
                            )
                    except Exception as e:  # noqa: BLE001
                        # 单个 reg 读失败不应终止整个轮询
                        logger.warning(
                            "[conn %s] 读 register 失败 %s: %s",
                            config.connection_id, reg_spec.topic_pattern, e,
                        )
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=config.poll_interval_seconds,
                    )
                    return  # stop signaled
                except asyncio.TimeoutError:
                    pass

            mark_status(config.connection_id, "disconnected")
            return
        except asyncio.CancelledError:
            mark_status(config.connection_id, "disconnected")
            raise
        except Exception as e:  # noqa: BLE001
            mark_status(config.connection_id, "error", message=str(e))
            logger.warning(
                "[conn %s] Modbus 错误: %s；%ss 后重连",
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
                    client.close()
                except Exception as e:  # noqa: BLE001
                    logger.debug(
                        "[conn %s] Modbus close 清理: %s",
                        config.connection_id, e,
                    )
