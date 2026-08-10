# 设计文档：首检接进过站

- 日期：2026-08-10
- 状态：设计已获用户确认
- 上游：`2026-08-10-p2h-dual-panorama-skip-rework-station-design.md`（P2h 完成后 §9 列出的下一项）、既有首检模型（FirstInspectionConfig/CheckItem/Record/CheckResult/State + FirstInspectionService）
- 定位：把首检从"过站后记录（fire-and-forget）"改为"过站前硬卡 + 放行"。`OperationPassService.pass_operation` 新增首检校验步骤；首检通过才能过站，失败拒绝过站。

---

## 1. 背景与目标

**现状（既有）**：
- 首检主数据 + 记录 + 状态跟踪五张表完整。
- `FirstInspectionService` 有 `create_config` / `get_config_by_operation` / `check_needs_inspection` / `create_inspection_record` / `submit_inspection` / `release_inspection`。
- `StationService.load` 已探测 `needs_inspection`（首检卡片在 station_view 显示）。
- `station_pass` 路由**已收** fi_* 表单字段，创建首检记录——但**顺序反了**：先调 `pass_operation`（过站成功），后 try/except 创建首检记录（错误吞掉）。

**问题**：操作员可以不填首检就过站；即使填了首检且不合格，过站仍然成功。首检形同虚设。

**本 spec 目标**：
1. `pass_operation` 在过站前硬卡首检——无首检数据 / 首检不合格 → 拒绝过站。
2. 首检数据随 PASS 表单一起提交（既有 UI 不变），service 层原子处理（首检提交 + 过站同一事务）。
3. 首检通过 → `FirstInspectionState.last_passed_at` 更新 + 过站推进。
4. 首检失败 → 拒绝过站（本 spec 不做 quarantine / supervisor waive，属下一个"缺陷管理 + 不良品隔离"spec）。

---

## 2. 关键决策（已确认）

| # | 主题 | 决策 |
|---|------|------|
| 1 | 首检提交位置 | `pass_operation` **内部**（与 spec §9 一致）：`OperationPassInput` 新增 `first_inspection` 参数；service 层查 config + needs + 提交评估 + 放行。首检与过站同事务（原子）。API 友好。 |
| 2 | 校验步骤位置 | 新步骤 **5c（首检）**：位于 5b（技能校验）之后。**既有 5c（BOM 累积校验）重命名为 5d**（仅注释改名，逻辑不动）。理由：技能=人不对（最便宜先卡），首检=件不符，BOM=料不齐（最后卡）。失败成本递增。 |
| 3 | 失败处理 | `status == "failed"` → `BusinessRuleError("首检不合格，不可过站")`。**不做** quarantine 状态、**不做** supervisor waive（下一个 spec 做）。 |
| 4 | 缺首检数据 | needs=True 但 `data.first_inspection is None` → `BusinessRuleError("该工序需首检，请填写首检结果后过站")`。 |
| 5 | 表单流 | 既有 UI 不变：操作员填检查项 + 点 PASS（同一表单）。路由解析 fi_* 字段构建 `FirstInspectionInput` 传 service。 |
| 6 | inspector 身份 | `inspector_id = data.operator_id`（操作员自检）。不做 inspector != operator 的职责分离（YAGNI，未来需要再加）。 |
| 7 | 跳站不卡首检 | `skip_operation` 是 supervisor 授权绕过，**不复用**首检链。既有 skip 流程不变。 |
| 8 | 范围边界 | 不改首检主数据 UI；不改 FirstInspectionConfig 触发条件逻辑；不做 quarantine_on_fail 的隔离实现；不做 require_authorization 的 waive 流程。 |

---

## 3. 数据模型

**无新表、无新迁移**。既有五张首检表 + State 跟踪足够。

`FirstInspectionService.submit_inspection` 已更新 `state.last_passed_at`（既有代码 lines ~250-256）——本 spec 复用。

---

## 4. Schema 变更（`production/schemas.py`）

新增两个 input schema：

```python
class FirstInspectionCheckResultInput(BaseModel):
    check_item_id: int
    result_type: str  # boolean/numeric/text
    boolean_value: bool | None = None
    numeric_value: float | None = None
    text_value: str | None = None
    remark: str | None = None


class FirstInspectionInput(BaseModel):
    check_results: list[FirstInspectionCheckResultInput]
    remark: str | None = None
```

`OperationPassInput` 新增可选字段：

