# P2i 工序级物料校验 - 设计文档

**日期**: 2026-08-11
**状态**: Approved
**关联**: P2h（双层全景+跳站）后续；质量模块 ⑦ 闭环已建成后的下一 gap

---

## 1. 背景与动机

### 1.1 当前状态（P2i 之前）

- BOM 只挂在 **Product 级**：`boms.product_id` → `bom_items`，所有工序看到同一份 BOM
- 物料绑定累积校验**只在最终工序触发**（`operation_pass_service.py:137-168`），检查"累积已绑 + 本次扫 = BOM 总需求"
- 中间工序扫什么件、什么时候扫，系统完全不管

### 1.2 问题

操作员在第 3 工序扫了本应在第 7 工序才装的件，系统不会发现，直到最后工序累积校验通过——但此时 SN 已经按错误顺序装配，可能已经造成损坏（如先打螺丝再装屏幕，导致屏幕压坏）。

### 1.3 目标

把"何时该装哪个件"前置到具体工序，让错装在**发生时**就被拦住，而不是拖到最后工序才发现。

---

## 2. 方案选型

### 方案 A（采用）：`bom_items.consume_at_operation_seq: int | None`

每个 BOM 行声明"该件在哪个工序被装配"，NULL 表示"仅在最终工序检查"（= 现有行为，向后兼容）。

### 方案 B（否决）：`operation_bom` 关联表

灵活性高（同件多 op 消耗、不同 op 不同用量），但 95% 用例用不上，多一张表 + 多一层 CRUD UI，YAGNI。

### 方案 C（否决）：直接 FK `bom_items.operation_id`

若 routing 重构（删 op / 调序），FK 会断；用 seq 与 `SerialUnit.current_operation_seq` 既有模式一致更稳。

### 选择理由

方案 A 契合笔记本壳装配场景（每个零件在一个工序安装）、最小改动、向后兼容老数据、跟随既有 seq 模式。

---

## 3. 数据模型

### 3.1 Schema 变更

`bom_items` 新增列：

```python
consume_at_operation_seq: Mapped[int | None] = mapped_column(default=None)
```

- 含义：该 BOM 行应在哪个 routing seq 装配/扫描
- NULL = "仅最终工序检查"（兼容老数据与"整批一次扫"场景）
- 非 NULL = 该 seq 工序即时校验 + 累积校验都参与

### 3.2 Alembic 迁移

```python
def upgrade():
    op.add_column(
        "bom_items",
        sa.Column("consume_at_operation_seq", sa.Integer(), nullable=True),
    )

def downgrade():
    op.drop_column("bom_items", "consume_at_operation_seq")
```

**不加 DB 层 FK 约束**：seq 值的有效性由 app 层基于 Product → active Routing 校验（加 FK 到 `operations.seq` 会跨 routing 冲突，且 routing 重构会断链）。

### 3.3 业务约束

- 每个 Product 一个 active BOM + 一个 active Routing（既有约束）
- `consume_at_operation_seq` 必须属于该 Product active Routing 的某个 op（app 层校验）
- 不影响 BOM 与 track_mode 的现有语义

---

## 4. 校验流程

### 4.1 触发点：`operation_pass_service` 5d 改造

把"仅最终工序累积校验"升级为"**逐工序即时校验 + 最终工序兜底**"：

```
当前工序 expected.seq：

  ① 即时校验（新）：
     找出所有 consume_at_operation_seq == expected.seq 的 BOM 项
     - track_mode = "serial": 累积已绑 + 本次扫 必须覆盖该 op 的需求
     - track_mode = "batch":  累积已绑 + 本次扫 必须有至少 1 条
     - track_mode = "none":   跳过
     缺件 → BusinessRuleError("缺件，不可过站: <件名>")

  ② 扫错件拦截（新，在 genealogy_service.bind_components）：
     若扫的 component_product_id 在 BOM 里存在，
     但其 consume_at_operation_seq != 当前 op.seq
     → BusinessRuleError("此物料应在工序 X 装配，不可在此工序扫描")

  ③ 最终工序累积兜底（保留现有逻辑）：
     仍按全 BOM 校验累积 = 总需求，作为安全网
     （防止 NULL/未声明 op 的 BOM 行漏检）
```

