# Equipment Runtime Module — UI Supplement (Tasks 9-10)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** Close the three UI scope gaps found in final review: live monitor board (3s polling + manual transition + connection banner), OEE board with real data, and reachable downtime-reason correction.

**Architecture:** Builds on the completed backend (Tasks 1-8). Adds a `MonitorService.monitor_board()` read model, a partial-HTML polling endpoint, a manual-transition POST route, an OEE rows assembler, and rewires the downtime page to list `ProductionDowntime` rows with a reason-assignment form. Uses htmx `hx-trigger="load, every 3s"` for polling (no new JS dependency).

## Global Constraints

- HTML routes: `html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")` returning `(user, auth_response)`; return `auth_response` when not None.
- Manual transition is a supervisor/admin action (spec: "班长手动 transition"); use `require_role("admin", "supervisor")`.
- Templates extend `base.html`; Jinja2Templates dir is `src/lightmes/templates/`.
- Errors raise `lightmes.shared.errors` subclasses.
- htmx is already loaded in `base.html` (`/static/vendor/htmx.min.js`); CSRF token is auto-attached via the global `htmx:configRequest` listener in `base.html`.

---

## Task 9: Live monitor board (polling + manual transition + connection banner)

**Files:**
- Modify: `src/lightmes/modules/equipment/monitor_service.py` (add `monitor_board()`)
- Modify: `src/lightmes/modules/equipment/router.py` (add partial + transition routes)
- Create: `src/lightmes/templates/equipment/partials/monitor_board.html`
- Modify: `src/lightmes/templates/equipment/monitor.html`
- Test: `tests/modules/equipment/test_monitor_board.py`

**Interfaces:**
- Consumes: `WorkStation` (masterdata), `MachineConnection` (connectivity), `WorkstationState` (equipment), `WorkstationStateMachine.transition`.
- Produces: `MonitorService.monitor_board() -> list[dict]`; GET `/equipment/monitor/partial`; POST `/equipment/monitor/{work_station_id}/transition`.

- [ ] **Step 1: Write failing test**

`tests/modules/equipment/test_monitor_board.py`:

```python
from lightmes.modules.equipment.monitor_service import MonitorService
from lightmes.modules.masterdata.models import Line, WorkStation
from lightmes.modules.connectivity.models import MachineConnection


def test_monitor_board_returns_station_with_state_and_conn(db_session):
    line = Line(code="L_MB", name="L_MB")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS_MB", name="WS_MB", line_id=line.id, seq=1)
    db_session.add(ws); db_session.flush()
    conn = MachineConnection(name="C_MB", protocol="mqtt",
                             work_station_id=ws.id, status="connected")
    db_session.add(conn); db_session.flush()

    board = MonitorService(db_session).monitor_board()
    assert len(board) == 1
    row = board[0]
    assert row["work_station_id"] == ws.id
    assert row["code"] == "WS_MB"
    assert row["state"] is None  # no state recorded yet
    assert row["conn_status"] == "connected"


def test_monitor_board_includes_state(db_session):
    from lightmes.modules.equipment import ensure_system_downtime_reasons
    from lightmes.modules.equipment.state_machine import WorkstationStateMachine
    from datetime import datetime, timezone

    ensure_system_downtime_reasons(db_session)
    line = Line(code="L_MB2", name="L_MB2")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS_MB2", name="WS_MB2", line_id=line.id, seq=1)
    db_session.add(ws); db_session.flush()
    sm = WorkstationStateMachine(db_session)
    sm.transition(ws.id, "RUNNING", at=datetime(2026, 8, 14, tzinfo=timezone.utc))

    board = MonitorService(db_session).monitor_board()
    assert board[0]["state"] == "RUNNING"
```

- [ ] **Step 2: Run to confirm fail**

`uv run pytest tests/modules/equipment/test_monitor_board.py -v` → FAIL (no `monitor_board`).

- [ ] **Step 3: Implement `monitor_board()`**

In `monitor_service.py`, add imports and method:

