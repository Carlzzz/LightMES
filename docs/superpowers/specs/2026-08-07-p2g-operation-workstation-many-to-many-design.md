# P2g 设计文档：工序-作业站多对多 + 连续过站

- 日期：2026-08-07
- 状态：设计已获用户确认
- 上游：`2026-08-05-p2a-hierarchy-traceability-design.md`（operation.default_work_station_id 单值 FK）、`2026-08-05-p2d-operator-station-design.md`（工位作业富主界面）、`2026-08-06-p2f-station-main-interface-redesign.md`（一站式入口）
- 定位：把"工序↔作业站"从单值 FK 改成两层模型——保留默认作业站（建议/展示），新增多对多关联表（"该工序允许在哪些作业站做"）；过站判定由"等于默认"改为"属于允许集合"；同一作业站上连续做完多个工序（不切回扫码页）。

---

## 1. 背景与目标

现状（P2a 起）：`operation.default_work_station_id` 单值 FK，把工序与物理作业站焊死。现实：一个作业站（物理单元）可能装多套工具/PLC，能在其上做多个不同工序；同一工序也可能在多个作业站做（柔性产线、交叉安排）。

P2g 改为两层模型：
1. **关联表**：`operation_work_stations` 多对多，存"某工序允许在哪些作业站做"。
2. **默认作业站**：保留 `default_work_station_id`（建议/路径全景展示），但必须 ∈ 关联表允许集合。
3. **过站判定**：从"`ws_id == default_work_station_id`"改为"`ws_id IN allowed_work_stations`"。
4. **连续过站 UX**：PASS 成功后若下一工序也允许在本站做 → 直接刷新富界面到下一工序（不回扫码页）；否则提示切站。
5. **混线**：作业站不绑单一产品/工艺；扫码后按 SN 的路线解析当前工序再校验。

---

## 2. 关键决策（已确认）

| # | 主题 | 决策 |
|---|---|---|
| 1 | 关联建模 | 保留 `default_work_station_id`（默认/建议作业站）+ 新增 `operation_work_stations` 多对多关联表；default 必须在关联表允许集合里（service 层校验）。 |
| 2 | 允许集合来源 | 主数据**手工维护**：工序编辑页多选作业站（`allowed_work_station_ids`，options 列所有 active 站带产线名标签）+ 选默认（从已选里挑）。过站时第一层仍兜底拦异产线。 |
| 2b | 全景显示 | 路径全景每个工序节点显示完整 allowed 站名列表（如"OP10 上线（可在：站A/站B）"），而非只显默认站名。`StationOpView` 加 `allowed_work_stations: list[str]`。 |
| 3 | 连续过站交互 | PASS 成功：next 工序 allowed 含本站 → 刷富界面到下一工序（不回扫码页）；不含 → 提示"请切到【XX 站】"。 |
| 4 | 混线 | 作业站不绑单一产品；扫码后按 SN 路线解析当前工序，再校验该工序是否允许在本站做。 |
| 5 | 防跳站第一层 | 保持不变：作业站 line_id 须 = 工单 line_id（跨产线拒绝）。 |
| 6 | OperationPassResult.next_op | 仍填 `default_work_station_id`（建议，不强制）；新增 `next_op_can_continue_here: bool`（next_op 的 allowed 含当前 work_station_id）。 |
| 7 | 数据迁移 | 对所有现有 operation，往关联表插 `(operation_id, default_work_station_id)`——让现状数据天然满足"默认站也在允许集合"。不删 default_work_station_id 列。 |
| 8 | 范围边界 | 不改 Line/WorkStation 模型；不建作业站分组/区域；不引入跨产线工序（仍由第一层拦）。 |

---

## 3. 数据模型

**新增 `OperationWorkStation` 关联表**（`masterdata/models.py`）：
```python
class OperationWorkStation(Base, TimestampMixin):
    __tablename__ = "operation_work_stations"
    __table_args__ = (
        UniqueConstraint("operation_id", "work_station_id",
                         name="uq_operation_work_station"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("operations.id", ondelete="CASCADE"))
    work_station_id: Mapped[int] = mapped_column(ForeignKey("work_stations.id"))
```

**`Operation` 保留** `default_work_station_id: Mapped[int]`（不变）。

**迁移**：一条 Alembic 迁移——`create_table operation_work_stations`（含 FK + 唯一约束）+ 数据迁移（对每个现有 operation 插一条 `(operation_id, default_work_station_id)`）。不删任何列/索引；不误删 uq_active_*/uq_operation_*/uq_*_erp_ref/uq_bom_item_component/uq_operator_skill_user_skill。

