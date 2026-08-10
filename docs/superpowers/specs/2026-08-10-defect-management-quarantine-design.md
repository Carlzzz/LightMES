# 设计文档：缺陷管理 + 不良品隔离

- 日期：2026-08-10
- 状态：设计已获用户确认
- 上游：`2026-08-10-first-inspection-wired-into-pass-design.md`（首检接进过站已完成）、P2h 返工站位选择模式（复用）
- 定位：建立缺陷记录闭环——发现缺陷 → SN 自动隔离 → 处理决策（返工/报废/让步）→ 解除隔离。新 2 张表 + SN 新状态 + 复用既有 rework/scrap。

---

## 1. 背景与目标

**现状**：
- SN 状态机：`pending / in_process / reworking / finished / scrapped`。无"隔离"中间态。
- 返工/报废流程既有（`ReworkService.rework / scrap`）。
- 首检不合格已能拒绝过站（`pass_operation` 5c），但**失败的首检记录回滚无审计**，且**操作员/QC 无法结构化记录任意时刻发现的缺陷**。
- 12 模块标准 ⑦ 质量管理缺：缺陷管理（独立 defect 表）、不良品隔离流程、让步接收。

**本 spec 目标**：
1. 缺陷类型主数据 + 缺陷记录（实例）。
2. SN 新增 `quarantined` 状态——发现缺陷即隔离，阻断过站。
3. 缺陷登记独立页面（QC/操作员输入 SN + 类型 + 位置 → 创建记录 + 隔离）。
4. 缺陷处理三路决策：返工（复用 ReworkService）、报废（复用 ReworkService.scrap）、让步（supervisor 授权回 in_process）。
5. SN 状态机适配：pass/skip 拒绝 quarantined；scrap 允许 quarantined + finished。

**不做**（下一个 spec）：首检 failed 自动建缺陷记录 + 自动隔离；缺陷照片上传；CAPA/8D；巡检 IPQC/OQC；缺陷分析报表。

---

## 2. 关键决策（已确认）

| # | 主题 | 决策 |
|---|------|------|
| 1 | 缺陷类型层级 | 扁平 + 可选 category（外观/尺寸/功能/其他）。不做多级树（YAGNI）。 |
| 2 | 严重度 | 3 级 critical/major/minor。登记时**快照**到 defect_record（主数据后续改不影响历史）。 |
| 3 | 缺陷位置 | 自由文本（"左上角"）。不做结构化 position 模型（YAGNI）。 |
| 4 | 发现即隔离 | 所有 defect 默认 `SN.status → quarantined`。不做"信息性缺陷不隔离"开关（YAGNI）。 |
| 5 | 让步授权 | `concession` 需 supervisor/admin 角色（复用既有 require_role）。 |
| 6 | 让步回原状态 | **一律回 in_process**（不记 pre_quarantine_status）。简化数据模型；finished 件让步后回 in_process 走完剩余工序（或操作员手动再完工）。|
| 7 | 返工/报废复用 | 决策=rework → 调既有 `ReworkService.rework(target_seq, expected_repass_station_id)`；决策=scrap → 调既有 `ReworkService.scrap()`。 |
| 8 | 返工 target_seq 选择 | 缺陷详情页内嵌返工表单：操作员**下拉选 target_seq**（routing 工序列表）+ HTMX 联动站位下拉（复用 P2h `/trace/rework/allowed-stations` 模式）。 |
| 9 | 既有状态机适配 | `pass_operation` / `skip_operation` 步骤 1 状态拒绝集合加 `quarantined`；`ReworkService.scrap` 允许集合扩为 `("in_process", "reworking", "quarantined", "finished")`；`ReworkService.rework` 仍仅拒 scrapped（quarantined 天然通过）。 |
| 10 | 范围边界 | 不做：首检 failed 自动建缺陷、缺陷照片、CAPA/8D、巡检 IPQC/OQC、缺陷分析报表。 |

---

## 3. 数据模型

### 3.1 新表 `DefectType`（`production/models.py`）

```python
class DefectType(Base, TimestampMixin):
    """缺陷类型主数据"""
    __tablename__ = "defect_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    category: Mapped[str | None] = mapped_column(default=None)  # 外观/尺寸/功能/其他
    severity: Mapped[str] = mapped_column(default="major")  # critical/major/minor
    description: Mapped[str | None] = mapped_column(default=None)
    is_active: Mapped[bool] = mapped_column(default=True)
```