```python
from lightmes.modules.connectivity.models import MachineConnection
from lightmes.modules.masterdata.models import WorkStation


class MonitorService:
    # ... existing current_states() ...

    def monitor_board(self) -> list[dict]:
        stations = list(self.db.execute(
            select(WorkStation)
            .where(WorkStation.is_active.is_(True))
            .order_by(WorkStation.line_id, WorkStation.seq)
        ).scalars().all())

        open_states = {
            s.work_station_id: s
            for s in self.db.execute(
                select(WorkstationState).where(WorkstationState.ended_at.is_(None))
            ).scalars().all()
        }

        conns = self.db.execute(
            select(MachineConnection).where(MachineConnection.work_station_id.isnot(None))
        ).scalars().all()
        conn_by_ws: dict[int, list] = {}
        for c in conns:
            conn_by_ws.setdefault(c.work_station_id, []).append(c)

        rows = []
        for ws in stations:
            st = open_states.get(ws.id)
            ws_conns = conn_by_ws.get(ws.id, [])
            conn = ws_conns[0] if ws_conns else None
            rows.append({
                "work_station_id": ws.id,
                "code": ws.code,
                "name": ws.name,
                "state": st.state if st else None,
                "state_started_at": st.started_at if st else None,
                "conn_status": conn.status if conn else None,
                "conn_name": conn.name if conn else None,
            })
        return rows
```

- [ ] **Step 4: Add routes to `router.py`**

Add imports and routes (after `monitor_page`):

```python
from lightmes.modules.equipment.models import ALL_STATES, DowntimeReason, MachineTag
from lightmes.modules.equipment.state_machine import WorkstationStateMachine
```

```python
@router.get("/equipment/monitor/partial", response_class=HTMLResponse)
def monitor_partial(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    board = MonitorService(db).monitor_board()
    return templates.TemplateResponse(
        request, "equipment/partials/monitor_board.html",
        {"board": board, "all_states": ALL_STATES})


@router.post("/equipment/monitor/{work_station_id}/transition")
def monitor_transition(work_station_id: int, state: str = Form(...),
                       db: Session = Depends(get_db),
                       _=Depends(require_role("admin", "supervisor"))):
    WorkstationStateMachine(db).transition(work_station_id, state, source="manual")
    db.commit()
    return RedirectResponse("/equipment/monitor", status_code=303)
```

Also update `monitor_page` to pass `board` and `all_states`:

```python
@router.get("/equipment/monitor", response_class=HTMLResponse)
def monitor_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    board = MonitorService(db).monitor_board()
    return templates.TemplateResponse(
        request, "equipment/monitor.html",
        {"board": board, "all_states": ALL_STATES})
```

- [ ] **Step 5: Create templates**

`src/lightmes/templates/equipment/partials/monitor_board.html`:

```html
{% for row in board %}
<div class="card">
  <div class="card__title">{{ row.code }} {{ row.name }}</div>
  <div class="nav-card__desc">
    状态：<strong>{{ row.state or "未采集" }}</strong>
    {% if row.conn_status %}
      <span class="badge {% if row.conn_status == 'connected' %}badge--ok{% elif row.conn_status in ('connecting','error') %}badge--danger{% endif %}">{{ row.conn_status }}</span>
    {% else %}
      <span class="badge">未配置连接</span>
    {% endif %}
  </div>
  <form hx-post="/equipment/monitor/{{ row.work_station_id }}/transition" hx-swap="none">
    <select name="state">
      {% for s in all_states %}
      <option value="{{ s }}" {% if row.state == s %}selected{% endif %}>{{ s }}</option>
      {% endfor %}
    </select>
    <button type="submit">切换</button>
  </form>
</div>
{% else %}
<div class="card"><div class="nav-card__desc">暂无工位</div></div>
{% endfor %}
```

Rewrite `src/lightmes/templates/equipment/monitor.html`:

```html
{% extends "base.html" %}
{% block title %}设备监控 - LightMES{% endblock %}
{% block content %}
<div class="page-head"><h1>设备监控</h1></div>
<div class="grid" hx-get="/equipment/monitor/partial" hx-trigger="load, every 3s" hx-swap="innerHTML">
  {% include "equipment/partials/monitor_board.html" %}
</div>
{% endblock %}
```