**`OperationCreate` schema 变更**（`masterdata/schemas.py`）：
```python
class OperationCreate(BaseModel):
    seq: int
    code: str
    name: str
    default_work_station_id: int
    allowed_work_station_ids: list[int]  # 新增：至少 1 个；必须含 default_work_station_id
    is_mandatory: bool = True
    required_skill_id: int | None = None
    required_level: int | None = None
```

**WorkStation / Line 不变**。

---

## 4. 服务层

**`MasterDataQueryService` 新增只读方法**：
- `get_allowed_work_stations(operation_id: int) -> list[WorkStation]`：返回该工序允许的作业站列表（join operation_work_stations）。

**`MasterDataService.create_routing` 扩展**（建工序时同时写关联表）：
- 对每个 op：校验 `allowed_work_station_ids` 非空且含 `default_work_station_id`；校验每个 ws 存在；INSERT 关联表行；保持现有 operation 行 INSERT。
- 同事务；失败整批回滚（沿用既有事务边界）。
- 若 op 既有 `required_skill_id`/`required_level`，沿用 P2c 校验（不变）。

**`OperationPassService.pass_operation` 防跳站第二层改写**（`operation_pass_service.py` 第 73-80 行）：
- 现状：`if data.work_station_id != expected.default_work_station_id: raise BusinessRuleError(...)`。
- 新：
```python
allowed = self.query.get_allowed_work_stations(expected.id)
allowed_ids = [ws.id for ws in allowed]
if data.work_station_id not in allowed_ids:
    names = "、".join(ws.name for ws in allowed) or f"作业站 #{expected.default_work_station_id}"
    raise BusinessRuleError(
        f"该 SN 当前工序 {expected.seq} {expected.name} "
        f"应在【{names}】之一作业站做，当前作业站不符")
```
- 第一层（`ws.line_id != wo.line_id`）、第三层（pending/技能/乐观锁/完工）不变。

**`StationService.load` off-station 判定同步改写**：
- 现状：`is_off_station = expected.default_work_station_id != work_station_id` → raise。
- 新：`allowed = get_allowed_work_stations(expected.id)`；`if work_station_id not in [ws.id for ws in allowed]: raise BusinessRuleError(同上消息)`。

**`OperationPassResult` 新增字段**：
- `next_op_can_continue_here: bool = False`：在 pass_operation 末尾计算——`next_op is not None and work_station_id in get_allowed_work_stations(next_op operation id)`。

---

## 5. 交互 / 路由

**`POST /production/station/pass` 成功分流**（`router.py`）：
- 若 `result.is_finished`：渲染既有完工提示片段。
- elif `result.next_op_can_continue_here`：调 `StationService(db).load(scan=su.sn, work_station_id=work_station_id, operator_id=user.id)` 组装下一工序 StationView，渲染 `station_view.html` 替换 `#station-root`（操作员继续 PASS，不回扫码页）。注意 scan 用 SN（pass 已知 result.sn）。
- else：渲染 `station_pass_result.html` 切站提示分支（带 `result.next_op` 站名 + work_station_id），提示"请切到【XX 站】"，不带 enter 表单（因为要切站）。

**`station_pass_result.html` 模板**：
- 完工分支（既有）
- **新增切站分支**：`{% elif result.next_op and not result.next_op_can_continue_here %}` → 显示"✓ 已过工序 X，下一工序 Y 应在【站 Z】做，请切换作业站" + 返回就绪页链接（带 work_station_id）。
- **既有连续扫码分支**改为只在"无 next_op 或本站连续"之外不渲染（被新分流覆盖）。

**主数据维护 UI**（`/masterdata/routings` 工序编辑）：
- 工序表单加 `allowed_work_station_ids` 多选（`<select multiple name="allowed_work_station_ids">`，options = **本工序所属路线的产品可生产的产线下的所有作业站**——UI 联动按产线过滤，避免选异产线作业站）。具体：路线已知 product_id；产品无"可生产产线"概念时，简化为**所有 is_active=true 的作业站** + 提交后由过站第一层（`ws.line_id != wo.line_id`）拦异产线。本期取简化：options 列所有 active 作业站（带产线名标签如"WS-01 上线（装配A线）"），操作员手动选本产线下的；过站时仍兜底校验。
- `default_work_station_id` 下拉 options = 已选 allowed 集（JS 联动：allowed change → 刷新 default 下拉）。
- 提交时 service 校验 default ∈ allowed（不在则红片段）。
- `OperationCreate` schema 带 `allowed_work_station_ids: list[int]`（FastAPI Form 多值）。