### 4.2 错误行为矩阵

| 场景 | 行为 |
|---|---|
| 该 op 应装的 serial 件未扫 | **硬拦**，列出缺件名 |
| 该 op 应装的 batch 件未扫 | **硬拦**，列出缺件名 |
| 操作员扫了**后续工序**才装的件 | **硬拦**："此物料应在工序 X 装配" |
| 操作员扫了**前面工序**已装过的件 | 由 serial 唯一性约束兜底（serial 件已绑过会冲突） |
| BOM 行 `consume_at_operation_seq = NULL` | 当作"最终工序才检查"（兼容老数据） |
| 返工 (re-pass) 同一工序 | 已绑不重复要求；只校验"应装未装"的件 |

### 4.3 跳站 (`skip_operation`) 行为

跳站时不触发物料校验（与现有逻辑一致 —— 跳站本身已需 supervisor 授权）。但若跳过的是某件应装的工序，最终工序累积兜底仍会拦下。

### 4.4 边界情况

- **BOM 变更**：SN 已开始生产后 BOM 改了 `consume_at_operation_seq`，已绑的件仍有效（不会要求重扫）；只影响未发生的工序
- **track_mode = "none"** 的件：永不参与任何工序校验（与现有最终工序逻辑一致）
- **多 routing 共用 BOM**：seq 在不同 routing 含义不同 —— 由"每个 Product 一个 active BOM + 一个 active Routing"的业务约束保证（UI 层选 seq 时基于该 Product 的 active Routing）

---

## 5. 服务层接口

### 5.1 `MasterDataQueryService` 新增方法

```python
def get_bom_items_by_consume_op(
    self, product_id: int, op_seq: int,
) -> list[BomItem]:
    """返回 consume_at_operation_seq == op_seq 的 active BOM 行。"""
```

### 5.2 `GenealogyService.bind_components` 签名变更

```python
def bind_components(
    self, parent_su, components: list[ComponentBind],
    operator_id: int | None,
    operation_record_id: int | None = None,
    current_op_seq: int | None = None,   # 新增
) -> list[GenealogyBind]:
```

新逻辑（在现有 BOM 校验之后追加）：
- 对每个被扫的 component，查其 BOM 行的 `consume_at_operation_seq`
- 若该字段非 NULL 且 `!= current_op_seq` → `BusinessRuleError(f"此物料应在工序 {consume_at_operation_seq} 装配，不可在此工序扫描")`
- `current_op_seq = None` 时跳过此校验（向后兼容现有调用方）

### 5.3 `operation_pass_service` 改造

5d 块拆为三步：

```python
# 5d-① 即时校验：consume_at_operation_seq == expected.seq 的 BOM 行
op_bom_items = self.query.get_bom_items_by_consume_op(wo.product_id, expected.seq)
if op_bom_items:
    existing_binds = GenealogyBindRepository(self.db).list_active_by_parent(su.id)
    provided_counts: Counter[int] = Counter()
    for b in existing_binds:
        provided_counts[b.component_product_id] += 1
    for c in data.components:
        provided_counts[c.component_product_id] += 1
    missing = []
    for item in op_bom_items:
        if item.track_mode == "none":
            continue
        comp = self.query.get_product(item.component_product_id)
        comp_name = comp.name if comp else f"#{item.component_product_id}"
        provided = provided_counts.get(item.component_product_id, 0)
        required = int(item.qty) if item.track_mode == "serial" else 1
        if provided == 0:
            missing.append(f"{comp_name}（{item.track_mode}）")
        elif item.track_mode == "serial" and provided < required:
            missing.append(
                f"{comp_name}（serial，需 {required} 件，已绑 {provided} 件）")
    if missing:
        raise BusinessRuleError(
            f"物料绑定不完整，不可过站：{', '.join(missing)}")

# 5d-② 绑定（带 current_op_seq 触发扫错件拦截）
# 现有 bind_components 调用增加 current_op_seq=expected.seq

# 5d-③ 最终工序累积兜底（保留现有逻辑不变）
```

