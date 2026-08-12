"""CLI entry: ``python -m lightmes.modules.connectivity.mqtt_listener``

Long-running supervisor process. Reads active connections (any protocol:
MQTT / OPC-UA / Modbus) from DB every ``RECONCILE_SECONDS`` seconds, spawns one
client task per connection, cancels them on deactivation/delete, restarts on
config change. Protocol dispatch happens in ``spawn()``.

Signals: SIGTERM/SIGINT → graceful shutdown (cancel all client tasks).

Usage::

    uv run python -m lightmes.modules.connectivity.mqtt_listener
"""
import asyncio
import logging
import signal
import sys

from lightmes.modules.connectivity.mqtt_listener.supervisor import (
    RECONCILE_SECONDS,
    reconcile,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("lightmes.connectivity.mqtt_listener")


async def main() -> int:
    """Run the supervisor loop until SIGTERM/SIGINT, then clean up. Returns exit code."""
    logger.info("MQTT listener 启动")
    stop_event = asyncio.Event()

    def _signal_handler(*_):
        logger.info("收到信号，准备关闭...")
        stop_event.set()

    # Windows 不支持 loop.add_signal_handler，回退到 signal.signal()
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)
    except NotImplementedError:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

    managed: dict[int, asyncio.Task] = {}
    sigs: dict[int, str] = {}

    async def spawn(config):
        # 已有 task（变更场景：reconcile 已 cancel_fn 旧 task → 此处只 spawn 新的）
        # 防御：若同一个 connection_id 还有残留 task，先 cancel
        existing = managed.get(config.connection_id)
        if existing is not None and not existing.done():
            existing.cancel()
        # Lazy import + dispatch by protocol
        if config.protocol == "opcua":
            from lightmes.modules.connectivity.mqtt_listener.opcua_client import (
                run_opcua_client as runner,
            )
            task_name = f"opcua-client-{config.connection_id}"
        elif config.protocol == "modbus":
            from lightmes.modules.connectivity.mqtt_listener.modbus_client import (
                run_modbus_client as runner,
            )
            task_name = f"modbus-client-{config.connection_id}"
        else:
            # mqtt (default) — 避免 --help 时也加载 aiomqtt
            from lightmes.modules.connectivity.mqtt_listener.client import (
                run_client_with_reconnect as runner,
            )
            task_name = f"mqtt-client-{config.connection_id}"

        task = asyncio.create_task(
            runner(config, stop_event),
            name=task_name,
        )
        managed[config.connection_id] = task

    def cancel(connection_id):
        task = managed.pop(connection_id, None)
        if task is not None and not task.done():
            task.cancel()
        sigs.pop(connection_id, None)

    while not stop_event.is_set():
        try:
            # reconcile 期望 sync spawn_fn/cancel_fn；用 pending_spawns 缓冲异步 spawn
            pending_spawns: list = []

            def sync_spawn(cfg):
                pending_spawns.append(cfg)

            reconcile(managed, sigs, sync_spawn, cancel)
            for cfg in pending_spawns:
                await spawn(cfg)
            # 清理已完成的 task
            done = [cid for cid, t in managed.items() if t.done()]
            for cid in done:
                task = managed.pop(cid)
                try:
                    task.result()  # 若崩溃则 raise
                except asyncio.CancelledError:
                    pass
                except Exception as e:  # noqa: BLE001
                    logger.warning("[conn %s] task 退出: %s", cid, e)
                # 注意：不在此重启 —— 下次 reconcile 会处理（若仍 active）
        except Exception as e:  # noqa: BLE001
            logger.exception("Reconcile loop 错误: %s", e)
        # 等下一个 tick（或 stop）
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RECONCILE_SECONDS)
        except asyncio.TimeoutError:
            pass

    # 关闭
    logger.info("取消所有 client task...")
    for cid, task in list(managed.items()):
        task.cancel()
    if managed:
        await asyncio.gather(*managed.values(), return_exceptions=True)
    logger.info("MQTT listener 已停止")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
