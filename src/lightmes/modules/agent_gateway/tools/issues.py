"""Issue MCP tools (4: list/get/create/update_status).

让 AI Agent 也能查询与管理 Issue：与 issue HTML UI 共用 IssueService 状态机，
保持业务规则一致（CAPA 验证闸 / 状态转换 / source 枚举）。Agent 通过 type_code
而非 type_id 创建，避免 type id 泄漏到 agent 接口。

授权对齐：与 issue HTML router (`issue/router.py`) 保持一致 —— operator 仅能
查看/操作自己上报的 Issue；supervisor+ 视为特权用户可见全部。角色判定走
`role_obj.name` 优先、legacy `role` 字段兜底（与 `auth/dependencies.py:require_role`
及 `agent_gateway/auth.py:_has_write_role` 同款 pattern）。
"""
from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.schemas import (
    CreateIssueResult, IssueActionReadV1, IssueReadV1, UpdateIssueStatusResult,
)
from lightmes.modules.agent_gateway.server import mcp
from lightmes.modules.auth.role_utils import is_privileged as _is_privileged
from lightmes.modules.auth.role_utils import user_role_name as _user_role_name


@mcp.tool()
@require_scope("read")
def list_issues(
    statuses: list[str] | None = None,
    severities: list[str] | None = None,
    sources: list[str] | None = None,
    serial_unit_id: int | None = None,
    work_order_id: int | None = None,
    page: int = 1,
    size: int = 20,
) -> list[IssueReadV1]:
    """列出 Issue，可按状态/严重度/来源/SN/WO 过滤。

    Args:
        statuses: 可选，如 ["open", "acknowledged"]。
        severities: 可选，如 ["critical", "major"]。
        sources: 可选，如 ["station_andon", "defect_linked", "manual"]。
        serial_unit_id: 可选，按 SN 过滤。
        work_order_id: 可选。
        page: 从 1 开始。
        size: 每页数量。

    Returns:
        Issue 列表（含 issue_type_code + is_blocking 派生字段），按 status asc / id desc 排序。

    授权：operator 仅看到自己上报的 Issue；supervisor+ 全部。与 HTML /issues 一致。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.issue.repository import IssueRepository

    request = get_http_request()
    db = request.state.db_session
    user = request.state.user

    kwargs: dict = {
        "statuses": statuses, "severities": severities, "sources": sources,
        "serial_unit_id": serial_unit_id, "work_order_id": work_order_id,
        "page": page, "size": size,
    }
    # operator 仅看自己上报的；特权用户看全部（与 HTML router 一致）
    if _user_role_name(user) == "operator":
        kwargs["reported_by_id"] = user.id
    rows = IssueRepository(db).list(**kwargs)
    return [_to_read(db, r) for r in rows]


@mcp.tool()
@require_scope("read")
def get_issue(issue_id: int) -> dict:
    """按 id 查 issue，含 actions（CAPA 列表）。

    Args:
        issue_id: Issue id。

    Returns:
        {"issue": IssueReadV1 (dict), "actions": [IssueActionReadV1 (dict), ...]}

    Raises:
        NotFoundError: 不存在。
        PermissionError: operator 试图查看他人上报的 Issue（与 HTML /issues/{id} 对齐）。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.issue.repository import (
        IssueActionRepository, IssueRepository,
    )
    from lightmes.shared.errors import NotFoundError

    request = get_http_request()
    db = request.state.db_session
    user = request.state.user

    issue = IssueRepository(db).get(issue_id)
    if issue is None:
        raise NotFoundError(f"Issue 不存在: {issue_id}")
    # operator 只能看自己上报的（与 HTML router 一致）；特权用户不受限
    if not _is_privileged(user) and issue.reported_by_id != user.id:
        raise PermissionError("无权查看该 Issue")
    actions = IssueActionRepository(db).list_for_issue(issue_id)
    return {
        "issue": _to_read(db, issue).model_dump(),
        "actions": [
            IssueActionReadV1(
                id=a.id, type=a.type, title=a.title, status=a.status,
                assigned_to_id=a.assigned_to_id,
                due_date=a.due_date.isoformat() if a.due_date else None,
            ).model_dump()
            for a in actions
        ],
    }


