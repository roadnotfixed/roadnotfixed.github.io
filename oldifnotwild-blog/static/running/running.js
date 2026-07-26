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
  const routeSelect = document.getElementById('running-route-select');
  const routeSummary = document.getElementById('running-route-summary');
  const mapElement = document.getElementById('running-map');
  let allActivities = [];
  let showAll = false;
  let map = null;
  let routeLayer = null;

  const number = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 });

  function distanceTotal(items) {
    return items.reduce((sum, item) => sum + item.distance_km, 0);
  }

  function durationTotal(items) {
    return items.reduce((sum, item) => sum + item.duration_min, 0);
  }

  function averageHeartRate(items) {
    const withHeartRate = items.filter(
      (item) => Number.isFinite(item.avg_hr_bpm) && item.avg_hr_bpm > 0
    );
    const duration = durationTotal(withHeartRate);
    if (!withHeartRate.length || duration <= 0) return null;
    return (
      withHeartRate.reduce(
        (sum, item) => sum + item.avg_hr_bpm * item.duration_min,
        0
      ) / duration
    );
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
    const averageHr = averageHeartRate(items);
    const cards = [
      [number.format(totalDistance), '公里'],
      [number.format(items.length), '次跑步'],
      [formatDuration(totalDuration), '总时长'],
      [number.format(longest), '最长距离 km'],
      [formatPace(averagePace), '平均配速'],
      [averageHr ? `${Math.round(averageHr)} bpm` : '—', '平均心率'],
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

  function initMap() {
    if (!mapElement || !window.L) {
      routeSummary.textContent = '地图组件加载失败，跑步统计仍可正常查看。';
      return;
    }
    map = window.L.map(mapElement, { scrollWheelZoom: false }).setView([20, 0], 2);
    window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18,
      attribution: '&copy; OpenStreetMap contributors',
    }).addTo(map);
  }

  function showRoute(item) {
    if (!map || !item || !Array.isArray(item.route) || item.route.length < 2) {
      return;
    }
    if (routeLayer) map.removeLayer(routeLayer);
    routeLayer = window.L.polyline(item.route, {
      color: '#e96524',
      weight: 4,
      opacity: 0.9,
      lineJoin: 'round',
    }).addTo(map);
    map.fitBounds(routeLayer.getBounds(), { padding: [24, 24], maxZoom: 14 });
    const heartRate = Number.isFinite(item.avg_hr_bpm)
      ? ` · ${Math.round(item.avg_hr_bpm)} bpm`
      : '';
    routeSummary.textContent = `${item.date} · ${number.format(item.distance_km)} km · ${formatPace(item.pace_min_km)}${heartRate}`;
  }

  function renderRouteOptions(items) {
    const routes = [...items]
      .filter((item) => Array.isArray(item.route) && item.route.length >= 2)
      .sort((a, b) => b.date.localeCompare(a.date));
    routeSelect.replaceChildren();
    if (!routes.length) {
      const option = document.createElement('option');
      option.textContent = '该年份没有可公开路线';
      routeSelect.append(option);
      routeSelect.disabled = true;
      routeSummary.textContent = '该年份的记录没有匹配到可公开的 Apple Health 路线。';
      if (routeLayer && map) {
        map.removeLayer(routeLayer);
        routeLayer = null;
      }
      return;
    }

    routeSelect.disabled = false;
    routes.forEach((item) => {
      const option = document.createElement('option');
      option.value = String(item._index);
      option.textContent = `${item.date} · ${number.format(item.distance_km)} km`;
      routeSelect.append(option);
    });
    showRoute(routes[0]);
  }

  function selectRoute(item) {
    if (!item || !Array.isArray(item.route) || item.route.length < 2) return;
    routeSelect.value = String(item._index);
    showRoute(item);
    mapElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  function renderActivities(items) {
    const ordered = [...items].sort((a, b) => b.date.localeCompare(a.date));
    const visible = showAll ? ordered : ordered.slice(0, 20);
    activitiesBody.replaceChildren();
    visible.forEach((item) => {
      const row = document.createElement('tr');
      const hasRoute = Array.isArray(item.route) && item.route.length >= 2;
      row.dataset.hasRoute = String(hasRoute);
      if (hasRoute) {
        row.title = '点击查看路线';
        row.addEventListener('click', () => selectRoute(item));
      }
      const values = [
        item.date,
        `${number.format(item.distance_km)} km`,
        formatDuration(item.duration_min),
        formatPace(item.pace_min_km),
        Number.isFinite(item.avg_hr_bpm) ? `${Math.round(item.avg_hr_bpm)} bpm` : '—',
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

  function selectedYearActivities() {
    const year = Number(yearSelect.value);
    return allActivities.filter(
      (item) => localDate(item.date).getFullYear() === year
    );
  }

  function renderYear() {
    const year = Number(yearSelect.value);
    const selected = selectedYearActivities();
    showAll = false;
    renderStats(selected);
    renderHeatmap(selected, year);
    renderMonthly(selected);
    renderRouteOptions(selected);
    renderActivities(selected);
  }

  toggle.addEventListener('click', () => {
    showAll = !showAll;
    renderActivities(selectedYearActivities());
  });

  routeSelect.addEventListener('change', () => {
    showRoute(allActivities[Number(routeSelect.value)]);
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
      allActivities.forEach((item, index) => {
        item._index = index;
      });

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
      const heartRateCount = allActivities.filter((item) =>
        Number.isFinite(item.avg_hr_bpm)
      ).length;
      const routeCount = allActivities.filter(
        (item) => Array.isArray(item.route) && item.route.length >= 2
      ).length;
      lifetime.textContent = `${firstDate} 至今 · ${number.format(distanceTotal(allActivities))} km · ${allActivities.length} 次跑步 · ${heartRateCount} 条心率 · ${routeCount} 条路线`;
      updated.textContent = payload.generated_date
        ? `数据更新：${payload.generated_date}`
        : '';
      initMap();
      renderYear();
    })
    .catch((error) => {
      errorBox.hidden = false;
      errorBox.textContent = `跑步数据加载失败：${error.message}`;
    });
})();
