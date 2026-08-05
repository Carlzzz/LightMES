# P2c 设计文档：技能资格体系

- 日期：2026-08-05
- 状态：设计已获用户口头确认，待书面复核
- 上游：`2026-08-07-p2a-hierarchy-traceability-design.md`（operation 预留 required_skill_id/required_level）、`2026-08-05-p2b-masterdata-ui-erp-sync-design.md`（主数据 UI 模式）
- 定位：车间执行能力跃升的第三块。启用 operation 的技能资格要求，过站时校验操作员技能，防止无资格人员作业关键工序。

---

## 1. 背景与目标

P2a 在 operation 上预留了 `required_skill_id`/`required_level` 字段，OperationPassService 有一个默认放行的技能校验钩子（`# 5b. 技能校验钩子（P2c 填）`）。P2c 把它落地：
1. **技能主数据**：技能定义 + 人员技能等级档案（纯 MES 本地维护）。
2. **operation 资格要求编辑**：路线编辑器里给工序设置"需要某技能达到某等级"。
3. **过站技能校验**：过站时硬校验操作员技能，不足则拒绝过站。

范围小于 P2b，单 spec。

---

## 2. 关键决策（已确认）

| # | 主题 | 决策 |
|---|---|---|
| 1 | 技能等级模型 | **单一数值等级（分级制，A）**：每技能有等级 1..N；一人对一技能持一个等级；operation 要求一个技能 ≥ 一个等级。校验 = `operator_level >= required_level`。完美贴合现有 required_skill_id/required_level 预留字段，不改 operation 结构、不加关联表。 |
| 2 | 校验强度 | **硬拦截**：技能不足 → 抛领域异常 `SkillError`，拒绝过站、不写工序记录、不推进。与三层防跳站/乐观锁同级的硬校验。非软提醒。 |
| 3 | 档案维护 | **纯本地手工维护**：skill/operator_skill 是 MES 本地主数据，无 source/erp_ref（不像 P2b product/bom）。管理页手工录入。 |
| 4 | 向后兼容 | operation.required_skill_id 为空 → 跳过校验直接过（现有无技能要求的工序不受影响）。 |
| 5 | operator_id 缺失 | 工序有技能要求但 operator_id 为空 → 视为不足，拦截（防匿名绕过）。 |

---

## 3. 数据模型

两张新表（MES 本地，无 source/erp_ref）：

**`skill`（技能定义）**
- `id: int` PK
- `code: str` unique + index
- `name: str`
- `max_level: int`（该技能最高等级，如 3）
- `description: str | None`
- + TimestampMixin

**`operator_skill`（人员技能档案）**
- `id: int` PK
- `user_id: int` FK → users.id
- `skill_id: int` FK → skill.id
- `level: int`
- UniqueConstraint `(user_id, skill_id)` 名 `uq_operator_skill_user_skill`（一人一技能一条）
- + TimestampMixin

**operation**（不改结构，补外键）
- `required_skill_id`：当前为裸 `int | None`，本期补 FK → skill.id（更规范）。
- `required_level`：不变（`int | None`）。

迁移：建 skill + operator_skill 两表（含唯一约束），给 operation.required_skill_id 加 FK 约束指向 skill.id。确认不误删既有索引（uq_active_*/uq_operation_*/uq_*_erp_ref 等）。

---

## 4. 服务与校验逻辑

**`SkillService`（masterdata 模块）**
- `create_skill(SkillCreate) -> Skill`（dup code → ValueError）
- `list_skills() -> list[Skill]`
- `set_operator_skill(user_id, skill_id, level) -> OperatorSkill`（**upsert**：同 (user_id, skill_id) 存在则更新 level，否则新建；校验 user/skill 存在、level 合法 1..max_level，否则 ValueError）
- `list_operator_skills() -> list[OperatorSkill]`
- `get_operator_level(user_id, skill_id) -> int | None`（查人员某技能等级，无记录返回 None）

**校验（OperationPassService 第 90 行钩子实现）**
位置：三层防跳站之后、写工序记录之前。
```python
if expected.required_skill_id is not None:
    level = SkillService(self.db).get_operator_level(
        data.operator_id, expected.required_skill_id) if data.operator_id else None
    if level is None or level < (expected.required_level or 0):
        raise SkillError(
            f"操作员技能不足：工序 {expected.seq} {expected.name} "
            f"需要技能等级 L{expected.required_level}+，当前 {level if level is not None else '无'}")
```
- **硬拦截**：`SkillError`（新领域异常，继承现有 DomainError 体系，全局 handler 映射为 4xx/HTMX 红片段）。
- **向后兼容**：required_skill_id 为空 → 整段跳过，直接过。
- **operator_id 空 + 有技能要求** → level 取 None → 拦截。
- 校验在乐观锁/写记录之前，异常直接抛出（HTMX 页面处理器已有 rollback 约定）。