- [ ] **Step 6: Run tests**

`uv run pytest tests/modules/equipment/test_monitor_board.py tests/modules/equipment/test_pages.py -v` → pass.

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/modules/equipment/monitor_service.py src/lightmes/modules/equipment/router.py src/lightmes/templates/equipment/monitor.html src/lightmes/templates/equipment/partials/monitor_board.html tests/modules/equipment/test_monitor_board.py
git commit -m "feat(equipment): live monitor board with polling and manual transition"
```

---

## Task 10: OEE board data + reachable downtime correction

**Files:**
- Modify: `src/lightmes/modules/equipment/router.py` (rewrite `oee_page` + `downtimes_page`, add `_oee_rows` helper)
- Modify: `src/lightmes/templates/equipment/oee.html`, `src/lightmes/templates/equipment/downtimes.html`
- Test: `tests/modules/equipment/test_oee_board.py`

**Interfaces:**
- Consumes: `OeeService`, `WorkstationStateMachine`, `ShiftService`, `WorkStation`, `WorkOrder`, `ProductionDowntime`, `DowntimeReason`.
- Produces: `_oee_rows(db) -> list[dict]`; reworked GET `/equipment/oee` and `/equipment/downtimes`.

- [ ] **Step 1: Write failing test**

`tests/modules/equipment/test_oee_board.py`:

```python
from lightmes.modules.equipment.router import _oee_rows


