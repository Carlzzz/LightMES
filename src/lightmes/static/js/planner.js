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
    card.addEventListener('click', function () {
      // 点击卡 → 弹详情浮层（简化：alert + 跳编辑页）
      window.location.href = '/production/planner/work-orders/' + card.dataset.woId + '/edit';
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