---

## 6. UI 改造

### 6.1 BOM 编辑器（`masterdata/page_router.py` + 模板）

- 表格新增列「消耗工序」：下拉选当前 Product active Routing 的 op（`seq - code/name`）
- 值可为空（显示"仅最终校验"），新建行时**默认 = 最后一个工序**（贴合现有行为）
- 提交时校验：seq 必须属于该 Product active Routing 的某个 op（否则 ValidationError）

### 6.2 过站页（`production/page_router.py` 过站 UI）

- 即时展示"本工序需装件清单"：从 `consume_at_operation_seq == 当前 op.seq` 的 BOM 项渲染
- 已绑的件打勾，未绑的红字提示
- 提交过站时若校验失败，错误信息回显在页顶（HTMX 模式，与其他卡控一致）

---

## 7. 测试策略（TDD）

| 测试 | 覆盖点 |
|---|---|
| `test_bom_item_defaults_consume_op_to_last_when_unset` | 默认值逻辑（UI 层） |
| `test_bom_item_rejects_consume_op_not_in_routing` | seq 必须属于 routing |
| `test_pass_blocks_when_required_part_for_op_not_scanned` | ① 即时校验硬拦 |
| `test_pass_ok_when_required_part_scanned_this_op` | ① 通过路径 |
| `test_bind_blocks_when_component_belongs_to_later_op` | ② 扫错件拦截 |
| `test_bind_allows_when_component_consume_op_is_null` | NULL 兼容老数据 |
| `test_final_op_cumulative_check_still_blocks_missing` | ③ 兜底未受影响 |
| `test_skip_operation_does_not_trigger_material_check` | 跳站不触发 |
| `test_repass_same_op_does_not_require_rescan` | 返工不重扫 |
| 现有 `test_pass_*_bom_*` 回归 | 老行为（NULL BOM 行）仍通过 |

---

## 8. 任务拆分（预估 6 task）

1. **Migration + Model 字段** — Alembic + `bom_items.consume_at_operation_seq`
2. **`MasterDataQueryService.get_bom_items_by_consume_op`** + 测试
3. **`GenealogyService.bind_components` 加 `current_op_seq` 参数** + 扫错件拦截 + 测试
4. **`operation_pass_service` 5d 改造**（① 即时校验） + 测试
5. **BOM 编辑器 UI**（下拉列）+ 后端校验 + **过站页"本工序需装件"展示**
6. **回归 + memory 更新**

---

## 9. 非目标 (Out of Scope)

- **多 routing 共用 BOM 的复杂场景**：本期假定每个 Product 一个 active Routing
- **同件跨多 op 消耗**（如 10 颗螺丝分 4 工序）：本期不支持，未来用 `operation_bom` 关联表升级
- **物料有效期/批次追溯深度校验**：不在本期范围
- **物料消耗与库存联动**（WMS 仓储）：不在 LightMES 范围
- **替代料（substitution）**：未来需求

---

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 老数据（NULL seq）行为变化 | NULL 明确等于"仅最终校验"= 老行为，零影响 |
| BOM 编辑器误选错 op | UI 下拉只显示当前 Product active Routing 的 op，提交前后端双重校验 |
| `bind_components` 签名变化破坏既有调用方 | `current_op_seq` 默认 None，None 时跳过新校验 |
| 测试 DB seed 与新字段冲突 | 新字段 nullable，老 seed 无需改动 |
| 返工时已绑件被重复要求 | 即时校验只校验"应装未装"，已绑件自然通过 |
