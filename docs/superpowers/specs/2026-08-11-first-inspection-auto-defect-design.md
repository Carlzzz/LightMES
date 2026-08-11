# 设计文档：首检 failed 自动建缺陷

- 日期：2026-08-11
- 状态：设计已获用户确认
- 上游：`2026-08-10-first-inspection-wired-into-pass-design.md`（首检接进过站，§10 标注此为 deferred）、`2026-08-10-defect-management-quarantine-design.md`（缺陷管理 + 不良品隔离）
- 定位：首检失败时自动创建 DefectRecord + 隔离 SN + 保留 FirstInspectionRecord 审计。衔接首检与缺陷管理两个已完成 spec，闭合质量检测环。

---

## 1. 背景与目标

**现状**：
- `pass_operation` 步骤 5c（首检硬卡）：首检 failed → `raise BusinessRuleError("首检不合格")` → 路由 catch + `db.rollback()` → FirstInspectionRecord 随事务回滚（**无审计**）+ SN 未隔离 + 无缺陷记录。
- 首检 spec §10 明确标注此为 deferred："首检失败记录随事务回滚（无审计）→ 缺陷管理 spec 处理"。
- 缺陷管理 spec 已交付：DefectType/DefectRecord 表 + DefectService.log_defect（自动隔离 SN）+ 三路处理决策（返工/报废/让步）。

**本 spec 目标**：
1. 首检 failed 时保留 FirstInspectionRecord（审计留痕，不回滚）。
2. 自动创建 DefectRecord（type=FIRST_INSPECTION_FAIL，severity=critical）。
3. 自动隔离 SN（status → quarantined，阻断后续过站）。
4. 仍拒绝过站（pass 不推进；操作员看到红色错误含缺陷记录号 + 隔离提示，引导去缺陷详情处理）。

**不做**：不改首检通过路径；不改缺陷处理决策；不做"首检失败自动建议返工工序"。

---

## 2. 关键决策（已确认）

| # | 主题 | 决策 |
|---|------|------|
| 1 | 系统缺陷类型 | 启动时 `DefectService.ensure_system_defect_types()` 自动创建 `FIRST_INSPECTION_FAIL`（code 唯一，severity=critical，category=质量）。仿 `AuthService.ensure_admin_user` 模式。幂等。 |
| 2 | 事务策略 | 首检 failed 时：`DefectService.log_defect_from_inspection`（创建 defect + 隔离 SN）+ `db.commit()`（保留 fi_record + defect + quarantined SN）+ `raise BusinessRuleError`。raise 后路由 `db.rollback()` 无 pending 可回滚（已 commit），状态保持。 |
| 3 | DefectService 新方法 | `log_defect_from_inspection(fi_record, sn, discovered_by, remark)` 内部调 `_get_or_create_system_defect_type("FIRST_INSPECTION_FAIL", "首检不合格", severity="critical", category="质量")` + 既有 `log_defect`。复用隔离逻辑。 |
| 4 | discoverer | `data.operator_id`（提交首检的操作员）。 |
| 5 | remark | `"首检不合格（触发：{reason}）"`——把首检触发原因带进缺陷备注，便于追溯。 |
| 6 | 边界 | SN 此时 status=in_process（5c 在步骤 1 状态检查之后），`log_defect` 不会撞 quarantined/scrapped 拒绝。 |
| 7 | 范围边界 | 不改首检通过路径；不改缺陷处理决策；不做自动建议返工工序。 |

---

## 3. 数据流

```
pass_operation(data) 步骤 5c（既有，扩展 failed 分支）:
  fi_record = submit_new_inspection(...)  # 既有，flushes FirstInspectionRecord
  if fi_record.status == "failed":
      defect = DefectService(self.db).log_defect_from_inspection(  # NEW
          fi_record=fi_record, sn=su.sn,
          discovered_by=data.operator_id,
          remark=f"首检不合格（触发：{reason}）")
      self.db.commit()  # NEW：保留 fi_record + defect + quarantined SN
      raise BusinessRuleError(
          f"首检不合格，SN 已隔离，缺陷记录 #{defect.id}。请前往 /quality/defects/{defect.id} 处理。")
  # passed 路径不变（fi_record 随主事务提交）
```

**路由层**（既有，不改）：catch DomainError → 渲染 station_view 红条。但因 commit 已发生，SN=quarantined 持久化，defect_record 持久化。操作员看到红条后扫码再过站 → 步骤 1 拒绝（已隔离）。

---

## 4. 服务层改动

### 4.1 `DefectService` 新方法（`production/defect_service.py`）

```python
SYSTEM_DEFECT_TYPES = [
    {"code": "FIRST_INSPECTION_FAIL", "name": "首检不合格",
     "category": "质量", "severity": "critical", "description": "系统自动创建：首检不合格"},
]

def _get_or_create_system_defect_type(self, code: str, name: str,
                                       severity: str, category: str,
                                       description: str | None = None) -> DefectType:
    """获取或创建系统缺陷类型（幂等，强制 is_active=True）。"""
    dt = self.db.execute(
        select(DefectType).where(DefectType.code == code)
    ).scalar_one_or_none()
    if dt is None:
        dt = DefectType(code=code, name=name, category=category,
                        severity=severity, description=description, is_active=True)
        self.db.add(dt); self.db.flush()
    return dt

def ensure_system_defect_types(self) -> None:
    """启动时调用：幂等创建系统缺陷类型。"""
    for spec in SYSTEM_DEFECT_TYPES:
        self._get_or_create_system_defect_type(**spec)
    self.db.flush()

def log_defect_from_inspection(self, fi_record, sn: str, discovered_by: int,
                                remark: str | None = None) -> DefectRecord:
    """首检失败时调用：用系统 FIRST_INSPECTION_FAIL 类型 + 既有 log_defect。"""
    dt = self._get_or_create_system_defect_type(
        code="FIRST_INSPECTION_FAIL", name="首检不合格",
        severity="critical", category="质量",
        description="系统自动创建：首检不合格")
    return self.log_defect(
        defect_type_id=dt.id, sn=sn, discovered_by=discovered_by,
        operation_id=fi_record.operation_id,
        work_station_id=fi_record.work_station_id,
        position=None, remark=remark)
```

