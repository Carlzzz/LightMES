import json

from sqlalchemy import delete
from sqlalchemy.orm import Session

from lightmes.modules.connectivity.crypto import encrypt_password
from lightmes.modules.connectivity.models import (
    MachineConnection, MachineMessage, MachineTopic, ModbusConnection,
    MqttConnection, OpcuaConnection, TopicMapping,
)
from lightmes.modules.connectivity.repository import (
    MachineConnectionRepository, MachineMessageRepository,
    MachineTopicRepository, ModbusConnectionRepository,
    MqttConnectionRepository, OpcuaConnectionRepository, TopicMappingRepository,
)
from lightmes.modules.connectivity.schemas import ConnectionCreate, TopicCreate
from lightmes.shared.errors import BusinessRuleError, NotFoundError, ValidationError

_VALID_FORMATS = {"json", "plain", "csv", "hex"}
_VALID_PROTOCOLS = {"mqtt", "opcua", "modbus"}
_VALID_ACTION_TYPES = {
    "log_event", "update_work_order_produced_qty", "set_work_order_status",
    "update_serial_unit_status", "create_defect", "webhook_forward",
}


class ConnectivityService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.conns = MachineConnectionRepository(db)
        self.mqtts = MqttConnectionRepository(db)
        self.opcuas = OpcuaConnectionRepository(db)
        self.modbuses = ModbusConnectionRepository(db)
        self.topics = MachineTopicRepository(db)
        self.messages = MachineMessageRepository(db)
        self.mappings = TopicMappingRepository(db)

    # ---- Connection ----

    def create_connection(
        self,
        *,
        name: str,
        protocol: str = "mqtt",
        description: str | None = None,
        # MQTT
        broker_host: str | None = None,
        broker_port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = False,
        keep_alive_seconds: int = 60,
        qos_default: int = 0,
        clean_session: bool = True,
        # OPC-UA
        server_url: str | None = None,
        security_mode: str = "none",
        # Modbus
        host: str | None = None,
        port: int = 502,
        slave_id: int = 1,
        # shared
        poll_interval_seconds: int = 5,
        connect_timeout_seconds: int = 10,
        reconnect_delay_seconds: int = 5,
    ) -> tuple[MachineConnection, MqttConnection | OpcuaConnection | ModbusConnection]:
        # 校验
        if protocol not in _VALID_PROTOCOLS:
            raise ValidationError(
                f"protocol 必须是 {sorted(_VALID_PROTOCOLS)} 之一: {protocol}"
            )
        # 重名检查
        if self.conns.get_by_name(name) is not None:
            raise BusinessRuleError(f"连接名称已存在: {name}")
        # 创建父行
        conn = self.conns.add(MachineConnection(
            name=name, description=description, protocol=protocol,
            is_active=False, status="disconnected"))

        if protocol == "mqtt":
            if not broker_host:
                raise ValidationError("MQTT 连接必须提供 broker_host")
            if not (1 <= broker_port <= 65535):
                raise ValidationError(f"broker_port 必须在 1-65535 之间: {broker_port}")
            if qos_default not in (0, 1, 2):
                raise ValidationError(f"qos_default 必须是 0/1/2: {qos_default}")
            sub = self.mqtts.add(MqttConnection(
                machine_connection_id=conn.id, broker_host=broker_host,
                broker_port=broker_port, username=username,
                password_encrypted=encrypt_password(password) if password else None,
                use_tls=use_tls, keep_alive_seconds=keep_alive_seconds,
                qos_default=qos_default, clean_session=clean_session,
                connect_timeout_seconds=connect_timeout_seconds,
                reconnect_delay_seconds=reconnect_delay_seconds))
        elif protocol == "opcua":
            if not server_url:
                raise ValidationError("OPC-UA 连接必须提供 server_url")
            sub = self.opcuas.add(OpcuaConnection(
                machine_connection_id=conn.id, server_url=server_url,
                security_mode=security_mode,
                username=username,
                password_encrypted=encrypt_password(password) if password else None,
                poll_interval_seconds=poll_interval_seconds,
                connect_timeout_seconds=connect_timeout_seconds,
                reconnect_delay_seconds=reconnect_delay_seconds))
        elif protocol == "modbus":
            if not host:
                raise ValidationError("Modbus 连接必须提供 host")
            if not (1 <= port <= 65535):
                raise ValidationError(f"port 必须在 1-65535 之间: {port}")
            sub = self.modbuses.add(ModbusConnection(
                machine_connection_id=conn.id, host=host, port=port,
                slave_id=slave_id, poll_interval_seconds=poll_interval_seconds,
                connect_timeout_seconds=connect_timeout_seconds,
                reconnect_delay_seconds=reconnect_delay_seconds))
        else:  # pragma: no cover — protected by _VALID_PROTOCOLS check above
            raise ValidationError(f"未支持的 protocol: {protocol}")
        return conn, sub

    def get_connection(self, conn_id: int) -> MachineConnection:
        c = self.conns.get(conn_id)
        if c is None:
            raise NotFoundError(f"连接不存在: {conn_id}")
        return c

    def get_mqtt_for_connection(self, conn_id: int) -> MqttConnection | None:
        return self.mqtts.get_by_machine_connection(conn_id)

    def get_opcua_for_connection(self, conn_id: int) -> OpcuaConnection | None:
        return self.opcuas.get_by_machine_connection(conn_id)

    def get_modbus_for_connection(self, conn_id: int) -> ModbusConnection | None:
        return self.modbuses.get_by_machine_connection(conn_id)

    def list_connections(self) -> list[MachineConnection]:
        return self.conns.list_all()

    def list_active_mqtt_connections(self) -> list[MachineConnection]:
        return self.conns.list_active()

    def activate_connection(self, conn_id: int) -> MachineConnection:
        c = self.get_connection(conn_id)
        c.is_active = True
        self.db.flush()
        return c

    def deactivate_connection(self, conn_id: int) -> MachineConnection:
        c = self.get_connection(conn_id)
        c.is_active = False
        self.db.flush()
        return c

    def delete_connection(self, conn_id: int) -> None:
        c = self.get_connection(conn_id)
        # 显式删除子行；仅依赖 DB ondelete=CASCADE 时，session identity map 会
        # 缓存陈旧子对象，导致测试和上层调用方观察到幽灵记录（已删除的
        # MqttConnection 仍能 get 到）。用 SQL delete() 直接删除并同步驱逐
        # identity map，避免 ORM delete() 与 DB CASCADE 的双删告警。
        self.db.execute(
            delete(MachineMessage).where(MachineMessage.machine_connection_id == conn_id)
        )
        self.db.execute(
            delete(MachineTopic).where(MachineTopic.machine_connection_id == conn_id)
        )
        self.db.execute(
            delete(MqttConnection).where(MqttConnection.machine_connection_id == conn_id)
        )
        self.db.execute(
            delete(OpcuaConnection).where(OpcuaConnection.machine_connection_id == conn_id)
        )
        self.db.execute(
            delete(ModbusConnection).where(ModbusConnection.machine_connection_id == conn_id)
        )
        self.conns.delete(c.id)
        self.db.flush()

    # ---- Topic ----

    def add_topic(
        self, conn_id: int, topic_pattern: str,
        payload_format: str = "json", description: str | None = None,
    ) -> MachineTopic:
        # 校验 connection 存在
        self.get_connection(conn_id)
        # 校验 format
        if payload_format not in _VALID_FORMATS:
            raise ValidationError(
                f"payload_format 必须是 {sorted(_VALID_FORMATS)} 之一: {payload_format}")
        # 重 pattern + active 检查
        if self.topics.find_active_duplicate(conn_id, topic_pattern) is not None:
            raise BusinessRuleError(
                f"同连接下已有相同的 active topic: {topic_pattern}（先停用旧的再加新）")
        return self.topics.add(MachineTopic(
            machine_connection_id=conn_id, topic_pattern=topic_pattern,
            payload_format=payload_format, description=description, is_active=True))

    def toggle_topic(self, conn_id: int, topic_id: int) -> MachineTopic:
        t = self.topics.get(topic_id)
        if t is None or t.machine_connection_id != conn_id:
            raise NotFoundError(f"topic 不存在: {topic_id}")
        # 切换前若要激活，检查是否有同 pattern active 冲突
        if not t.is_active:
            existing = self.topics.find_active_duplicate(conn_id, t.topic_pattern)
            if existing is not None and existing.id != t.id:
                raise BusinessRuleError(
                    f"已有 active 的相同 pattern topic #{existing.id}，先停用之")
        t.is_active = not t.is_active
        self.db.flush()
        return t

    def delete_topic(self, conn_id: int, topic_id: int) -> None:
        t = self.topics.get(topic_id)
        if t is None or t.machine_connection_id != conn_id:
            raise NotFoundError(f"topic 不存在: {topic_id}")
        self.topics.delete(topic_id)

    def list_topics(self, conn_id: int) -> list[MachineTopic]:
        # 若连接已删除（级联后），返回空列表；否则校验存在后返回 topic 列表。
        if self.conns.get(conn_id) is None:
            return []
        return self.topics.list_for_connection(conn_id)

    # ---- Messages ----

    def list_recent_messages(self, conn_id: int, limit: int = 100) -> list[MachineMessage]:
        self.get_connection(conn_id)
        return self.messages.list_recent_for_connection(conn_id, limit)

    # ---- Topic Mappings ----

    def add_mapping(
        self,
        conn_id: int,
        topic_id: int,
        action_type: str,
        action_params: dict | str | None = None,
        field_path: str | None = None,
        condition_expr: str | None = None,
        priority: int = 100,
        description: str | None = None,
    ) -> TopicMapping:
        t = self.topics.get(topic_id)
        if t is None or t.machine_connection_id != conn_id:
            raise NotFoundError(f"topic 不存在或不属于连接 {conn_id}: {topic_id}")
        if action_type not in _VALID_ACTION_TYPES:
            raise ValidationError(
                f"action_type 必须是 {sorted(_VALID_ACTION_TYPES)} 之一: {action_type}"
            )
        # Priority bounds
        if not (1 <= priority <= 1000):
            raise ValidationError(f"priority 必须在 1-1000 之间: {priority}")
        # Max lengths
        if field_path and len(field_path) > 255:
            raise ValidationError("field_path 太长（最多 255 字符）")
        if condition_expr and len(condition_expr) > 255:
            raise ValidationError("condition_expr 太长（最多 255 字符）")
        if description and len(description) > 255:
            raise ValidationError("description 太长（最多 255 字符）")
        # action_params 可能是 JSON 字符串（表单）或 dict（API）
        if isinstance(action_params, str):
            s = action_params.strip()
            if not s:
                action_params = None
            else:
                if len(s) > 5000:
                    raise ValidationError("action_params 太长（最多 5000 字符）")
                try:
                    action_params = json.loads(s)
                except json.JSONDecodeError:
                    raise ValidationError(
                        f"action_params 不是有效 JSON: {action_params}"
                    )
        if isinstance(action_params, dict) and not action_params:
            action_params = None
        return self.mappings.add(TopicMapping(
            machine_topic_id=topic_id, action_type=action_type,
            action_params=action_params, field_path=field_path or None,
            condition_expr=condition_expr or None, priority=priority,
            description=description, is_active=True))

    def toggle_mapping(self, conn_id: int, topic_id: int, mapping_id: int) -> TopicMapping:
        t = self.topics.get(topic_id)
        if t is None or t.machine_connection_id != conn_id:
            raise NotFoundError(f"topic 不存在或不属于连接 {conn_id}: {topic_id}")
        m = self.mappings.get(mapping_id)
        if m is None or m.machine_topic_id != topic_id:
            raise NotFoundError(f"mapping 不存在: {mapping_id}")
        m.is_active = not m.is_active
        self.db.flush()
        return m

    def delete_mapping(self, conn_id: int, topic_id: int, mapping_id: int) -> None:
        t = self.topics.get(topic_id)
        if t is None or t.machine_connection_id != conn_id:
            raise NotFoundError(f"topic 不存在或不属于连接 {conn_id}: {topic_id}")
        m = self.mappings.get(mapping_id)
        if m is None or m.machine_topic_id != topic_id:
            raise NotFoundError(f"mapping 不存在: {mapping_id}")
        self.mappings.delete(mapping_id)

    def list_mappings(self, conn_id: int, topic_id: int) -> list[TopicMapping]:
        # topic 不存在或不属于该连接时返回 []（避免级联删除后页面报错 + IDOR）
        t = self.topics.get(topic_id)
        if t is None or t.machine_connection_id != conn_id:
            return []
        return self.mappings.list_for_topic(topic_id)
