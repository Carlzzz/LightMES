"""Integration tests for MQTT listener — requires a real broker.

默认 skip。运行方式::

    RUN_BROKER_TESTS=1 TEST_MQTT_BROKER_HOST=127.0.0.1 \\
        uv run pytest tests/modules/connectivity/test_listener_integration.py -v
"""
import asyncio
import os

import pytest

BROKER_HOST = os.environ.get("TEST_MQTT_BROKER_HOST")
RUN_BROKER_TESTS = os.environ.get("RUN_BROKER_TESTS") == "1"

pytestmark = pytest.mark.skipif(
    not RUN_BROKER_TESTS or not BROKER_HOST,
    reason="需要 RUN_BROKER_TESTS=1 + TEST_MQTT_BROKER_HOST 环境变量",
)


@pytest.mark.asyncio
async def test_listener_picks_up_new_connection(db_session):
    """新建 active connection → listener 进程自动 spawn client task → 收到消息入库。"""
    from sqlalchemy import select

    from lightmes.database import SessionLocal
    from lightmes.modules.connectivity.models import MachineMessage
    from lightmes.modules.connectivity.service import ConnectivityService

    # 1. 创建 active connection + topic
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(
        name="integration-1",
        broker_host=BROKER_HOST,
        broker_port=1883,
    )
    svc.activate_connection(conn.id)
    svc.add_topic(conn.id, "test/integration/+", "plain")
    db_session.commit()

    # 2. 启动 listener 进程
    import subprocess

    proc = subprocess.Popen(
        [
            "uv", "run", "python",
            "-m", "lightmes.modules.connectivity.mqtt_listener",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        # 3. 等 listener reconcile (5s) + connect
        await asyncio.sleep(8)

        # 4. publish 一条消息
        import aiomqtt

        async with aiomqtt.Client(hostname=BROKER_HOST, port=1883) as pub:
            await pub.publish("test/integration/hello", b"world", qos=0)

        # 5. 等消息入库
        await asyncio.sleep(2)

        # 6. 验证 —— 用独立 SessionLocal 查（listener 进程写的是同一个 DB）
        db = SessionLocal()
        try:
            msgs = list(
                db.execute(
                    select(MachineMessage).where(
                        MachineMessage.machine_connection_id == conn.id
                    )
                ).scalars().all()
            )
            assert len(msgs) >= 1
            assert msgs[0].topic == "test/integration/hello"
            assert msgs[0].raw_payload == "world"
            assert msgs[0].processing_status == "ok"
        finally:
            db.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
