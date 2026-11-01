// «Сегодня»: semi-manual outreach. The app prepares the text and opens the chat;
// the admin presses «Отправить» in Telegram and then reports what happened.
(function () {
  const RESULTS = [
    ['sent', 'Отправил', 'primary'],
    ['not_sent', 'Не отправил', ''],
    ['no_account', 'Нет аккаунта', ''],
    ['replied', 'Ответили', ''],
    ['stop', 'Попросили не писать', 'danger'],
  ];
  const STAGE = { first: 'Первое сообщение', followup: 'Напоминание' };
  const TYPE = { no_site: 'Без сайта', weak_site: 'Конструктор', unavailable: 'Сайт недоступен', other: 'Есть сайт' };
  let data = null;
  let skipped = new Set();
  let timer = null;
  let current = null; // {business_id, contact, text}

  const bracketsLeft = (text) => /\[[^\]]+\]/.test(text || '');
  const fmtWait = (s) => (s >= 60 ? `${Math.floor(s / 60)} мин ${String(s % 60).padStart(2, '0')} с` : `${s} с`);

  function progress(b) {
    const pct = b.limit ? Math.min(100, Math.round((100 * b.used) / b.limit)) : 0;
    return `<section class="panel today-progress" aria-label="Лимит на сегодня">
      <div class="today-progress-head"><div>
        <h2><b>${E(b.used)}</b> из ${E(b.limit)}</h2></div>
        <div class="today-wait" id="today-wait" ${b.wait_seconds ? '' : 'hidden'}>Пауза <b>${fmtWait(b.wait_seconds)}</b></div></div>
      <div class="stat-bar-track" role="progressbar" aria-valuemin="0" aria-valuemax="${E(b.limit)}" aria-valuenow="${E(b.used)}"><i style="width:${pct}%"></i></div>
      <p class="muted small">Осталось ${E(b.left)} · пауза между сообщениями ${E(data.settings.delay_seconds)} с</p></section>`;
  }

  function warning() {
    if (data.acknowledged) {
      return `<div class="today-warning-line" role="note">Холодные сообщения: риск ограничения аккаунта за спам (@SpamBot). Пишите адресно.
        <details><summary>Подробнее</summary><p class="small">Рекламные рассылки без согласия подпадают под закон о рекламе. Сразу прекращайте, если просят. Лимит и паузу можно поменять в Настройках.</p></details></div>`;
    }
    return `<section class="panel today-warning" role="note"><h2>Перед началом</h2>
      <p>Вы пишете людям, которые не просили вам писать. Telegram может ограничить аккаунт за жалобы на спам — проверить можно в <b>@SpamBot</b>.
      Рекламные рассылки без согласия подпадают под закон о рекламе. Пишите адресно, без давления, и сразу прекращайте, если просят.</p>
      <p class="muted small">Решение остаётся за вами. Лимит и паузу можно поменять в Настройках.</p>
      <button type="button" class="btn primary" data-today-ack>Понятно</button></section>`;
  }

  function awaitingCard(a) {
    return `<article class="row today-awaiting"><div class="row-head"><div><span class="row-title">${E(a.name)}</span>
      <div class="muted small">${E(STAGE[a.stage])} · ${E(a.contact)} · чат открыт ${E((a.opened_at || '').slice(11, 16))} UTC</div></div></div>
      <p class="small">Отправили сообщение?</p>${resultButtons(a.outreach_id)}</article>`;
  }

  function resultButtons(id) {
    return `<div class="today-results">${RESULTS.map(([key, label, tone]) =>
      `<button type="button" class="btn ${tone}" data-today-mark="${E(id)}" data-result="${key}">${E(label)}</button>`).join('')}</div>`;
  }

  function currentCard(item) {
    const contact = current && current.business_id === item.business_id ? current.contact : item.contacts[0].contact;
    const text = current && current.business_id === item.business_id ? current.text : item.text;
    current = { business_id: item.business_id, stage: item.stage, contact, text };
    const b = data.budget;
    const blocked = b.left <= 0 ? 'Дневной лимит исчерпан' : b.wait_seconds > 0 ? 'Идёт пауза' : '';
    return `<section class="panel today-current" aria-label="Следующий адресат">
      <div class="panel-head"><div><span class="section-kicker">${E(STAGE[item.stage])}${item.pinned ? ' · добавлен вручную' : ''}</span>
        <h2>${E(item.name)}</h2><p>${E([item.niche, item.city].filter(Boolean).join(' · '))}</p></div>
        <button type="button" class="ghost" data-open="${E(item.business_id)}">Карточка</button></div>
      <div class="chips"><span class="chip">${E(TYPE[item.site_type] || item.site_type || '')}</span>${item.rating ? `<span class="chip">Рейтинг ${E(item.rating)} · ${E(item.reviews || 0)} отзывов</span>` : ''}</div>
      <fieldset class="today-contacts"><legend>Кому писать</legend>${item.contacts.map((c) =>
        `<label class="today-contact"><input type="radio" name="today-contact" value="${E(c.contact)}" ${c.contact === contact ? 'checked' : ''}>
         <span>${E(c.contact)}</span><small>${c.channel === 'username' ? 'Telegram' : 'по номеру'}</small></label>`).join('')}</fieldset>
      <label class="field">Текст<textarea id="today-text" rows="9">${E(text)}</textarea></label>
      <p class="muted small">В конце добавится: «${E(item.footer)}»</p>
      <p class="inline-note error-note" id="today-brackets" ${bracketsLeft(text) ? '' : 'hidden'}>Заполните поля в [квадратных скобках]</p>
      <div class="today-actions">
        <button type="button" class="btn primary today-open" data-today-open ${blocked || bracketsLeft(text) ? 'disabled' : ''}>${blocked ? E(blocked) : 'Открыть чат'}</button>
        <button type="button" class="ghost" data-today-skip>Пропустить</button></div></section>`;
  }

  function nextList(items) {
    if (!items.length) return '';
    return `<section class="panel"><div class="panel-head"><h2>Дальше в очереди</h2><span class="result-count">${items.length}</span></div>
      <div class="list">${items.slice(0, 12).map((i) => `<div class="row today-next"><div><span class="row-title">${E(i.name)}</span>
        <div class="muted small">${E(STAGE[i.stage])} · ${E(TYPE[i.site_type] || '')} · ${E(i.city || '')}</div></div></div>`).join('')}</div></section>`;
  }

  let segment = 'queue';
  const STATUS_ACTIONS = [['call', 'Позвонить'], ['replied', 'Ответили'], ['working', 'В работе'], ['completed', 'Договорились'], ['unsuitable', 'Отказ']];
  const daysAgo = (iso) => {
    if (!iso) return '';
    const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
    return days <= 0 ? 'сегодня' : days === 1 ? 'вчера' : `${days} дн. назад`;
  };
  function segments(count) {
    return `<div class="today-segments" role="tablist">
      <button type="button" role="tab" class="${segment === 'queue' ? 'active' : ''}" aria-selected="${segment === 'queue'}" data-today-segment="queue">Очередь</button>
      <button type="button" role="tab" class="${segment === 'contacted' ? 'active' : ''}" aria-selected="${segment === 'contacted'}" data-today-segment="contacted">Написал${count ? ` <b>${count}</b>` : ''}</button></div>`;
  }
  function contactedRow(r) {
    return `<article class="row today-lead"><div class="row-head"><div><span class="row-title">${E(r.name)}</span>
      <div class="muted small">${E([r.city, r.contact, r.written_at ? 'писали ' + daysAgo(r.written_at) : ''].filter(Boolean).join(' · '))}</div></div>
      <span class="chip">${E(state.config?.statuses?.[r.status] || r.status)}</span></div>
      <div class="today-status-actions">${STATUS_ACTIONS.filter(([key]) => key !== r.status).map(([key, label]) =>
        `<button type="button" class="btn ${key === 'unsuitable' ? 'ghost' : ''}" data-lead-status="${E(key)}" data-lead="${E(r.id)}">${E(label)}</button>`).join('')}
      ${r.chat_url ? `<button type="button" class="ghost" data-lead-chat="${E(r.chat_url)}">Открыть чат</button>` : ''}
      <button type="button" class="ghost" data-open="${E(r.id)}">Карточка</button></div></article>`;
  }
  async function contactedPage(node) {
    const rows = await call('outreach_contacted');
    node.innerHTML = segments(rows.length) + (rows.length
      ? `<section class="panel"><p class="muted small">Отмечайте, что ответили лиды. «Открыть чат» покажет переписку в Telegram.</p><div class="list">${rows.map(contactedRow).join('')}</div></section>`
      : `<section class="panel">${empty('Пока никому не писали', 'Здесь появятся лиды после отметки «Отправил».')}</section>`);
  }

  window.todayPage = async function (node) {
    setHeader('Сегодня', 'Кому написать сейчас');
    if (segment === 'contacted') { await contactedPage(node); return; }
    data = await call('outreach_today');
    const items = data.items.filter((i) => !skipped.has(i.business_id));
    if (!items.length && skipped.size) skipped = new Set();
    const list = items.length ? items : data.items;
    node.innerHTML = segments(0) + warning() + progress(data.budget)
      + (data.awaiting.length ? `<section class="panel"><h2>Ждут отметки</h2><div class="list">${data.awaiting.map(awaitingCard).join('')}</div></section>` : '')
      + (data.acknowledged
        ? (list.length ? currentCard(list[0]) + nextList(list.slice(1))
          : `<section class="panel today-empty">${empty('Очередь пуста', 'Откройте новые лиды и нажмите «Написать» в карточке — или запустите поиск. Напоминания приходят через 3 дня после первого сообщения.')}
            <div class="today-empty-actions"><button type="button" class="btn primary" data-today-goto="leads">Открыть новые лиды</button><button type="button" class="btn" data-today-goto="search">Новый поиск</button></div></section>`)
        : '');
    startTimer();
  };

  function startTimer() {
    clearTimeout(timer);
    if (!data || !data.budget.wait_seconds) return;
    let left = data.budget.wait_seconds;
    const step = () => {  // chain of timeouts: Desktop forbids independent intervals
      left -= 1;
      const node = document.querySelector('#today-wait b');
      if (state.view !== 'today') return;
      if (left <= 0) { render(); return; }
      if (node) node.textContent = fmtWait(left);
      timer = setTimeout(step, 1000);
    };
    timer = setTimeout(step, 1000);
  }

  function sheet(outreachId, name) {
    document.querySelector('.today-sheet')?.remove();
    const el = document.createElement('div');
    el.className = 'today-sheet';
    el.innerHTML = `<div class="today-sheet-panel" role="dialog" aria-modal="true" aria-labelledby="today-sheet-title">
      <h2 id="today-sheet-title">Отправили сообщение?</h2><p class="muted">${E(name)}</p>${resultButtons(outreachId)}
      <button type="button" class="ghost" data-today-later>Отмечу позже</button></div>`;
    document.body.appendChild(el);
  }

  async function mark(id, result) {
    await call('outreach_mark', Number(id), result);
    document.querySelector('.today-sheet')?.remove();
    current = null;
    LEM.platform.haptic(result === 'sent' || result === 'replied' ? 'success' : 'light');
    toast({ sent: 'Отмечено: отправлено', not_sent: 'Место в лимите возвращено', no_account: 'Контакт больше не предложим',
      replied: 'Статус: ответили', stop: 'Добавлено в блок-лист' }[result]);
    render();
  }

  document.addEventListener('input', (e) => {
    if (e.target.id !== 'today-text' || !current) return;
    current.text = e.target.value;
    const left = bracketsLeft(current.text);
    document.querySelector('#today-brackets')?.toggleAttribute('hidden', !left);
    const button = document.querySelector('[data-today-open]');
    if (button && data && data.budget.left > 0 && !data.budget.wait_seconds) button.disabled = left;
  });
  document.addEventListener('change', (e) => {
    if (e.target.name === 'today-contact' && current) current.contact = e.target.value;
  });
  document.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.hasAttribute('data-today-ack')) safe(async () => { await call('outreach_ack'); render(); });
    else if (b.hasAttribute('data-today-skip')) { skipped.add(current?.business_id); current = null; render(); }
    else if (b.hasAttribute('data-today-later')) { document.querySelector('.today-sheet')?.remove(); render(); }
    else if (b.dataset.todayMark) safe(() => mark(b.dataset.todayMark, b.dataset.result));
    else if (b.hasAttribute('data-today-open')) safe(async () => {
      const item = data.items.find((i) => i.business_id === current.business_id);
      const opened = await call('outreach_prepare', current.business_id, current.stage, current.contact, current.text);
      LEM.platform.openLink(opened.url);
      sheet(opened.outreach_id, item ? item.name : '');
    });
    else if (b.dataset.todayGoto) {
      if (b.dataset.todayGoto === 'leads' && window.leadsShowSegment) window.leadsShowSegment('new');
      setView(b.dataset.todayGoto);
    }
    else if (b.dataset.todaySegment) { segment = b.dataset.todaySegment; render(); }
    else if (b.dataset.leadStatus) safe(async () => {
      await call('business_save', b.dataset.lead, b.dataset.leadStatus);
      LEM.platform.haptic('success');
      toast('Статус: ' + (state.config?.statuses?.[b.dataset.leadStatus] || b.dataset.leadStatus));
      render();
    });
    else if (b.dataset.leadChat) safe(() => LEM.platform.openLink(b.dataset.leadChat));
    else if (b.hasAttribute('data-today-add')) safe(async () => { await call('outreach_add', b.dataset.todayAdd); toast('Добавлено первым в «Сегодня»'); });
  });

  // Settings block shared by desktop and Mini App.
  async function settingsBlock(node) {
    const s = await call('outreach_settings');
    node.insertAdjacentHTML('beforeend', `<section class="panel" id="settings-outreach"><h2>Сообщения «Сегодня»</h2>
      <form id="outreach-form" class="form-grid" style="margin-top:12px">
        <label class="field">Сообщений в день<input name="daily_limit" type="number" min="1" max="100" value="${E(s.daily_limit)}"></label>
        <label class="field">Пауза между сообщениями, с<input name="delay_seconds" type="number" min="30" max="3600" value="${E(s.delay_seconds)}"></label>
        <label class="field">Кому можно писать<select name="mode">
          <option value="cold" ${s.mode === 'cold' ? 'selected' : ''}>Всем из базы (холодные)</option>
          <option value="consent" ${s.mode === 'consent' ? 'selected' : ''}>Только с согласием</option></select></label>
        <div><button class="btn primary">Сохранить</button></div></form>
      <p class="muted small">Лимит обнуляется в полночь по Москве.</p></section>`);
  }
  // Desktop: «Админы» and «Домашний агент» are Mini App server features (docs/unify/DECISIONS.md U10).
  document.addEventListener('submit', (e) => {
    if (e.target.id !== 'outreach-form') return;
    e.preventDefault();
    e.stopImmediatePropagation();
    const f = new FormData(e.target);
    safe(async () => {
      await call('outreach_settings_save', { daily_limit: Number(f.get('daily_limit')), delay_seconds: Number(f.get('delay_seconds')), mode: f.get('mode') });
      toast('Настройки сохранены');
    });
  }, true);

  const baseRender = window.render;
  window.render = async function () {
    if (state.view === 'today') {
      const node = document.querySelector('#content');
      try { await todayPage(node); } catch (error) { node.innerHTML = empty('Не удалось загрузить очередь', error.message); }
      if (typeof syncBackButton === 'function') syncBackButton();
      return;
    }
    const result = await baseRender.apply(this, arguments);
    const node = document.querySelector('#content');
    if (state.view === 'settings') {
      try { await settingsBlock(node); } catch (_) { /* settings still usable */ }
    }
    if (state.view === 'detail' && state.businessId && !node.querySelector('[data-today-add]')) {
      node.querySelector('.panel-head')?.insertAdjacentHTML('beforeend',
        `<button type="button" class="btn" data-today-add="${E(state.businessId)}">В «Сегодня»</button>`);
    }
    return result;
  };
  render = window.render;

})();