```python
class OperationPassInput(BaseModel):
    work_station_id: int
    work_order_code: str | None = None
    sn: str | None = None
    operator_id: int | None = None
    components: list[ComponentInput] = []
    params: list[ParamInput] = []
    first_inspection: FirstInspectionInput | None = None  # 新增
```

---

## 5. 服务层

### 5.1 `FirstInspectionService.submit_new_inspection`（NEW helper）

封装"创建记录 + 提交评估"两步为一个调用，供 `pass_operation` 使用：

```python
def submit_new_inspection(
    self, config: FirstInspectionConfig, work_order_id: int, operation_id: int,
    work_station_id: int, inspector_id: int, trigger_reason: str,
    serial_unit_id: int | None,
    check_results: list[FirstInspectionCheckResultInput],
    remark: str | None = None,
) -> FirstInspectionRecord:
    """创建 + 提交首检记录，返回带最终 status (passed/failed) 的 record。"""
    record = self.create_inspection_record(
        config, work_order_id, operation_id, work_station_id,
        inspector_id, trigger_reason,
        serial_unit_id=serial_unit_id)
    return self.submit_inspection(
        FirstInspectionSubmitInput(
            record_id=record.id, check_results=check_results, remark=remark),
        inspector_id)
```

### 5.2 `OperationPassService.pass_operation` 新增步骤 5c

在步骤 5b（技能校验，~line 89-97）之后插入新步骤 5c（首检硬卡）。**既有 5c（BOM 累积校验，~line 99-130）的注释改名为 5d**（仅注释，逻辑不动）：

```python
# 5c. 首检硬卡：工序有启用的首检配置 + 触发条件命中时，必须提交合格的首检才能过站
fi_svc = FirstInspectionService(self.db)
fi_config = fi_svc.get_config_by_operation(expected.id, data.work_station_id)
if fi_config and fi_config.is_enabled:
    needs, reason, fi_state = fi_svc.check_needs_inspection(
        fi_config, wo.id, expected.id)
    if needs:
        if data.first_inspection is None or not data.first_inspection.check_results:
            raise BusinessRuleError(
                f"该工序需首检（触发：{reason}），请填写首检结果后过站")
        fi_record = fi_svc.submit_new_inspection(
            config=fi_config, work_order_id=wo.id, operation_id=expected.id,
            work_station_id=data.work_station_id, inspector_id=data.operator_id,
            trigger_reason=reason, serial_unit_id=su.id,
            check_results=data.first_inspection.check_results,
            remark=data.first_inspection.remark)
        if fi_record.status == "failed":
            raise BusinessRuleError(
                f"首检不合格，不可过站（记录 #{fi_record.id}）")
        # status == "passed"：state.last_passed_at 已在 submit_inspection 内更新，继续过站
```

文件顶部 import 加 `FirstInspectionService`：
```python
from lightmes.modules.production.quality_service import FirstInspectionService
```

### 5.3 `OperationPassService.skip_operation` 不改

跳站是 supervisor 授权绕过，不复用首检链。既有 skip 流程不变。

---

## 6. 路由改动（`production/router.py`）

### 6.1 `station_pass` 路由

**新增**：解析 fi_* 表单字段 → 构建 `FirstInspectionInput` → 传入 `OperationPassInput.first_inspection`。

在既有 components/params 解析之后、调 `pass_operation` 之前插入：

```python
# 首检：把表单字段聚合成 FirstInspectionInput
first_inspection = None
if fi_check_item_id:
    check_results = []
    for i, item_id in enumerate(fi_check_item_id):
        result_type = fi_result_type[i] if i < len(fi_result_type) else "boolean"
        check_results.append(FirstInspectionCheckResultInput(
            check_item_id=item_id,
            result_type=result_type,
            boolean_value=fi_boolean_value[i] if i < len(fi_boolean_value) else None,
            numeric_value=fi_numeric_value[i] if i < len(fi_numeric_value) else None,
            text_value=fi_text_value[i] if i < len(fi_text_value) else None,
            remark=fi_remark[i] if i < len(fi_remark) else None,
        ))
    first_inspection = FirstInspectionInput(
        check_results=check_results,
        remark=fi_overall_remark or None)
```

`OperationPassInput` 构造加 `first_inspection=first_inspection`。

**删除**：既有"AFTER pass 的首检创建逻辑"（lines ~337-357，整段 try/except）。这段逻辑移到 pass_operation 内部了。

### 6.2 测试数据处理（既有逻辑）

