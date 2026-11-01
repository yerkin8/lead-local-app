// «Статистика» as plain charts: a funnel, ranked bars and a 14-day activity chart.
// One blue hue everywhere: every chart shows amounts, not categories.
(function () {
  const pct = (part, total) => (total ? Math.round((100 * part) / total) : 0);
  const TYPES = { no_site: 'Без сайта', weak_site: 'Сайт на конструкторе', unavailable: 'Сайт недоступен', other: 'Обычный сайт', own_site: 'Свой сайт' };
  const typeName = (key) => TYPES[key] || (window.LeadNav && LeadNav.siteType(key)) || 'Другое';
  const statusName = (key) => state.config?.statuses?.[key] || key;
  const plural = (n, one, few, many) => {
    const a = Math.abs(n) % 100, b = a % 10;
    return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
  };

  function funnel(s) {
    const steps = [
      ['Всего в базе', s.total, ''],
      ['Начали контакт', s.contacted, `${pct(s.contacted, s.total)}% базы`],
      ['Получили ответ', s.replied, `${pct(s.replied, s.contacted)}% от написанных`],
      ['Договорились', s.completed, `${pct(s.completed, s.contacted)}% от написанных`],
    ];
    const max = Math.max(1, s.total);
    return `<section class="panel chart-panel"><h2>Воронка</h2><p class="muted small">Сколько организаций дошло до каждого шага</p>
      <ol class="chart-funnel">${steps.map(([label, value, note], i) => `<li>
        <div class="chart-funnel-text"><span>${E(label)}</span><b>${E(value)}</b>${note ? `<small>${E(note)}</small>` : ''}</div>
        <div class="chart-track"><i class="tone-${i}" style="width:${Math.max(value ? 2 : 0, (100 * value) / max)}%"></i></div></li>`).join('')}</ol></section>`;
  }

  function ranked(title, hint, rows, total) {
    const max = Math.max(1, ...rows.map((r) => r[1]));
    const body = rows.length ? rows.map(([label, value]) => `<li title="${E(label)}: ${E(value)}">
        <div class="chart-bar-head"><span>${E(label)}</span><b>${E(value)}</b>${total ? `<small>${pct(value, total)}%</small>` : ''}</div>
        <div class="chart-track"><i style="width:${Math.max(value ? 2 : 0, (100 * value) / max)}%"></i></div></li>`).join('')
      : `<li class="muted">Данных пока нет</li>`;
    return `<section class="panel chart-panel"><h2>${E(title)}</h2><p class="muted small">${E(hint)}</p><ol class="chart-bars">${body}</ol></section>`;
  }

  function activity(timeline) {
    const perDay = {};
    for (const t of timeline) perDay[t.day] = (perDay[t.day] || 0) + t.count;
    const days = [];
    const today = new Date();
    for (let i = 13; i >= 0; i -= 1) {
      const d = new Date(today);
      d.setDate(today.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      days.push({ key, label: `${key.slice(8, 10)}.${key.slice(5, 7)}`, value: perDay[key] || 0 });
    }
    const max = Math.max(1, ...days.map((d) => d.value));
    const total = days.reduce((sum, d) => sum + d.value, 0);
    return `<section class="panel chart-panel"><h2>Работа по дням</h2>
      <p class="muted small">Сколько раз меняли статус за 14 дней — всего ${total}. Нажмите на столбик, чтобы увидеть число.</p>
      <p class="chart-readout" id="chart-readout" aria-live="polite">&nbsp;</p>
      <div class="chart-columns" role="list">${days.map((d, i) => `<button type="button" role="listitem" class="chart-column"
          data-chart-day="${E(d.label)}" data-chart-value="${E(d.value)}"
          aria-label="${E(d.label)}: ${E(d.value)} ${plural(d.value, 'изменение', 'изменения', 'изменений')}">
          <i style="height:${d.value ? Math.max(4, (100 * d.value) / max) : 0}%"></i>
          <span>${i % 2 === 1 || i === 13 ? E(d.label) : ''}</span></button>`).join('')}</div></section>`;
  }

  window.statsPage = async function (node) {
    setHeader('Статистика', 'Воронка, статусы и активность');
    const s = await call('stats');
    const statuses = Object.keys(state.config?.statuses || {}).map((k) => [statusName(k), s.statuses[k] || 0]);
    const types = Object.entries(s.site_types || {}).map(([k, v]) => [typeName(k), v]).sort((a, b) => b[1] - a[1]);
    const places = {};
    for (const g of s.groups) {
      const key = `${g.niche} · ${g.city}`;
      places[key] = (places[key] || 0) + g.count;
    }
    const topPlaces = Object.entries(places).sort((a, b) => b[1] - a[1]).slice(0, 10);
    node.innerHTML = funnel(s)
      + `<div class="two-cols">${ranked('Статусы', 'Где сейчас каждая организация', statuses, s.total)}
         ${ranked('Типы сайтов', 'Кому нужнее сайт', types, s.total)}</div>`
      + activity(s.timeline)
      + ranked('Ниши и города', 'Топ-10 по числу организаций', topPlaces, 0);
  };

  document.addEventListener('click', (e) => {
    const column = e.target.closest('[data-chart-day]');
    if (!column) return;
    document.querySelectorAll('.chart-column.active').forEach((c) => c.classList.remove('active'));
    column.classList.add('active');
    const n = Number(column.dataset.chartValue);
    const readout = document.querySelector('#chart-readout');
    if (readout) readout.textContent = `${column.dataset.chartDay}: ${n} ${plural(n, 'изменение', 'изменения', 'изменений')}`;
  });

  const baseRender = window.render;
  window.render = async function () {
    if (state.view !== 'stats') return baseRender.apply(this, arguments);
    const node = document.querySelector('#content');
    try { await statsPage(node); } catch (error) { node.innerHTML = empty('Не удалось загрузить статистику', error.message); }
    document.querySelectorAll('nav button[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === 'stats'));
    if (typeof syncBackButton === 'function') syncBackButton();
  };
  render = window.render;
})();