**SkillError**：加入 `lightmes/shared/errors.py`，继承 `DomainError`，`status_code = 422`（与 BusinessRuleError 同 HTTP 语义——不可执行的业务规则）。全局异常 handler 已按 DomainError 基类统一处理，新异常继承即被捕获，无需改 handler。

---

## 5. UI（HTMX + 薄荷绿卡片）

复用 P2b 的最简 CRUD 模式（`.card`/`.data-table`/`.form-row`，require_login 写守卫，current_user_or_none→401+HX-Redirect，error_row/结果片段）：

**技能定义页 `/masterdata/skills`**
- 列表：code/name/max_level/描述
- 新增：code/name/max_level(number)/描述

**人员技能档案页 `/masterdata/operator-skills`**
- 列表：人员(display_name)/技能(name)/等级
- 新增：选人下拉(users) + 选技能下拉(skills) + 等级(number) → `set_operator_skill` upsert（重复=更新等级，不报错）

**路线编辑器增强（P2b `/masterdata/routings`）**
- 每工序行加两个可选字段：**技能下拉**（选项含"无"=不要求）+ **要求等级**(number)
- 保存：选了技能 → 写 operation.required_skill_id + required_level；选"无" → 两者留空
- 工艺路径/工序展示带技能要求（如"装配 L3+"），无要求则不显示

**首页导航**：主数据卡片区加"技能管理"(/masterdata/skills)、"人员技能"(/masterdata/operator-skills) 两入口。

**安全**：写操作 require_login；HTMX 片段 `{{ }}` 自动转义。

---

## 6. 子任务切分（约 5 个，顺序，各自 TDD + 复审）
1. 数据模型：skill + operator_skill 两表 + operation.required_skill_id 补 FK + 迁移
2. SkillService：create_skill/list_skills、set_operator_skill(upsert)/list_operator_skills、get_operator_level + 测试
3. SkillError + OperationPassService 钩子实现（硬拦截/向后兼容/operator_id 空拦截）+ 校验四态测试
4. UI：技能定义页 + 人员技能档案页(upsert) + 首页导航 + 测试
5. 路线编辑器加技能要求字段（技能下拉+等级）+ 保存写 operation + 工序展示 + 测试

---

## 7. 范围边界

**不做（留后期）：**
- 多技能矩阵（C 模型，一工序多技能要求）——单技能门槛够用，真需要再扩（需 operation↔技能要求关联表）
- 技能有效期 / 认证到期 / 复审流程
- 技能等级审批流、变更历史
- 从 ERP/人事系统同步技能档案（纯本地维护，既定）
- 软提醒模式（既定硬拦截）
- 工位作业主界面的技能展示（P2d；本期只做过站校验 + 主数据）

---

## 8. 错误处理与边界
- 技能不足 → SkillError 硬拦截，不写记录、不推进；HTMX 扫码页红片段（复用 scan_result 错误渲染 + 处理器 rollback 约定）。
- required_skill_id 空 → 放行（向后兼容）。
- operator_id 空 + 有要求 → 拦截。
- set_operator_skill：level 越界（<1 或 >max_level）或 user/skill 不存在 → ValueError → 页面 error_row。
- (user_id, skill_id) 唯一约束兜底防重复档案；set_operator_skill 应用层 upsert 优先命中。
- 写接口 require_login；领域异常全局 handler。

---

## 9. 测试策略
- 真实 PostgreSQL 集成测试，TDD，逐任务复审 + 终审。
- 重点：
  - operator_skill 唯一约束（同人同技能第二条 → IntegrityError / upsert 命中更新）
  - set_operator_skill upsert（重复录入=更新 level，不新增）、level 越界 → ValueError
  - get_operator_level（有记录返回 level / 无记录返回 None）
  - **校验四态**：技能足够→过站成功；技能不足→SkillError；工序无要求→过站成功；operator_id 空+有要求→SkillError
  - 技能页 / 人员技能页 CRUD + require_login
  - 路线编辑器写 required_skill_id/required_level（选技能 / 选"无"两路径）
  - operation.required_skill_id FK 约束

---

## 10. 下一步
本 spec 覆盖 P2c（技能资格体系）。经复核后走 writing-plans 出实现计划。P2c 完成后推进 P2d（工位作业主界面，集大成，采纳 docs/design-refs/p2d-operator-station-reference.html 布局）。