### 3.2 新表 `DefectRecord`（`production/models.py`）

```python
class DefectRecord(Base, TimestampMixin):
    """缺陷记录（实例）"""
    __tablename__ = "defect_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    defect_type_id: Mapped[int] = mapped_column(ForeignKey("defect_types.id"), index=True)
    defect_type_code: Mapped[str] = mapped_column()  # 快照
    defect_type_name: Mapped[str] = mapped_column()  # 快照
    severity: Mapped[str] = mapped_column()  # 快照（登记时刻）
    serial_unit_id: Mapped[int] = mapped_column(ForeignKey("serial_units.id"), index=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    operation_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id"), default=None)
    work_station_id: Mapped[int | None] = mapped_column(ForeignKey("work_stations.id"), default=None)
    position: Mapped[str | None] = mapped_column(default=None)
    discovered_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    handling_status: Mapped[str] = mapped_column(default="pending")  # pending/rework/scrap/concession
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    handling_remark: Mapped[str | None] = mapped_column(default=None)
    remark: Mapped[str | None] = mapped_column(default=None)
```

**handling_status 流转**：`pending`（登记后）→ `rework`/`scrap`/`concession`（处理决策后，终态）。

### 3.3 SerialUnit.status 扩展

新增值 `"quarantined"`（无 schema 改动，string 字段）。无新字段、无迁移。

### 3.4 迁移

一条 Alembic：`create_table defect_types` + `create_table defect_records`（含 FK + 索引）。不删/改既有表。

---

## 4. 服务层

### 4.1 新 `DefectService`（`production/defect_service.py`）

```python
class DefectService:
    def __init__(self, db: Session):
        self.db = db
        self.query = MasterDataQueryService(db)
        self.rework = ReworkService(db)
        self.serial_units = SerialUnitRepository(db)

    def log_defect(self, defect_type_id: int, sn: str, discovered_by: int,
                   operation_id: int | None = None, work_station_id: int | None = None,
                   position: str | None = None, remark: str | None = None,
                   ) -> DefectRecord:
        """创建缺陷记录 + 隔离 SN。"""
        # 1. 查 defect_type（必须 is_active=True）
        # 2. 查 SN（必须存在，status != scrapped）
        # 3. su.status = "quarantined"
        # 4. 创建 defect_record（快照 type code/name/severity；work_order_id 从 su 反查）
        # 5. flush + publish DefectLogged 事件

    def handle_rework(self, record_id: int, handled_by: int,
                      target_seq: int, expected_repass_station_id: int,
                      remark: str | None = None) -> DefectRecord:
        """返工处理：调 ReworkService.rework + 更新 record。"""
        # 1. 查 record（handling_status 必须 pending）
        # 2. self.rework.rework(sn, target_seq, expected_repass_station_id, operator_id=handled_by)
        # 3. record.handling_status = "rework"; handled_by/at; remark
        # 4. flush + publish DefectHandled

    def handle_scrap(self, record_id: int, handled_by: int,
                     remark: str | None = None) -> DefectRecord:
        """报废处理：调 ReworkService.scrap + 更新 record。"""
        # 1. 查 record（handling_status 必须 pending）
        # 2. self.rework.scrap(sn, reason=remark)
        # 3. record.handling_status = "scrap"; handled_by/at; remark

    def handle_concession(self, record_id: int, handled_by: int,
                          remark: str | None = None) -> DefectRecord:
        """让步处理：supervisor 授权，SN 回 in_process。"""
        # 1. 查 record（handling_status 必须 pending）
        # 2. su.status = "in_process"  # 一律回 in_process（不记原状态）
        # 3. record.handling_status = "concession"; handled_by/at; remark
```

### 4.2 既有服务适配

**`OperationPassService.pass_operation`** 步骤 1（定位 SN 后）：
```python
# 原：if su.status in ("finished", "scrapped"):
#     raise BusinessRuleError(f"SN 已{su.status}，不可过站: {su.sn}")
# 改：
if su.status in ("finished", "scrapped", "quarantined"):
    raise BusinessRuleError(f"SN 已{su.status}，不可过站: {su.sn}")
```