### 4.2 `pass_operation` 步骤 5c failed 分支扩展（`operation_pass_service.py`）

原（首检 spec 交付物）：
```python
if fi_record.status == "failed":
    raise BusinessRuleError(f"首检不合格，不可过站（记录 #{fi_record.id}）")
```

改：
```python
if fi_record.status == "failed":
    defect = DefectService(self.db).log_defect_from_inspection(
        fi_record=fi_record, sn=su.sn,
        discovered_by=data.operator_id,
        remark=f"首检不合格（触发：{reason}）")
    self.db.commit()  # 保留 fi_record + defect + quarantined SN
    raise BusinessRuleError(
        f"首检不合格，SN 已隔离，缺陷记录 #{defect.id}。"
        f"请前往 /quality/defects/{defect.id} 处理。")
```

顶部 import 加 `DefectService`：
```python
from lightmes.modules.production.defect_service import DefectService
```

### 4.3 `main.py` startup 调 ensure

在 `on_startup` 既有 `AuthService(db).ensure_admin_user()` 之后加：
```python
from lightmes.modules.production.defect_service import DefectService
DefectService(db).ensure_system_defect_types()
```

---

## 5. 边界处理

| 场景 | 行为 |
|------|------|
| 首检 passed | 既有路径不变（fi_record 随主事务提交；无 defect） |
| 首检 failed | 新路径：defect 创建 + SN quarantined + commit + raise |
| 首检 failed 后操作员再扫该 SN 过站 | 步骤 1 拒绝（SN 已 quarantined），提示去缺陷详情 |
| 首检 failed 后 SN 缺陷被让步处理 | SN 回 in_process → 下次过站 5c 再判 needs_inspection（state.last_passed_at 仍 None → needs=True → 需再次提交合格首检）|
| 首检 failed 后 SN 缺陷被返工处理 | SN reworking → re-pass 时 5c 再判（needs 取决于 trigger）|
| 首检 failed 后 SN 缺陷被报废处理 | SN scrapped → 终态 |
| ensure_system_defect_types 多次调用 | 幂等（code 唯一，已存在则跳过） |
| FIRST_INSPECTION_FAIL 类型被管理员误停用 | `_get_or_create_system_defect_type` 强制 `is_active=True`（每次调用重置） |

---

## 6. 测试

### 6.1 单元（`tests/modules/production/test_first_inspection_auto_defect.py`）

- `test_fi_failed_creates_defect_and_quarantines`：提交不合格首检 → DefectRecord 创建（type=FIRST_INSPECTION_FAIL, severity=critical）+ SN=quarantined + BusinessRuleError 抛出
- `test_fi_failed_preserves_inspection_record`：FirstInspectionRecord status=failed 持久化（不回滚）
- `test_fi_failed_error_message_includes_defect_id`：错误消息含 `缺陷记录 #X` + `/quality/defects/X`
- `test_fi_passed_no_defect`：合格首检 → 无 DefectRecord（回归）
- `test_ensure_system_defect_types_idempotent`：多次调用 → 仍 1 条 FIRST_INSPECTION_FAIL 记录

### 6.2 回归

- 既有 `test_operation_pass_first_inspection.py` 6 用例（已含 failed 拒绝测试，需适配新错误消息 + 新增 SN quarantined 断言）
- 既有 `test_first_inspection_e2e.py` 3 用例
- 既有 `test_defect_service.py` 7 用例（ensure 不影响）

---

## 7. 文件改动清单

| 文件 | 改动 |
|------|------|
| `src/lightmes/modules/production/defect_service.py` | 新 `SYSTEM_DEFECT_TYPES` + `_get_or_create_system_defect_type` + `ensure_system_defect_types` + `log_defect_from_inspection` |
| `src/lightmes/modules/production/operation_pass_service.py` | 5c failed 分支：log_defect_from_inspection + commit + raise（替换原裸 raise）；import DefectService |
| `src/lightmes/main.py` | startup 加 `DefectService(db).ensure_system_defect_types()` |
| `tests/modules/production/test_first_inspection_auto_defect.py` | 新（5 用例） |
| `tests/modules/production/test_operation_pass_first_inspection.py` | 改（适配新错误消息 + SN quarantined 断言） |

无新表、无迁移。

---

## 8. 后续工作（不在本 spec）

按剩余 gap 优先级：
1. **P2i 工序级物料校验** — operation_bom 表 + per-op 校验
2. **CSRF 项目级防护** — 所有路由加 token
3. **巡检 IPQC / OQC** — 质量 ⑦ 剩余检验类型
4. **设备管理 ⑧** — 整模块
5. **Andon/异常 ⑪** — 整模块
6. **包装/物流 ⑫** — 整模块
7. **管理驾驶舱/报表**