@mcp.tool()
@require_scope("write")
def create_issue(
    type_code: str,
    title: str,
    description: str | None = None,
    source: str = "manual",
    serial_unit_id: int | None = None,
    work_order_id: int | None = None,
    work_station_id: int | None = None,
) -> CreateIssueResult:
    """创建 Issue（默认 source=manual；如要标 station 上报请传 source=station_andon）。

    Args:
        type_code: IssueType code，如 'quality' / 'material_shortage'。
        title: 简短标题。
        description: 可选详细描述。
        source: station_andon | defect_linked | manual。
        serial_unit_id: 可选。
        work_order_id: 可选。
        work_station_id: 可选。

    Returns:
        CreateIssueResult：{id, status}（初始 status 固定 "open"）。

    Raises:
        NotFoundError: IssueType code 不存在。
        ValidationError: title 为空。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.issue.repository import IssueTypeRepository
    from lightmes.modules.issue.service import IssueService
    from lightmes.shared.errors import NotFoundError

    db = get_http_request().state.db_session
    user_id = get_http_request().state.user.id

    it = IssueTypeRepository(db).get_by_code(type_code)
    if it is None:
        raise NotFoundError(f"IssueType code 不存在: {type_code}")

    issue = IssueService(db).create_issue(
        issue_type_id=it.id,
        title=title,
        description=description,
        source=source,
        serial_unit_id=serial_unit_id,
        work_order_id=work_order_id,
        work_station_id=work_station_id,
        reported_by_id=user_id,
    )
    db.commit()
    db.refresh(issue)
    return CreateIssueResult(id=issue.id, status=issue.status)


@mcp.tool()
@require_scope("write")
def update_issue_status(
    issue_id: int,
    action: str,
    root_cause: str | None = None,
    containment_action: str | None = None,
    disposition: str | None = None,
    reopen_reason: str | None = None,
) -> UpdateIssueStatusResult:
    """触发 Issue 状态转换。

    Args:
        issue_id: Issue id。
        action: acknowledge | resolve | close | reopen。
        root_cause / containment_action / disposition: action=resolve 时必填
            （disposition 取值: use_as_is | rework | scrap | hold）。
        reopen_reason: action=reopen 时必填。

    Returns:
        UpdateIssueStatusResult：{id, status}（转换后的新状态）。

    Raises:
        BusinessRuleError: 当前状态不允许该转换 / close 时还有 CAPA 未 verified。
        ValidationError: action 非法 / 缺必填字段。
        NotFoundError: issue 不存在。
        PermissionError: operator 试图操作他人 Issue（defense-in-depth：scope 写
            权限已限制 admin/supervisor，但显式校验防止 scope 配置漂移）。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.issue.repository import IssueRepository
    from lightmes.modules.issue.service import IssueService
    from lightmes.shared.errors import NotFoundError, ValidationError

    request = get_http_request()
    db = request.state.db_session
    user = request.state.user
    user_id = user.id

    # Defense-in-depth：即便 scope=write 已要求 admin/supervisor，仍显式校验归属，
    # 防止未来 scope 配置变更（例如给 operator 写权限）后非授权访问。
    existing = IssueRepository(db).get(issue_id)
    if existing is None:
        raise NotFoundError(f"Issue 不存在: {issue_id}")
    if not _is_privileged(user) and existing.reported_by_id != user_id:
        raise PermissionError("无权操作该 Issue")

    svc = IssueService(db)
    if action == "acknowledge":
        issue = svc.acknowledge(issue_id, user_id)
    elif action == "resolve":
        if not root_cause or not containment_action or not disposition:
            raise ValidationError(
                "resolve 需要 root_cause / containment_action / disposition"
            )
        issue = svc.resolve(
            issue_id, user_id,
            root_cause=root_cause,
            containment_action=containment_action,
            disposition=disposition,
        )
    elif action == "close":
        issue = svc.close(issue_id, user_id)
    elif action == "reopen":
        if not reopen_reason:
            raise ValidationError("reopen 需要 reopen_reason")
        issue = svc.reopen(issue_id, user_id, reason=reopen_reason)
    else:
        raise ValidationError(f"非法 action: {action}")

    db.commit()
    db.refresh(issue)
    return UpdateIssueStatusResult(id=issue.id, status=issue.status)


def _to_read(db, issue) -> IssueReadV1:
    """Issue ORM → IssueReadV1（带 is_blocking 派生 + 关联 type code）。"""
    from lightmes.modules.issue.models import IssueType
    from lightmes.modules.issue.service import IssueService

    it = db.get(IssueType, issue.issue_type_id)
    return IssueReadV1(
        id=issue.id,
        issue_type_code=it.code if it else "",
        title=issue.title,
        description=issue.description,
        status=issue.status,
        severity=issue.severity,
        source=issue.source,
        serial_unit_id=issue.serial_unit_id,
        work_order_id=issue.work_order_id,
        work_station_id=issue.work_station_id,
        defect_id=issue.defect_id,
        reported_at=issue.reported_at.isoformat() if issue.reported_at else "",
        acknowledged_at=(
            issue.acknowledged_at.isoformat() if issue.acknowledged_at else None
        ),
        resolved_at=issue.resolved_at.isoformat() if issue.resolved_at else None,
        closed_at=issue.closed_at.isoformat() if issue.closed_at else None,
        is_blocking=IssueService.is_blocking(issue),
    )
