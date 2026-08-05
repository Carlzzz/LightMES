# P2b 设计文档：主数据管理 UI + ERP 同步抽象层

- 日期：2026-08-05
- 状态：设计已获用户口头确认，待书面复核
- 上游：`2026-08-07-p2a-hierarchy-traceability-design.md`（P2a 三层模型，已合并）
- 定位：车间执行能力跃升的第二块。补齐主数据的可编辑界面，并为 ERP 主数据下行同步建好抽象层（金蝶版本未定，隔离在 adapter 后）。

---

## 1. 背景与目标

P2a 建好了三层模型（line/work_station/routing/operation）+ 追溯体系，但主数据除 product 外都只有 API、没有管理界面；且尚无 ERP 集成的落点。P2b：
1. **主数据管理 UI**：补齐 line/work_station/routing/operation/bom/sn_rule 的可编辑界面（HTMX + 薄荷绿卡片，沿用现有风格），让现场能全程点界面配置一条产线。
2. **ERP 同步抽象层**：给受 ERP 管辖的主数据加来源标记，建 `ErpSyncService` 抽象 + 文件导入实现（模拟金蝶下发），把真接金蝶隔离在一个将来填的 adapter 后。

**顺序**：先数据模型层（ERP 字段 + 同步逻辑 + 抽象层），再 UI（UI 一次带上来源徽标 + 导入入口，不返工）。

---

## 2. 关键决策（已确认）

| # | 主题 | 决策 |
|---|---|---|
| 1 | ERP 同步方向 | **仅下行**（ERP→MES）。ERP(金蝶) 是物料/BOM/工艺路线的权威源，MES 只读同步；MES 特有字段本地维护；**不回写 ERP**。 |
| 2 | 受 ERP 管辖实体 | `product`、`bom`(+bom_item)、`routing` 头层加 source/erp_ref/synced_at。`line`/`work_station`/`operation` MES 字段/`sn_rule` 纯本地。"路线从 ERP 来，工序细节 MES 补"。 |
| 3 | 抽象层 | `ErpSyncService` 抽象基类 + 一个 `FileErpSyncService`（文件导入）。同步业务逻辑（upsert/打标/幂等/不覆盖 manual）现在完整实现+测试；接金蝶=换一个读 API 的 adapter。 |
| 4 | 冲突/幂等 | 按 `erp_ref` upsert：存在→更新 ERP 管字段+synced_at、不动 MES 本地字段；不存在→新建 source=erp。source=manual（erp_ref 空）永不被覆盖。重复导入结果一致。 |
| 5 | 校验失败 | **部分成功 + 错误报告**（不整批回滚）。坏行记入 errors 跳过，好行照常导入。 |
| 6 | 导入文件格式 | product→CSV（扁平）、bom→JSON（嵌套）。各用单一最佳格式（系统整体支持两种）；不做每实体双解析器（该层注定被金蝶 API 替换，核心价值在同步逻辑）。 |
| 7 | 同步逻辑归属 | 放 masterdata（加 `upsert_product`/`upsert_bom`）；integration 只做"解析文件→调 masterdata upsert"。 |
| 8 | UI | 每实体一管理页（列表+新增+编辑），HTMX+薄荷绿卡片。source=erp 记录：ERP 管字段编辑时禁用+来源徽标。routing 编辑页=选产品+加有序工序行。写操作 require_login。 |

---

## 3. 数据模型改动

给以下实体各加三字段（加字段，不改现有逻辑）：
- **`product`**、**`bom`**、**`routing`**：
  - `source: str = "manual"`（manual/erp）
  - `erp_ref: str | None`（外部单据号，索引）
  - `synced_at: datetime | None`（tz-aware）
- **erp_ref 部分唯一索引**（每实体）：`Index("uq_<t>_erp_ref", "erp_ref", unique=True, postgresql_where=text("erp_ref IS NOT NULL"))`——同一外部单据号不重复导入成两条。沿用 P1c/P2a 的 partial unique index 模式。
- `line`/`work_station`/`operation`/`sn_rule`/`bom_item` **不加** source（纯本地或从属结构）。

迁移：三张表各 add 三列 + 一个部分唯一索引。因 source 有 Python 默认 "manual"，现有行需 server_default="manual"（迁移里带上，避免 NOT NULL 冲突）。

---

## 4. ERP 同步抽象层（integration 模块）

新增 `src/lightmes/modules/integration/`（`__init__.py` register、`service.py`、`schemas.py`、`router.py`），沿用模块化单体约定。

### 4.1 抽象与结果
```python
# schemas
class SyncResult(BaseModel):
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = []

# service
class ErpSyncService(ABC):
    @abstractmethod
    def sync_products(self, raw: bytes) -> SyncResult: ...
    @abstractmethod
    def sync_boms(self, raw: bytes) -> SyncResult: ...
```
（**本期 ERP 导入只实现 product + bom 两个**。`ErpSyncService` 抽象只声明 `sync_products`/`sync_boms`；routing 的 ERP 同步留到后续（routing 的本地编辑页本期做，但不从 ERP 导入）。见 §7。）