def test_oee_rows_returns_station_rows(db_session):
    from lightmes.modules.masterdata.models import Line, WorkStation

    line = Line(code="L_OB", name="L_OB")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS_OB", name="WS_OB", line_id=line.id, seq=1)
    db_session.add(ws); db_session.flush()

    rows = _oee_rows(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row["code"] == "WS_OB"
    assert row["state"] == "未采集"
    # quality is None (no work order) → displayed as "N/A"
    assert row["quality"] is None
```

- [ ] **Step 2: Run to confirm fail**

`uv run pytest tests/modules/equipment/test_oee_board.py -v` → FAIL (no `_oee_rows`).

- [ ] **Step 3: Add `_oee_rows` helper + rewrite routes**

Add to `router.py` (imports at top, plus helper):

```python
from datetime import datetime, timedelta, timezone

from lightmes.modules.equipment.oee_service import (
    OeeService, _shift_duration_seconds, compute_availability, compute_oee,
)
from lightmes.modules.equipment.models import ProductionDowntime
from lightmes.modules.masterdata.models import WorkStation
from lightmes.modules.production.models import WorkOrder
from lightmes.modules.production.shift_service import ShiftService


def _oee_rows(db: Session) -> list[dict]:
    now = datetime.now(timezone.utc)
    oee_svc = OeeService(db)
    sm = WorkstationStateMachine(db)
    shift_svc = ShiftService(db)

    stations = list(db.execute(
        select(WorkStation).where(WorkStation.is_active.is_(True))
        .order_by(WorkStation.line_id, WorkStation.seq)
    ).scalars().all())

    rows = []
    for ws in stations:
        shift = shift_svc.current_at(ws.line_id, now)
        duration = _shift_duration_seconds(shift) if shift is not None else 8 * 3600
        since = now - timedelta(seconds=duration)
        unplanned = oee_svc.unplanned_downtime_seconds(ws.id, since, now)
        availability = compute_availability(duration, unplanned)

        wo = db.execute(
            select(WorkOrder).where(WorkOrder.line_id == ws.line_id)
            .order_by(WorkOrder.id.desc()).limit(1)
        ).scalars().first()
        quality = oee_svc.quality_for_work_order(wo.id) if wo is not None else None

        cur = sm.current(ws.id)
        oee = compute_oee(availability, quality if quality is not None else 0.0)
        rows.append({
            "code": ws.code,
            "name": ws.name,
            "state": cur.state if cur is not None else "未采集",
            "availability": availability,
            "quality": quality,
            "oee": oee,
        })
    return rows
```

Rewrite `oee_page`:

```python
@router.get("/equipment/oee", response_class=HTMLResponse)
def oee_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    rows = _oee_rows(db)
    return templates.TemplateResponse(request, "equipment/oee.html", {"rows": rows})
```

Rewrite `downtimes_page` (list `ProductionDowntime` + reasons for the form):

```python
@router.get("/equipment/downtimes", response_class=HTMLResponse)
def downtimes_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    rows = db.execute(
        select(ProductionDowntime, DowntimeReason.code, DowntimeReason.name, WorkStation.code)
        .outerjoin(DowntimeReason, ProductionDowntime.downtime_reason_id == DowntimeReason.id)
        .join(WorkStation, ProductionDowntime.work_station_id == WorkStation.id)
        .order_by(ProductionDowntime.started_at.desc())
    ).all()
    downtimes = [
        {"dt": dt, "reason_code": r_code, "reason_name": r_name, "station_code": ws_code}
        for dt, r_code, r_name, ws_code in rows
    ]
    reasons = list(db.execute(
        select(DowntimeReason).where(DowntimeReason.is_active.is_(True)).order_by(DowntimeReason.id)
    ).scalars().all())
    return templates.TemplateResponse(
        request, "equipment/downtimes.html",
        {"downtimes": downtimes, "reasons": reasons})
```

- [ ] **Step 4: Rewrite templates**

`src/lightmes/templates/equipment/oee.html`:

```html
{% extends "base.html" %}
{% block title %}OEE 看板 - LightMES{% endblock %}
{% block content %}
<div class="page-head"><h1>OEE 看板</h1></div>
<table>
  <thead><tr><th>工位</th><th>当前状态</th><th>可用率</th><th>质量率</th><th>OEE</th></tr></thead>
  <tbody>
  {% for r in rows %}
  <tr>
    <td>{{ r.code }} {{ r.name }}</td>
    <td>{{ r.state }}</td>
    <td>{{ "%.1f%%"|format(r.availability * 100) }}</td>
    <td>{% if r.quality is not none %}{{ "%.1f%%"|format(r.quality * 100) }}{% else %}N/A{% endif %}</td>
    <td>{{ "%.1f%%"|format(r.oee * 100) }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

`src/lightmes/templates/equipment/downtimes.html`:

```html
{% extends "base.html" %}
{% block title %}停机记录 - LightMES{% endblock %}
{% block content %}
<div class="page-head"><h1>停机记录</h1></div>
<table>
  <thead><tr><th>ID</th><th>工位</th><th>开始</th><th>结束</th><th>当前原因</th><th>修正原因</th></tr></thead>
  <tbody>
  {% for d in downtimes %}
  <tr>
    <td>{{ d.dt.id }}</td>
    <td>{{ d.station_code }}</td>
    <td>{{ d.dt.started_at }}</td>
    <td>{{ d.dt.ended_at or "进行中" }}</td>
    <td>{{ d.reason_name or "未分配" }}</td>
    <td>
      <form hx-post="/equipment/downtimes/{{ d.dt.id }}/reason" hx-swap="none">
        <select name="reason_id">
          {% for r in reasons %}
          <option value="{{ r.id }}" {% if d.reason_code == r.code %}selected{% endif %}>{{ r.name }}</option>
          {% endfor %}
        </select>
        <button type="submit">分配</button>
      </form>
    </td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 5: Run tests**

`uv run pytest tests/modules/equipment/test_oee_board.py tests/modules/equipment/test_pages.py -v` → pass. Also run the full equipment suite to confirm no regression:
`uv run pytest tests/modules/equipment/ -v`.

- [ ] **Step 6: Commit**

```bash
git add src/lightmes/modules/equipment/router.py src/lightmes/templates/equipment/oee.html src/lightmes/templates/equipment/downtimes.html tests/modules/equipment/test_oee_board.py
git commit -m "feat(equipment): OEE board data and reachable downtime correction"
```

---

## Self-review

- Both tasks build only on the completed backend; no new models/migrations.
- `monitor_board()` returns a superset of `current_states()` (keeps the latter for compatibility).
- Manual transition uses `source="manual"` (already supported by `transition`).
- OEE quality uses the line's most recent work order (Phase-1 granularity simplification; documented).
- `oee_page` uses `8*3600` as the fallback shift duration when no active shift is configured.
