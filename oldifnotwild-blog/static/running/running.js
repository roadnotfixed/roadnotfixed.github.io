(function () {
  const root = document.querySelector('.running-page[data-running-url]');
  if (!root) return;

  const yearSelect = document.getElementById('running-year');
  const lifetime = document.getElementById('running-lifetime');
  const updated = document.getElementById('running-updated');
  const stats = document.getElementById('running-stats');
  const heatmap = document.getElementById('running-heatmap');
  const heatmapTitle = document.getElementById('running-heatmap-title');
  const monthly = document.getElementById('running-monthly');
  const activitiesBody = document.getElementById('running-activities');
  const toggle = document.getElementById('running-toggle');
  const errorBox = document.getElementById('running-error');
  let allActivities = [];
  let showAll = false;

  const number = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 });

  function distanceTotal(items) {
    return items.reduce((sum, item) => sum + item.distance_km, 0);
  }

  function durationTotal(items) {
    return items.reduce((sum, item) => sum + item.duration_min, 0);
  }

  function formatDuration(minutes) {
    const rounded = Math.round(minutes);
    const hours = Math.floor(rounded / 60);
    const mins = rounded % 60;
    return hours ? `${hours}h ${mins}m` : `${mins}m`;
  }

  function formatPace(value) {
    if (!Number.isFinite(value) || value <= 0) return '—';
    let minutes = Math.floor(value);
    let seconds = Math.round((value - minutes) * 60);
    if (seconds === 60) {
      minutes += 1;
      seconds = 0;
    }
    return `${minutes}:${String(seconds).padStart(2, '0')} /km`;
  }

  function localDate(value) {
    const [year, month, day] = value.split('-').map(Number);
    return new Date(year, month - 1, day);
  }

  function dateKey(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  }

  function levelFor(distance) {
    if (distance <= 0) return 0;
    if (distance < 3) return 1;
    if (distance < 6) return 2;
    if (distance < 10) return 3;
    return 4;
  }

  function renderStats(items) {
    const totalDistance = distanceTotal(items);
    const totalDuration = durationTotal(items);
    const longest = Math.max(...items.map((item) => item.distance_km));
    const averagePace = totalDistance > 0 ? totalDuration / totalDistance : 0;
    const cards = [
      [number.format(totalDistance), '公里'],
      [number.format(items.length), '次跑步'],
      [formatDuration(totalDuration), '总时长'],
      [number.format(longest), '最长距离 km'],
      [formatPace(averagePace), '平均配速'],
    ];

    stats.replaceChildren();
    cards.forEach(([value, label]) => {
      const card = document.createElement('div');
      card.className = 'running-stat';
      const valueNode = document.createElement('span');
      valueNode.className = 'running-stat-value';
      valueNode.textContent = value;
      const labelNode = document.createElement('span');
      labelNode.className = 'running-stat-label';
      labelNode.textContent = label;
      card.append(valueNode, labelNode);
      stats.append(card);
    });
  }

  function renderHeatmap(items, year) {
    heatmapTitle.textContent = `${year} 跑步热力图`;
    heatmap.replaceChildren();
    const byDate = new Map();
    items.forEach((item) => {
      byDate.set(item.date, (byDate.get(item.date) || 0) + item.distance_km);
    });

    const start = new Date(year, 0, 1);
    const end = new Date(year, 11, 31);
    for (let index = 0; index < start.getDay(); index += 1) {
      const spacer = document.createElement('span');
      spacer.className = 'running-day-empty';
      heatmap.append(spacer);
    }
    for (let day = new Date(start); day <= end; day.setDate(day.getDate() + 1)) {
      const key = dateKey(day);
      const distance = byDate.get(key) || 0;
      const cell = document.createElement('span');
      cell.className = 'running-day';
      cell.dataset.level = String(levelFor(distance));
      cell.title = distance ? `${key} · ${number.format(distance)} km` : key;
      cell.setAttribute('aria-label', cell.title);
      heatmap.append(cell);
    }
  }

  function renderMonthly(items) {
    const totals = Array(12).fill(0);
    items.forEach((item) => {
      totals[localDate(item.date).getMonth()] += item.distance_km;
    });
    const maximum = Math.max(...totals, 1);
    monthly.replaceChildren();
    totals.forEach((value, index) => {
      const column = document.createElement('div');
      column.className = 'running-month';
      column.title = `${index + 1} 月 · ${number.format(value)} km`;
      const valueNode = document.createElement('span');
      valueNode.className = 'running-month-value';
      valueNode.textContent = value ? number.format(value) : '';
      const bar = document.createElement('div');
      bar.className = 'running-month-bar';
      bar.style.height = `${Math.max((value / maximum) * 135, value ? 3 : 0)}px`;
      const label = document.createElement('span');
      label.className = 'running-month-label';
      label.textContent = String(index + 1);
      column.append(valueNode, bar, label);
      monthly.append(column);
    });
  }

  function renderActivities(items) {
    const ordered = [...items].sort((a, b) => b.date.localeCompare(a.date));
    const visible = showAll ? ordered : ordered.slice(0, 20);
    activitiesBody.replaceChildren();
    visible.forEach((item) => {
      const row = document.createElement('tr');
      const values = [
        item.date,
        `${number.format(item.distance_km)} km`,
        formatDuration(item.duration_min),
        formatPace(item.pace_min_km),
      ];
      values.forEach((value) => {
        const cell = document.createElement('td');
        cell.textContent = value;
        row.append(cell);
      });
      activitiesBody.append(row);
    });
    toggle.hidden = ordered.length <= 20;
    toggle.textContent = showAll ? '收起' : `显示全部 ${ordered.length} 次`;
  }

  function renderYear() {
    const year = Number(yearSelect.value);
    const selected = allActivities.filter(
      (item) => localDate(item.date).getFullYear() === year
    );
    showAll = false;
    renderStats(selected);
    renderHeatmap(selected, year);
    renderMonthly(selected);
    renderActivities(selected);
  }

  toggle.addEventListener('click', () => {
    showAll = !showAll;
    const year = Number(yearSelect.value);
    renderActivities(
      allActivities.filter((item) => localDate(item.date).getFullYear() === year)
    );
  });

  yearSelect.addEventListener('change', renderYear);

  fetch(root.dataset.runningUrl, { cache: 'no-cache' })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((payload) => {
      allActivities = (payload.activities || []).filter(
        (item) =>
          /^\d{4}-\d{2}-\d{2}$/.test(item.date) &&
          Number.isFinite(item.distance_km) &&
          Number.isFinite(item.duration_min) &&
          item.distance_km > 0 &&
          item.duration_min > 0
      );
      if (!allActivities.length) throw new Error('没有可显示的跑步记录');

      const years = [
        ...new Set(allActivities.map((item) => localDate(item.date).getFullYear())),
      ].sort((a, b) => b - a);
      years.forEach((year) => {
        const option = document.createElement('option');
        option.value = String(year);
        option.textContent = String(year);
        yearSelect.append(option);
      });

      const firstDate = allActivities[0].date;
      lifetime.textContent = `${firstDate} 至今 · ${number.format(distanceTotal(allActivities))} km · ${allActivities.length} 次跑步`;
      updated.textContent = payload.generated_date
        ? `数据更新：${payload.generated_date}`
        : '';
      renderYear();
    })
    .catch((error) => {
      errorBox.hidden = false;
      errorBox.textContent = `跑步数据加载失败：${error.message}`;
    });
})();