### 4.2 FileErpSyncService（本期唯一实现）
- `sync_products(raw)`：解析 **CSV**（列：erp_ref,code,name,type,spec,unit,track_mode），逐行调 `MasterDataService.upsert_product`。
- `sync_boms(raw)`：解析 **JSON**（`[{erp_ref, product_code, items:[{component_code, qty}]}]`），逐条调 `MasterDataService.upsert_bom`。
- 解析/校验失败的行/条：记入 `SyncResult.errors`，跳过，不回滚其余。
- 将来 `KingdeeErpSyncService(ErpSyncService)` 读金蝶 API 产出同样的 raw→调同一 upsert，本期不实现（只留类的位置说明）。

### 4.3 同步业务逻辑（masterdata，核心价值）
- `MasterDataService.upsert_product(data)`：按 `erp_ref` 查 product；存在→更新 ERP 管字段（code/name/type/spec/unit/track_mode）+`synced_at=now`；不存在→新建 `source="erp"`。返回 (obj, "created"|"updated")。
- `MasterDataService.upsert_bom(data)`：按 `erp_ref` 查 bom；组件 code 逐个解析为 product（找不到→抛可捕获错误记入 errors）；存在→替换 bom_item 行 + synced_at；不存在→新建 source="erp" bom + items。返回 (obj, action)。
- source=manual 记录 erp_ref 空，不匹配任何导入，永不被覆盖。幂等：重复导入同 erp_ref 走 updated。

### 4.4 跨模块
integration 只调 masterdata 的 service（upsert_*）；不碰 masterdata repository。沿用 facade/service 边界约定。

---

## 5. UI（HTMX + 薄荷绿卡片）

每实体一管理页（列表+新增+编辑），复用现有最简 CRUD 模式与 `.card`/`.data-table`/`.badge` 样式：
- **产线** `/masterdata/lines`：列表+新增（code/name）
- **作业站** `/masterdata/work-stations`：列表+新增（code/name/选产线下拉/seq）
- **产品** `/masterdata/products`（已有，增强）：加来源徽标（ERP/本地）+ synced_at；source=erp 记录编辑时 ERP 字段 disabled
- **SN 规则** `/masterdata/sn-rules`：列表+新增（code/name/pattern/seq_reset/绑产品）
- **BOM** `/masterdata/boms`：列表+查看（成品→组件行，来源徽标）
- **工艺路径** `/masterdata/routings`：路线编辑页——选产品→加工序行（seq/code/name/选作业站下拉）→保存（复用 P2a 的 create_routing）
- **ERP 导入** `/integration/import`：上传文件（product CSV / bom JSON）→显示 SyncResult（created/updated/skipped + errors 列表）
- **首页导航**：主数据卡片区列全部管理页 + ERP 导入入口

来源徽标：`.badge` 样式，source=erp 绿色"ERP"+synced_at，manual"本地"。写操作 `require_login`；HTMX 片段 `{{ }}` 自动转义。

---

## 6. 子任务切分（约 8 个，顺序，各自 TDD + 复审）
1. 数据模型：product/bom/routing 加 source/erp_ref/synced_at + erp_ref 部分唯一索引 + 迁移（server_default="manual"）
2. masterdata：`upsert_product`（打标/幂等/不覆盖 manual）+ 测试
3. masterdata：`upsert_bom`（JSON 结构，组件 code 解析，item 替换）+ 测试
4. integration 模块骨架 + `ErpSyncService` 抽象 + `SyncResult` + `FileErpSyncService`（CSV product / JSON bom 解析，部分成功+errors）+ 测试
5. integration 导入 API + 导入页面（上传→结果）+ 测试
6. masterdata：补齐 line/work_station/sn_rule 的 list service+API（create 部分 P2a 已有）+ 三个管理页
7. masterdata：routing 编辑页（选产品+加工序行）+ product 页来源徽标增强 + bom 列表/查看页
8. 首页导航扩展 + 全量回归（+ seed 按需更新）

---

## 7. 范围边界

**不做（留后期）：**
- 真接金蝶 API（版本未定；只留 KingdeeErpSyncService 的位置说明，不实现）
- ERP 回写（仅下行，既定）
- 工单报工回传 ERP（生产数据，非主数据）
- routing 的 ERP 同步：MVP 聚焦 product+bom 导入；routing 从 ERP 同步留接口/后续（routing 本地编辑页本期做）
- operation 的 SOP/技能/面板编辑（P2c/P2d 预留字段，本期路线编辑页只做 seq/code/name/作业站）
- 主数据导出、审批流、版本对比

---

## 8. 错误处理与并发
- 导入：部分成功+错误报告；坏行/条记入 SyncResult.errors 跳过，好的照常。
- upsert 按 erp_ref + 部分唯一索引兜底，防重复导入产生两条。
- source=manual 永不被 ERP 导入覆盖。
- 写接口 require_login；领域异常沿用全局 handler；HTMX 页面吞异常前 rollback（沿用约定）。

---

## 9. 测试策略
- 真实 PostgreSQL 集成测试，TDD，逐任务复审 + 终审。
- 重点：upsert 幂等（重复导入同 erp_ref 不重复、走 updated）、source=erp 不覆盖 source=manual、部分成功+errors（一行坏不影响其余）、erp_ref 部分唯一索引、CSV 解析（product）、JSON 解析（bom + 组件 code 解析失败记 errors）、来源徽标渲染、路线编辑保存工序、各管理页 require_login。

---

## 10. 下一步
本 spec 覆盖 P2b（主数据 UI + ERP 同步抽象层）。经复核后走 writing-plans 出实现计划。P2b 完成后推进 P2c（技能资格）、P2d（工位作业主界面）。