**`OperationPassService.skip_operation`** 步骤 1：同上扩展。

**`ReworkService.scrap`**：
```python
# 原：if su.status not in ("in_process", "reworking"):
# 改：
if su.status not in ("in_process", "reworking", "quarantined", "finished"):
    raise BusinessRuleError(f"仅在制/返工/隔离/完工件可判废，当前: {su.status}")
```

**`ReworkService.rework`** 不改（仅拒 scrapped，quarantined 天然通过）。

### 4.3 新事件（`production/events.py`）

```python
@dataclass
class DefectLogged(Event):
    defect_record_id: int
    serial_unit_id: int
    sn: str
    defect_type_code: str
    severity: str

@dataclass
class DefectHandled(Event):
    defect_record_id: int
    serial_unit_id: int
    sn: str
    decision: str  # rework/scrap/concession
```

---

## 5. 路由

| 路由 | 用途 | 守卫 |
|------|------|------|
| `GET /quality/defect-types` | 缺陷类型管理页（list + add） | login |
| `POST /quality/defect-types` | 创建缺陷类型 | login |
| `POST /quality/defect-types/{id}/delete` | 软删（is_active=False） | login |
| `GET /quality/defects/log` | 缺陷登记页 | login |
| `POST /quality/defects/log` | 提交登记 → 创建 record + 隔离 SN | login |
| `GET /quality/defects` | 缺陷记录列表（过滤 handling_status / SN） | login |
| `GET /quality/defects/{id}` | 缺陷详情 + 处理按钮 | login |
| `POST /quality/defects/{id}/handle-rework` | 返工处理（target_seq + station_id） | login |
| `POST /quality/defects/{id}/handle-scrap` | 报废处理 | login |
| `POST /quality/defects/{id}/handle-concession` | 让步处理 | **require_role supervisor/admin** |
| `GET /quality/defects/{id}/rework-stations` | HTMX：target_seq 选定后联动站位下拉（复用 P2h `_resolve_rework_stations` 逻辑） | login |

返工 target_seq 下拉从 routing 工序列表填充；站位下拉 HTMX 联动（同 P2h rework 页模式）。

---

## 6. UI/模板

### 6.1 缺陷类型管理（`quality/defect_types.html`）

复用 P2 既有 mint-green 卡片 CRUD 风格（参考 `skills.html`）。表格列：code/name/category/severity/is_active + 删除按钮。新增表单：code + name + category 下拉 + severity 下拉 + 描述。

### 6.2 缺陷登记（`quality/defect_log.html`）

表单：
- SN 输入（必填）
- 缺陷类型下拉（仅 active）
- 位置文本（可选）
- 工序下拉（可选，从 SN 的 routing 填充——可用 HTMX 联动）
- 作业站下拉（可选）
- 备注

提交后显示成功片段：「缺陷 [code] [name] 已登记，SN [X] 已隔离。前往 [缺陷详情] 处理。」

### 6.3 缺陷列表（`quality/defect_list.html`）

表格列：SN / 缺陷类型 / 严重度 / 发现时间 / 处理状态（带颜色 badge：pending=红/rework=蓝/scrap=灰/concession=绿）。点行进详情。过滤：handling_status 下拉 + SN 搜索。

### 6.4 缺陷详情（`quality/defect_detail.html`）

卡片显示完整记录信息。底部 3 个处理按钮（仅 handling_status=pending 时显示）：
- **返工**：点击展开内嵌表单——target_seq 下拉（routing 工序）+ 站位下拉（HTMX 联动 `/quality/defects/{id}/rework-stations?target_seq=X`）+ 备注 + 提交
- **报废**：confirm 对话框 + 备注 + 提交
- **让步**：confirm + 备注 + 提交（supervisor 角色才显示按钮）

---

## 7. 边界处理

