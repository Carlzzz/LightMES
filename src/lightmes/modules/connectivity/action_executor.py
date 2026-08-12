"""ActionExecutor — dispatches TopicMapping actions against parsed MQTT data.

Each action handler is independent: failures are caught and recorded per-mapping.
Actions commit their own writes; ActionExecutor does not manage transactions.
"""
import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.connectivity.parser import MqttMessageParser

logger = logging.getLogger(__name__)


class ActionExecutor:
    """Execute TopicMapping actions against a parsed MQTT payload.

    `execute_all` sorts mappings by priority ascending; `execute_single` runs
    one mapping through condition check → handler dispatch → result recording.
    Each action's exception is caught — the executor never re-raises.
    """

    def __init__(self, db: Session):
        self.db = db
        self.parser = MqttMessageParser()

    def execute_all(self, mappings: list, parsed_data: dict) -> list[dict]:
        """Execute all mappings sorted by priority ascending. Returns result list."""
        results = []
        for mapping in sorted(mappings, key=lambda m: m.priority):
            results.append(self.execute_single(mapping, parsed_data))
        return results

    def execute_single(self, mapping, parsed_data: dict) -> dict:
        result = {
            "mapping_id": mapping.id,
            "action_type": mapping.action_type,
            "status": "skipped",
            "message": None,
        }
        try:
            field_value = self.parser.resolve_path(mapping.field_path, parsed_data)
            if not self.parser.evaluate_condition(mapping.condition_expr, field_value):
                result["message"] = "Condition not met"
                return result
            params = mapping.action_params or {}
            outcome = self._dispatch(mapping.action_type, params, parsed_data, field_value)
            result["status"] = "ok"
            result["message"] = json.dumps(outcome) if outcome else None
        except Exception as e:
            result["status"] = "error"
            result["message"] = str(e)[:500]
            logger.warning("Action %s failed: %s", mapping.action_type, e)
        return result

    def _dispatch(self, action_type: str, params: dict, data: dict, field_value):
        handlers = {
            "log_event": self._log_event,
            "update_work_order_produced_qty": self._update_wo_qty,
            "set_work_order_status": self._set_wo_status,
            "update_serial_unit_status": self._update_sn_status,
            "create_defect": self._create_defect,
            "webhook_forward": self._webhook_forward,
        }
        handler = handlers.get(action_type)
        if handler is None:
            raise ValueError(f"未知 action 类型: {action_type}")
        return handler(params, data, field_value)

    def _resolve_param(self, params: dict, key: str, data: dict):
        """先查 {key}_path（动态解析），再查 {key}（静态值）。"""
        path = params.get(f"{key}_path")
        if path:
            return self.parser.resolve_path(path, data)
        return params.get(key)

    # ── Action handlers ──────────────────────────────────────────────

    def _log_event(self, params, data, field_value):
        """纯日志：仅记录到 actions_triggered（由调用方写入 MachineMessage）。"""
        return {"logged": True, "data": data}

    def _update_wo_qty(self, params, data, field_value):
        from lightmes.modules.production.models import WorkOrder

        order_code = self._resolve_param(params, "work_order_code", data)
        qty = self._resolve_param(params, "qty", data)
        if qty is None:
            qty = field_value
        increment = bool(params.get("qty_increment", False))
        if order_code is None:
            raise ValueError("缺少 work_order_code 或 work_order_code_path")
        wo = self.db.execute(
            select(WorkOrder).where(WorkOrder.code == order_code)
        ).scalar_one_or_none()
        if wo is None:
            raise ValueError(f"工单不存在: {order_code}")
        qty_num = int(qty) if qty is not None else 0
        if increment:
            wo.produced_qty = (wo.produced_qty or 0) + qty_num
        else:
            wo.produced_qty = qty_num
        self.db.commit()
        return {"work_order": wo.code, "produced_qty": wo.produced_qty, "increment": increment}

    def _set_wo_status(self, params, data, field_value):
        from lightmes.modules.production.models import WorkOrder

        order_code = self._resolve_param(params, "work_order_code", data)
        status = self._resolve_param(params, "status", data)
        if order_code is None or status is None:
            raise ValueError("缺少 work_order_code 或 status")
        wo = self.db.execute(
            select(WorkOrder).where(WorkOrder.code == order_code)
        ).scalar_one_or_none()
        if wo is None:
            raise ValueError(f"工单不存在: {order_code}")
        wo.status = status
        self.db.commit()
        return {"work_order": wo.code, "status": status}

    def _update_sn_status(self, params, data, field_value):
        from lightmes.modules.production.models import SerialUnit

        sn = self._resolve_param(params, "sn", data)
        status = self._resolve_param(params, "status", data)
        if sn is None or status is None:
            raise ValueError("缺少 sn 或 status")
        su = self.db.execute(
            select(SerialUnit).where(SerialUnit.sn == sn)
        ).scalar_one_or_none()
        if su is None:
            raise ValueError(f"SN 不存在: {sn}")
        su.status = status
        self.db.commit()
        return {"sn": sn, "status": status}

    def _create_defect(self, params, data, field_value):
        """自动登记缺陷：discovered_by 取系统/首个激活用户，缺则现场创建。"""
        from lightmes.modules.auth.models import User
        from lightmes.modules.production.defect_service import DefectService
        from lightmes.modules.production.models import DefectType

        sn = self._resolve_param(params, "sn", data)
        defect_type_code = self._resolve_param(params, "defect_type_code", data)
        remark = self._resolve_param(params, "remark", data) or "机器自动报告"
        if sn is None or defect_type_code is None:
            raise ValueError("缺少 sn 或 defect_type_code")
        dt = self.db.execute(
            select(DefectType).where(DefectType.code == defect_type_code)
        ).scalar_one_or_none()
        if dt is None:
            raise ValueError(f"缺陷类型不存在: {defect_type_code}")
        # discovered_by 是 NOT NULL FK；机器上报无真人，回退到首个激活用户，
        # 都没有则现场创建一个系统占位用户（幂等 by username）。
        user = self.db.execute(
            select(User).where(User.is_active.is_(True)).limit(1)
        ).scalar_one_or_none()
        if user is None:
            user = self.db.execute(
                select(User).where(User.username == "_system_machine").limit(1)
            ).scalar_one_or_none()
            if user is None:
                user = User(
                    username="_system_machine",
                    password_hash="!",
                    display_name="机器自动上报",
                    is_active=True,
                )
                self.db.add(user); self.db.flush()
        record = DefectService(self.db).log_defect(
            defect_type_id=dt.id, sn=sn,
            discovered_by=user.id, remark=remark,
        )
        self.db.commit()
        return {"defect_id": record.id, "sn": sn, "defect_type": defect_type_code}

    def _webhook_forward(self, params, data, field_value):
        """转发到外部 webhook。使用 httpx 同步客户端（从同步上下文调用）。"""
        url = params.get("url")
        method = params.get("method", "POST")
        if not url:
            raise ValueError("缺少 webhook url")
        timeout = float(params.get("timeout", 10.0))
        headers = params.get("headers") or {"Content-Type": "application/json"}
        body = data
        resp = httpx.request(method, url, json=body, headers=headers, timeout=timeout)
        if resp.status_code >= 400:
            raise ValueError(f"Webhook 返回 {resp.status_code}: {resp.text[:200]}")
        return {"status_code": resp.status_code, "url": url}