既有 `# 处理测试数据`（lines ~359-394）保留不动。测试数据不是首检，是另一套采集。

### 6.3 错误渲染（既有逻辑）

`pass_operation` 抛 `BusinessRuleError` 时，既有 catch 逻辑渲染回 station_view 顶部红条（lines ~312-327）。首检不合格走同一路径，无需新模板。

---

## 7. 边界处理

| 场景 | 行为 |
|------|------|
| 无 fi_config | `fi_config is None` → 跳过 5c，直接过站 |
| config 存在但 `is_enabled=False` | 跳过 5c |
| config 启用 + `needs=False`（已通过无重燃） | 跳过 5c，直接过站 |
| config 启用 + `needs=True` + `first_inspection=None` | 拒绝："该工序需首检（触发：X），请填写首检结果后过站" |
| config 启用 + needs + 提交 + 评估 passed | `state.last_passed_at` 更新，过站推进 |
| config 启用 + needs + 提交 + 评估 failed | 拒绝："首检不合格，不可过站（记录 #X）" |
| 首件过站（首道工序有 config） | `pass_operation` 内 su 已从 pending 取出，`su.id` 可用；`inspector_id=operator_id` |
| 返工 re-pass | `check_needs_inspection` 按 trigger 判定（trigger_abnormal_restart / trigger_previous_failed 等既有逻辑） |
| 跳站（skip_operation） | 不卡首检（supervisor 授权绕过） |
| 末工序 + 需首检 | 末工序完工前的最后一道首检，正常 5c 卡；通过后完工 |

---

## 8. 测试策略

### 8.1 单元测试（`tests/modules/production/test_operation_pass_first_inspection.py` 新建）

- `test_pass_no_fi_config_skips_gate`：工序无 config → 直接过站
- `test_pass_fi_config_disabled_skips_gate`：config 存在但禁用 → 直接过站
- `test_pass_fi_already_passed_no_trigger`：state.last_passed_at 有值 + 无重燃 → 直接过站
- `test_pass_fi_needs_but_no_data_blocks`：needs=True + first_inspection=None → BusinessRuleError
- `test_pass_fi_needs_passed_data_proceeds`：needs=True + 提交合格数据 → 过站 + state 更新
- `test_pass_fi_needs_failed_data_blocks`：needs=True + 提交不合格数据 → BusinessRuleError + state 未更新 last_passed_at
- `test_pass_fi_failed_leaves_no_operation_record`：首检失败时 operation_record 未写入（验证步骤 5c 在步骤 6 之前）
- `test_skip_operation_does_not_trigger_fi`：跳站不卡首检（回归）

### 8.2 回归测试

- 既有 `test_operation_pass.py` / `test_operation_pass_skill.py` / `test_operation_pass_rework_station.py` 全绿（无首检 config 的工序不受影响）
- 既有 `test_operation_pass_skip.py` 全绿（skip 不触发首检）

### 8.3 E2E

- 工序配置首检 → 操作员不填首检直接 PASS → 红条"该工序需首检"
- 操作员填合格首检 → PASS → 过站成功
- 操作员填不合格首检 → PASS → 红条"首检不合格"

---

## 9. 文件改动清单

| 文件 | 改动 |
|------|------|
| `src/lightmes/modules/production/schemas.py` | 新增 `FirstInspectionCheckResultInput` + `FirstInspectionInput`；`OperationPassInput.first_inspection` |
| `src/lightmes/modules/production/quality_service.py` | 新增 `FirstInspectionService.submit_new_inspection` helper |
| `src/lightmes/modules/production/operation_pass_service.py` | `pass_operation` 新增步骤 5c；import `FirstInspectionService` |
| `src/lightmes/modules/production/router.py` | `station_pass` 路由构建 `FirstInspectionInput` 传入；删除 AFTER-pass 首检创建逻辑 |
| `tests/modules/production/test_operation_pass_first_inspection.py` | 新测试文件（8 个用例） |

无新表、无迁移、无模板改动。

---

## 10. 后续工作（不在本 spec）

按推荐顺序：

1. **本 spec（首检接进过站）**
2. **缺陷管理 + 不良品隔离**：新 `defect_type` / `defect_record` 表 + `SerialUnit.status="quarantined"` + 首检 failed 自动隔离 + supervisor 授权 waive 流程 + 缺陷发现页面
3. **P2i 工序级物料校验**：新 `operation_bom` 关联表 + per-op 物料校验
