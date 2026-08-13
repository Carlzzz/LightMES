from lightmes.modules.issue.models import Issue, IssueAction, IssueType
from lightmes.modules.issue.repository import (
    IssueRepository, IssueActionRepository, IssueTypeRepository,
)
from lightmes.modules.production.models import SerialUnit


def _make_serial_unit(db_session, sn: str = "ISSSN1") -> SerialUnit:
    """构造一个真实 SerialUnit（满足 issues.serial_unit_id FK）。
    走 MasterDataService + ProductionService 创建 product/line/ws/routing/wo。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import WorkOrderCreate
    from lightmes.modules.production.repository import SerialUnitRepository
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code=f"P{sn}", name="壳", type="finished"))
    line = md.create_line(LineCreate(code=f"L{sn}", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code=f"W{sn}", name="作业站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code=f"R{sn}", name="路线", product_id=p.id,
        operations=[OperationCreate(
            seq=1, code=f"OP{sn}", name="装配",
            default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    wo = ProductionService(db_session).create_work_order(
        WorkOrderCreate(code=f"WO{sn}", product_id=p.id, routing_id=r.id,
                        line_id=line.id, qty=5))
    return SerialUnitRepository(db_session).add(
        SerialUnit(sn=sn, work_order_id=wo.id, product_id=p.id))


def test_get_blocking_for_sn_returns_none_when_no_issue(db_session, sample_user):
    repo = IssueRepository(db_session)
    assert repo.get_blocking_for_sn(serial_unit_id=999) is None


def test_get_blocking_for_sn_returns_open_blocking(db_session, sample_user):
    """is_blocking=true + status=open 命中；is_blocking=false 不命中。"""
    su = _make_serial_unit(db_session, "ISSB1")
    it_block = IssueType(code="B", name="B", severity="critical", is_blocking=True)
    it_non = IssueType(code="N", name="N", severity="minor", is_blocking=False)
    db_session.add_all([it_block, it_non]); db_session.flush()

    repo = IssueRepository(db_session)
    blocking = Issue(
        issue_type_id=it_block.id, title="b", severity="critical",
        source="manual", serial_unit_id=su.id,
        reported_by_id=sample_user.id)
    non_block = Issue(
        issue_type_id=it_non.id, title="n", severity="minor",
        source="manual", serial_unit_id=su.id,
        reported_by_id=sample_user.id)
    db_session.add_all([blocking, non_block]); db_session.flush()

    found = repo.get_blocking_for_sn(su.id)
    assert found is not None
    assert found.id == blocking.id


def test_count_unverified(db_session, sample_user):
    """验证 count_unverified 正确计数未 verify 的 action。"""
    it = IssueType(code="X", name="X", severity="minor")
    db_session.add(it); db_session.flush()
    issue = Issue(issue_type_id=it.id, title="t", severity="minor",
                  reported_by_id=sample_user.id)
    db_session.add(issue); db_session.flush()
    a1 = IssueAction(issue_id=issue.id, type="corrective", title="a1", status="verified")
    a2 = IssueAction(issue_id=issue.id, type="corrective", title="a2", status="open")
    a3 = IssueAction(issue_id=issue.id, type="corrective", title="a3", status="in_progress")
    db_session.add_all([a1, a2, a3]); db_session.flush()

    repo = IssueActionRepository(db_session)
    assert repo.count_unverified(issue.id) == 2


# --- 补充覆盖 count_open / count_blocking / list 过滤 / IssueType 仓库 ---

def test_count_open_and_count_blocking_only_counts_this_test_s_issues(
    db_session, sample_user,
):
    """count_open / count_blocking 是全局计数；为避免 seed 数据污染，断言增量。
    断言：新增 2 阻断 open + 1 非阻断 acknowledged + 1 resolved 后，
    open 增量 = 3、blocking 增量 = 2。"""
    it_block = IssueType(code="BK_CNT", name="BK", severity="critical", is_blocking=True)
    it_minor = IssueType(code="MI_CNT", name="MI", severity="minor", is_blocking=False)
    db_session.add_all([it_block, it_minor]); db_session.flush()

    repo = IssueRepository(db_session)
    before_open = repo.count_open()
    before_blocking = repo.count_blocking()

    db_session.add_all([
        Issue(issue_type_id=it_block.id, title="cnt-b1", severity="critical",
              source="manual", status="open", reported_by_id=sample_user.id),
        Issue(issue_type_id=it_block.id, title="cnt-b2", severity="critical",
              source="manual", status="acknowledged", reported_by_id=sample_user.id),
        Issue(issue_type_id=it_minor.id, title="cnt-m1", severity="minor",
              source="manual", status="acknowledged", reported_by_id=sample_user.id),
        Issue(issue_type_id=it_minor.id, title="cnt-m2", severity="minor",
              source="manual", status="resolved", reported_by_id=sample_user.id),
    ]); db_session.flush()

    assert repo.count_open() - before_open == 3       # b1, b2, m1
    assert repo.count_blocking() - before_blocking == 2  # b1, b2


def test_list_filters_by_severity_and_status(db_session, sample_user):
    """list() 按 severity / status 过滤；用本测试内独有的 title 集合断言，
    避免与 seed 中可能存在的 issue 冲突。"""
    it = IssueType(code="LF_T", name="LF", severity="minor")
    db_session.add(it); db_session.flush()

    repo = IssueRepository(db_session)
    db_session.add_all([
        Issue(issue_type_id=it.id, title="lft-o-min", severity="minor",
              source="manual", status="open", reported_by_id=sample_user.id),
        Issue(issue_type_id=it.id, title="lft-o-crit", severity="critical",
              source="manual", status="open", reported_by_id=sample_user.id),
        Issue(issue_type_id=it.id, title="lft-r-min", severity="minor",
              source="manual", status="resolved", reported_by_id=sample_user.id),
    ]); db_session.flush()

    open_only = [i for i in repo.list(statuses=["open"]) if i.title.startswith("lft-")]
    assert {i.title for i in open_only} == {"lft-o-min", "lft-o-crit"}

    crit_only = [i for i in repo.list(severities=["critical"]) if i.title.startswith("lft-")]
    assert {i.title for i in crit_only} == {"lft-o-crit"}


def test_issue_type_repository_lookup_by_created_ids(db_session, sample_user):
    """覆盖 get / get_by_code（断言只取本测试新建的行，避免与 seed 数据冲突）。
    list_active / list_all 不断言全集，只断言我们新建的 type 能被找到。"""
    it_a = IssueType(code="TWRK_A", name="A", severity="minor", is_active=True)
    it_b = IssueType(code="TWRK_B", name="B", severity="critical", is_active=False)
    db_session.add_all([it_a, it_b]); db_session.flush()

    repo = IssueTypeRepository(db_session)

    # get / get_by_code 直接命中
    assert repo.get(it_a.id).code == "TWRK_A"
    assert repo.get_by_code("TWRK_B").name == "B"

    # list_active 包含 it_a、不包含 it_b（断言本测试创建集合的子集关系）
    active_codes = {t.code for t in repo.list_active()}
    assert "TWRK_A" in active_codes
    assert "TWRK_B" not in active_codes

    # list_all 包含 it_a 和 it_b
    all_codes = {t.code for t in repo.list_all()}
    assert {"TWRK_A", "TWRK_B"}.issubset(all_codes)