**路径全景显示完整 allowed 列表**（`StationView.StationOpView` 扩展）：
- 每个 op 的 `StationOpView` 新增 `allowed_work_stations: list[str]`（作业站名列表，如 `["上线工位", "备料工位"]`）。
- 富主界面工艺路径全景的每个工序节点显示"工序名（可在：站A/站B）"，而非只显默认站名；当前工序节点同样显示其 allowed 集（含本站）。
- 由 `StationService.load` 组装时填入 `get_allowed_work_stations(op.id)` 的 name 列表。

---

## 6. 子任务切分（4 个，顺序，各自 TDD + 复审）

1. **数据模型 + 迁移**：`OperationWorkStation` 模型 + 关联表 + 唯一约束 + 数据迁移（每个现有 operation 插默认站）；`OperationCreate.allowed_work_station_ids` schema。`MasterDataService.create_routing` 校验 default ∈ allowed + 写关联表。测试：建工序带 allowed，关联表正确；default 不在 allowed 报错；现有 operation 迁移后关联表非空。
2. **查询 + 主数据维护 UI**：`get_allowed_work_stations`；工序编辑页加多选（带产线名标签）+ 默认站联动 JS；`POST /masterdata/routings/{routing_id}/operations` 扩展接收 allowed。测试：建/改工序 allowed 多选；default 不在 allowed 拒绝；多选去重。
3. **过站判定改写 + 全景显示 allowed**：`pass_operation` 防跳站第二层 `work_station_id in allowed`；`StationService.load` off-station 同步；错误消息列 allowed 站名；`StationOpView.allowed_work_stations` 填入；全景模板每个工序节点显示完整 allowed 站名列表；`OperationPassResult.next_op_can_continue_here` 计算。回归：防跳站、off-station 抛错、技能硬校验、乐观锁、完工自动解绑。
4. **连续过站 UX**：`station_pass` 成功三路分流（finished / continue-here-render-station_view / switch-station-prompt）；`station_pass_result.html` 切站分支。端到端：OP10+OP20 allowed 都含本站 → 富界面刷新到 OP20；下一站不在本站 → 切站提示。

---

## 7. 测试策略

- 真实 PostgreSQL 集成测试，TDD，逐任务复审 + 终审。
- 重点：
  - 关联表数据迁移：每个现有 operation 至少 1 条关联；default ∈ 关联集合。
  - `MasterDataService.create_routing`：allowed 多选写入；default ∉ allowed → ValueError；allowed 空列表 → ValueError；FK 完整性（不存在的 ws_id → 拒绝）。
  - `get_allowed_work_stations` 返回正确列表（含 default）。
  - `pass_operation` 防跳站第二层：ws_id 在 allowed → 通过；不在 → BusinessRuleError（消息含 allowed 站名）；第一层（跨产线）仍拦。
  - `StationService.load` off-station：ws_id 不在 allowed → BusinessRuleError。
  - `OperationPassResult.next_op_can_continue_here`：next_op allowed 含本站 → True；不含 → False；无 next_op → False。
  - 路径全景显示 allowed 列表：每个工序节点 HTML 含完整 allowed 站名（如"可在：站A、站B"）。
  - 连续过站端到端：OP10+OP20 allowed 都含本站 → 过 OP10 后富界面刷新到 OP20；OP30 allowed 不含本站 → 过 OP20 后切站提示。
  - 既有回归：单站工序（allowed 只含 default）行为不变；技能硬校验；乐观锁；完工自动解绑；载体码定位。

---

## 8. 范围边界（不做，留后期）

- 作业站分组/区域；作业站锁单一产品/工序（仍混线）。
- 跨产线工序（仍由第一层 ws.line_id != wo.line_id 拦）。
- **富界面工序级下钻可视化（双层全景：路线级 + 作业站级）**——留 P2h 单独 spec。
- **工序级跳过功能**（之前禁用占位）——留 P2h 单独 spec。
- **返工粒度复核/作业站选择 UI**（返工已是工序级 target_seq，但 UI/消息/作业站选择需复核）——留 P2h 单独 spec。
- **工序级物料校验**（每工序绑特定组件，未全绑拒绝过站）——留 P2i 单独 spec。
- 工序级 SOP/PLC（沿用 P2d 占位）。

---

## 9. 下一步

本 spec 覆盖 P2g（工序-作业站多对多 + 连续过站）。经用户复审后走 writing-plans 出实现计划 → subagent-driven-development 执行 → 终审 → 合并。
