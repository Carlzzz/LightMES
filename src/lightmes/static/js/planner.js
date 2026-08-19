(function () {
  // ===== Weekly view drag-drop =====
  document.querySelectorAll('.planner-backlog__item').forEach(function (item) {
    item.addEventListener('dragstart', function (e) {
      e.dataTransfer.setData('text/plain', JSON.stringify({
        wo_id: item.dataset.woId,
        source: 'backlog'
      }));
      e.dataTransfer.effectAllowed = 'move';
    });
  });

  document.querySelectorAll('.planner-card').forEach(function (card) {
    card.addEventListener('dragstart', function (e) {
      e.dataTransfer.setData('text/plain', JSON.stringify({
        wo_id: card.dataset.woId,
        source: 'grid'
      }));
      e.dataTransfer.effectAllowed = 'move';
      e.stopPropagation();
    });
  });

  document.querySelectorAll('.planner-cell').forEach(function (cell) {
    cell.addEventListener('dragover', function (e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      cell.classList.add('planner-cell--drag-over');
    });
    cell.addEventListener('dragleave', function () {
      cell.classList.remove('planner-cell--drag-over');
    });
    cell.addEventListener('drop', function (e) {
      e.preventDefault();
      cell.classList.remove('planner-cell--drag-over');
      var payload;
      try { payload = JSON.parse(e.dataTransfer.getData('text/plain')); }
      catch (_) { return; }
      if (!payload || !payload.wo_id) return;
      var lineId = cell.dataset.lineId;
      var date = cell.dataset.date;
      // 简化：默认 8 小时；从 backlog 拖来弹确认，从 grid 拖来直接移
      var start = date + 'T08:00:00';
      var end = date + 'T16:00:00';
      fetch('/production/planner/work-orders/' + payload.wo_id + '/schedule', {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: 'line_id=' + encodeURIComponent(lineId)
              + '&planned_start=' + encodeURIComponent(start)
              + '&planned_end=' + encodeURIComponent(end)
      }).then(function (r) {
        if (r.ok) {
          window.location.reload();
        } else {
          return r.text().then(function (t) {
            if (window.showErrorModal) window.showErrorModal(t || '排程失败');
            else alert(t || '排程失败');
          });
        }
      }).catch(function (e) {
        if (window.showErrorModal) window.showErrorModal('网络错误: ' + e);
        else alert('网络错误: ' + e);
      });
    });
  });
})();

// ===== Daily Gantt drag + resize + snap =====
(function () {
  var SNAP_MIN = 15;
  var tracks = document.querySelectorAll('.planner-gantt__track');
  if (!tracks.length) return;

  function snap(minutes) { return Math.round(minutes / SNAP_MIN) * SNAP_MIN; }

  function updateWoSchedule(woId, lineId, date, startMin, endMin) {
    var hhmm = function (m) {
      var h = Math.floor(m / 60), mm = m % 60;
      return (h < 10 ? '0' : '') + h + ':' + (mm < 10 ? '0' : '') + mm + ':00';
    };
    fetch('/production/planner/work-orders/' + woId + '/schedule', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'line_id=' + encodeURIComponent(lineId)
            + '&planned_start=' + encodeURIComponent(date + 'T' + hhmm(startMin))
            + '&planned_end=' + encodeURIComponent(date + 'T' + hhmm(endMin))
    }).then(function (r) {
      if (r.ok) window.location.reload();
      else return r.text().then(function (t) {
        if (window.showErrorModal) window.showErrorModal(t || '调度失败');
        else alert(t || '调度失败');
      });
    }).catch(function (e) {
      if (window.showErrorModal) window.showErrorModal('网络错误: ' + e);
      else alert('网络错误: ' + e);
    });
  }

  tracks.forEach(function (track) {
    var lineId = track.dataset.lineId;
    var date = track.dataset.date;
    var blocks = track.querySelectorAll('.planner-gantt__block');
    blocks.forEach(function (block) {
      var woId = block.dataset.woId;

      // 整块拖动（改 start，保持 duration）；block._moved 供 click 区分拖动/点击
      var dragStart = null;
      block._moved = false;
      block.addEventListener('mousedown', function (e) {
        if (e.target.classList.contains('planner-gantt__resize-handle')) return;  // resize 接管
        dragStart = {
          x: e.clientX,
          origLeft: parseInt(block.style.left, 10) || 0,
          origWidth: parseInt(block.style.width, 10) || 60
        };
        block._moved = false;
        e.preventDefault();
      });
      document.addEventListener('mousemove', function (e) {
        if (!dragStart) return;
        var dx = e.clientX - dragStart.x;
        if (Math.abs(dx) > 3) block._moved = true;
        var newLeft = Math.max(0, Math.min(24 * 60 - dragStart.origWidth, dragStart.origLeft + dx));
        block.style.left = newLeft + 'px';
      });
      document.addEventListener('mouseup', function () {
        if (!dragStart) return;
        var leftMin = snap(parseInt(block.style.left, 10) || 0);
        var widthMin = snap(dragStart.origWidth);
        block.style.left = leftMin + 'px';
        block.style.width = widthMin + 'px';
        dragStart = null;
        updateWoSchedule(woId, lineId, date, leftMin, leftMin + widthMin);
      });

      // resize handle（改 duration，保持 start）
      var handle = block.querySelector('.planner-gantt__resize-handle');
      if (handle) {
        var resizeStart = null;
        handle.addEventListener('mousedown', function (e) {
          resizeStart = {
            x: e.clientX,
            origWidth: parseInt(block.style.width, 10) || 60,
            origLeft: parseInt(block.style.left, 10) || 0
          };
          e.preventDefault();
          e.stopPropagation();
        });
        document.addEventListener('mousemove', function (e) {
          if (!resizeStart) return;
          var dx = e.clientX - resizeStart.x;
          if (Math.abs(dx) > 3) block._moved = true;
          var newWidth = Math.max(30, resizeStart.origWidth + dx);
          block.style.width = newWidth + 'px';
        });
        document.addEventListener('mouseup', function () {
          if (!resizeStart) return;
          var leftMin = snap(resizeStart.origLeft);
          var widthMin = snap(parseInt(block.style.width, 10) || 30);
          if (leftMin + widthMin > 24 * 60) widthMin = 24 * 60 - leftMin;
          block.style.width = widthMin + 'px';
          resizeStart = null;
          updateWoSchedule(woId, lineId, date, leftMin, leftMin + widthMin);
        });
      }
    });
  });
})();