| 场景 | 行为 |
|------|------|
| 登记缺陷时 SN 状态=scrapped | 拒绝："SN 已判废，不可登记缺陷" |
| 登记缺陷时 SN 状态=quarantined | 拒绝："SN 已隔离，请先处理既有缺陷" |
| 登记缺陷时 SN 状态=finished | 允许（成品也可发现缺陷）→ su.status="quarantined" |
| 登记缺陷时 SN 状态=pending | 允许（来料就发现缺陷）→ su.status="quarantined" |
| 处理时 handling_status != pending | 拒绝："该缺陷已处理" |
| 返工处理：target_seq 之后的工序不存在 | ReworkService 已有校验（拒绝） |
| 让步处理：非 supervisor | 403 |
| 让步后 SN 状态 | 一律 in_process（无论隔离前是 in_process 还是 finished） |
| 已隔离 SN 扫码过站 | pass/skip 步骤 1 拒绝"SN 已 quarantined" |
| 已隔离 SN 报废 | scrap 允许（扩展后） |
| 已隔离 SN 返工 | rework 允许（天然通过，仅拒 scrapped） |

---

## 8. 测试策略

### 8.1 单元（`tests/modules/production/test_defect_service.py`）

- `log_defect` 创建记录 + 隔离 SN + 快照正确
- `log_defect` SN=scrapped 拒绝
- `log_defect` SN=quarantined 拒绝
- `handle_rework` 调 ReworkService + 更新 record + SN=reworking
- `handle_scrap` 调 ReworkService.scrap + SN=scrapped
- `handle_concession` SN 回 in_process（从 in_process 隔离的）
- `handle_concession` SN 回 in_process（从 finished 隔离的——一律 in_process）
- `handle_*` handling_status != pending 拒绝

### 8.2 状态机适配（既有测试 + 新断言）

- `pass_operation` quarantined SN 拒绝（新）
- `skip_operation` quarantined SN 拒绝（新）
- `ReworkService.scrap` quarantined SN 允许（新）
- `ReworkService.scrap` finished SN 允许（新）
- 既有 pass/skip/rework 测试全绿（回归）

### 8.3 路由 + UI

- 登记页提交 → 创建 record + 隔离
- 详情页返工表单提交 → record.handling_status=rework
- 详情页报废按钮 → record.handling_status=scrap
- 详情页让步按钮（非 supervisor）→ 403

### 8.4 E2E

- 登记缺陷 → SN 隔离 → 扫码过站被拒 → 返工处理 → SN reworking → re-pass → in_process
- 登记缺陷 → 让步处理 → SN 回 in_process → 扫码过站通过
- 登记缺陷 → 报废处理 → SN scrapped → 扫码过站终态拒绝

---

## 9. 文件改动清单

| 文件 | 改动 |
|------|------|
| `src/lightmes/modules/production/models.py` | 新增 DefectType + DefectRecord |
| `src/lightmes/migrations/versions/xxx_create_defect_tables.py` | 新迁移 |
| `src/lightmes/modules/production/defect_service.py` | 新：DefectService |
| `src/lightmes/modules/production/events.py` | 新事件 DefectLogged + DefectHandled |
| `src/lightmes/modules/production/operation_pass_service.py` | 改：pass/skip 拒绝 quarantined |
| `src/lightmes/modules/trace/rework_service.py` | 改：scrap 允许 quarantined + finished |
| `src/lightmes/modules/quality/router.py` | 改：新增 11 个路由 |
| `src/lightmes/templates/quality/defect_types.html` | 新 |
| `src/lightmes/templates/quality/defect_log.html` | 新 |
| `src/lightmes/templates/quality/defect_list.html` | 新 |
| `src/lightmes/templates/quality/defect_detail.html` | 新 |
| `src/lightmes/templates/quality/partials/defect_*_row.html` | 新（CRUD 行片段） |
| `src/lightmes/templates/home.html` | 改：质量管理卡片加 4 个入口 |
| `tests/modules/production/test_defect_service.py` | 新 |
| `tests/modules/production/test_defect_state_machine.py` | 新（pass/skip/rework quarantined 适配） |
| `tests/modules/quality/test_defect_routes.py` | 新 |
| `tests/modules/production/test_defect_e2e.py` | 新 |

---

## 10. 后续工作（不在本 spec）

1. **首检 failed 自动建缺陷 + 自动隔离**：`pass_operation` 5c 首检失败时，不再仅 raise，而是调 `DefectService.log_defect`（type="首检不合格"），SN 自动隔离。需新缺陷类型 + 改 5c 失败路径。
2. **P2i 工序级物料校验**：operation_bom 表 + per-op 物料校验。
3. **缺陷分析报表**：按类型/工序/时间统计；管理驾驶舱。
4. **CAPA/8D**：纠正预防措施 + 根本原因分析。