// ===== Work order detail (click) =====
(function () {
  function go(id) { window.location.href = '/production/work-orders/' + id; }
  // 周视图卡片/backlog：HTML5 拖拽完成后浏览器不触发 click，直接绑定即可
  document.querySelectorAll('.planner-card, .planner-backlog__item').forEach(function (el) {
    el.title = '点击查看工单详情';
    el.addEventListener('click', function () { go(el.dataset.woId); });
  });
  // 日视图块：自定义 mousedown 拖动/resize 后仍会触发 click，
  // 拖拽闭包把位移写入 block._moved，click 检查后消费
  document.querySelectorAll('.planner-gantt__block').forEach(function (block) {
    block.title = '点击查看工单详情（拖动改时间）';
    block.addEventListener('click', function () {
      if (block._moved) { block._moved = false; return; }
      go(block.dataset.woId);
    });
  });
})();

// ===== Recent changes drawer =====
(function () {
  var btn = document.getElementById('planner-changes-btn');
  var panel = document.getElementById('planner-changes-panel');
  var list = document.getElementById('planner-changes-list');
  if (!btn || !panel || !list) return;

  function load() {
    fetch('/production/planner/changes').then(function (r) { return r.json(); }).then(function (data) {
      if (!data.changes || !data.changes.length) {
        list.innerHTML = '<div style="color:#6b7280;padding:8px">暂无变更</div>';
        return;
      }
      list.innerHTML = data.changes.map(function (c) {
        var undone = c.undone_at ? 'planner-changes__item--undone' : '';
        var undoBtn = c.undone_at ? '' : '<button class="planner-changes__undo-btn" onclick="undoChange(' + c.id + ')">Undo</button>';
        var time = c.created_at ? new Date(c.created_at).toLocaleString('zh-CN') : '';
        return '<div class="planner-changes__item ' + undone + '">'
          + '<div><strong>#' + c.work_order_id + '</strong> ' + c.action + ' · ' + time + '</div>'
          + '<div style="color:#6b7280">' + (c.before ? JSON.stringify(c.before) : 'null') + ' → ' + (c.after ? JSON.stringify(c.after) : 'null') + '</div>'
          + undoBtn
          + '</div>';
      }).join('');
    }).catch(function (e) {
      list.innerHTML = '<div style="color:#dc2626">加载失败: ' + e + '</div>';
    });
  }

  window.undoChange = function (logId) {
    if (!confirm('确认 undo 此变更？')) return;
    fetch('/production/planner/changes/' + logId + '/undo', { method: 'POST' })
      .then(function (r) {
        if (r.ok) window.location.reload();
        else return r.json().catch(function () { return { error: 'undo 失败' }; }).then(function (d) {
          var msg = (d && d.error) || 'undo 失败';
          if (window.showErrorModal) window.showErrorModal(msg); else alert(msg);
        });
      })
      .catch(function (e) {
        if (window.showErrorModal) window.showErrorModal('网络错误: ' + e); else alert('网络错误: ' + e);
      });
  };

  btn.addEventListener('click', function () {
    panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
    if (panel.style.display === 'flex') load();
  });
})();
