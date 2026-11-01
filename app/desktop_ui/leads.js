// «Лиды»: one feed, one card, one main action. The whole daily loop in as few taps as possible:
// open a lead (1) → «Написать» (1, chat opens with a ready text) → back (status «Написал» is set
// automatically, a toast offers «Не отправил / Нет аккаунта») → «Следующий лид».
(function () {
  const SEGMENTS = [['new', 'Новые'], ['remind', 'Напомнить'], ['written', 'Написал'], ['talking', 'В диалоге'], ['all', 'Все']];
  const STATUSES = [['not_started', 'Новый'], ['call', 'Позвонить'], ['waiting', 'Написал'], ['replied', 'Ответили'], ['working', 'В работе'],
    ['completed', 'Договорились'], ['unsuitable', 'Отказ']];
  const TYPE = { no_site: 'Без сайта', weak_site: 'Конструктор', unavailable: 'Сайт не работает', other: 'Свой сайт' };
  const typeLabel = (key) => TYPE[key] || LeadNav.siteType(key);
  const AUTO_NEXT = 'lem.leads.autoNext';
  let offerIndex = -1;    // the offer chosen in the card's action bar; -1 = the card's own text
  const SAVED = 'lem.leads.v1';
  const narrow = () => window.matchMedia('(max-width: 760px)').matches;
  let L = { segment: 'new', q: '', niche: '', city: '', run: '', site_type: '', items: [], counts: {}, more: false, selecting: false, selected: new Set() };
  try { Object.assign(L, JSON.parse(sessionStorage.getItem(SAVED) || '{}'), { items: [], selecting: false, selected: new Set() }); } catch (_) { /* fresh */ }
  let lead = null;          // the open card
  let pending = null;       // {outreach_id, business_id, name} while the chat is open in Telegram
  let waitTimer = null;

  const save = () => { try { sessionStorage.setItem(SAVED, JSON.stringify({ segment: L.segment, q: L.q, niche: L.niche, city: L.city, run: L.run, site_type: L.site_type })); } catch (_) { /* private mode */ } };
  const plural = (n, a, b, c) => { const m = Math.abs(n) % 100, k = m % 10; return m > 10 && m < 20 ? c : k === 1 ? a : k >= 2 && k <= 4 ? b : c; };
  const fmtRating = (b) => (b.rating ? `${String(b.rating).replace('.', ',')} · ${b.reviews || 0} ${plural(b.reviews || 0, 'отзыв', 'отзыва', 'отзывов')}` : 'Без рейтинга');
  const statusName = (key) => (STATUSES.find(([k]) => k === key) || [key, key])[1];
  const filterQuery = () => ({ segment: L.segment, q: L.q, niche: L.niche, city: L.city, run: L.run, site_type: L.site_type });

  // ---------- snackbar with actions (undo instead of confirm dialogs) ----------
  let snackTimer = null;
  function snack(text, actions = [], ms = 6000) {
    let node = document.querySelector('#snackbar');
    if (!node) { node = document.createElement('div'); node.id = 'snackbar'; node.setAttribute('role', 'status'); document.body.appendChild(node); }
    node.innerHTML = `<span>${E(text)}</span>${actions.map((a, i) => `<button type="button" data-snack="${i}">${E(a[0])}</button>`).join('')}`;
    node.className = 'show';
    node.onclick = (e) => { const b = e.target.closest('[data-snack]'); if (!b) return; const a = actions[Number(b.dataset.snack)]; node.className = ''; clearTimeout(snackTimer); a && a[1](); };
    clearTimeout(snackTimer);
    snackTimer = setTimeout(() => { node.className = ''; }, ms);
  }
  window.leadSnack = snack;
  window.leadsShowSegment = (segment) => { L.segment = segment; L.selected.clear(); save(); };

  // ---------- main action: Telegram MainButton, or a sticky button outside Telegram ----------
  let mainHandler = null;
  function mainButton(text, handler, enabled = true) {
    mainHandler = enabled ? handler : null;
    const tg = window.Telegram?.WebApp;
    if (LEM.platform.isTelegram() && tg?.MainButton) {
      tg.MainButton.setText(text);
      if (!tg.MainButton.__lemBound) { tg.MainButton.onClick(() => mainHandler && mainHandler()); tg.MainButton.__lemBound = true; }
      tg.MainButton.show();
      try { enabled ? tg.MainButton.enable() : tg.MainButton.disable(); } catch (_) { /* old client */ }
      document.body.classList.remove('has-main-action');
      return;
    }
    let node = document.querySelector('#main-action');
    if (!node) { node = document.createElement('button'); node.id = 'main-action'; node.type = 'button'; document.body.appendChild(node); node.onclick = () => mainHandler && mainHandler(); }
    node.textContent = text;
    node.disabled = !enabled;
    node.hidden = false;
    document.body.classList.add('has-main-action');
  }
  function hideMain() {
    mainHandler = null;
    try { window.Telegram?.WebApp?.MainButton?.hide(); } catch (_) { /* old client */ }
    const node = document.querySelector('#main-action');
    if (node) node.hidden = true;
    document.body.classList.remove('has-main-action');
    clearTimeout(waitTimer);
  }

  // ---------- feed ----------
  function chips() {
    return `<div class="lead-segments" role="tablist" aria-label="Разделы лидов">${SEGMENTS.map(([key, label]) => {
      const n = L.counts[key];
      return `<button type="button" role="tab" aria-selected="${L.segment === key}" class="${L.segment === key ? 'active' : ''}" data-segment="${key}">${E(label)}${n ? ` <b>${E(n)}</b>` : ''}</button>`;
    }).join('')}</div>`;
  }
  function activeFilters() {
    const run = L.run && (window.__leadRuns || []).find((r) => r.id === L.run);
    const parts = [['niche', L.niche], ['city', L.city], ['site_type', L.site_type && TYPE[L.site_type]], ['run', run ? `Поиск ${run.niche || ''} · ${run.city || ''}`.trim() : (L.run ? 'Один поиск' : '')]].filter(([, v]) => v);
    if (!parts.length) return '';
    return `<div class="lead-active-filters">${parts.map(([k, v]) => `<button type="button" class="chip-filter" data-clear-filter="${k}" aria-label="Убрать фильтр ${E(v)}">${E(v)} ✕</button>`).join('')}</div>`;
  }
  function row(b) {
    const contact = b.channel ? 'Telegram' : b.has_vk ? 'ВК' : b.has_phone ? 'Только звонок' : 'Нет контакта';
    const sel = L.selecting ? `<span class="lead-check" aria-hidden="true">${L.selected.has(b.id) ? '✓' : ''}</span>` : '';
    return `<button type="button" class="lead-row ${L.selected.has(b.id) ? 'selected' : ''}" data-lead-open="${E(b.id)}">${sel}
      <span class="lead-row-main"><span class="lead-row-name">${E(b.name)}</span>
      <span class="lead-row-meta">${E([b.category, b.city].filter(Boolean).join(' · '))}</span>
      <span class="lead-row-meta">${E([fmtRating(b), typeLabel(b.site_type)].filter(Boolean).join(' · '))}</span></span>
      <span class="lead-row-side"><span class="lead-contact ${b.channel ? '' : (b.has_vk || b.has_phone) ? 'weak' : 'none'}">${E(contact)}</span>
      ${L.segment === 'new' ? '' : `<span class="lead-status s-${E(b.status)}">${E(statusName(b.status))}</span>`}</span></button>`;
  }
  function emptyState() {
    const text = {
      new: ['Новых лидов нет', 'Запустите поиск: найденные организации появятся здесь.', 'search', 'Новый поиск'],
      remind: ['Напоминать пока некому', 'Лид попадёт сюда через 3 дня после первого сообщения.', '', ''],
      written: ['Вы ещё никому не написали', 'Откройте лид в «Новых» и нажмите «Написать».', 'new', 'К новым лидам'],
      talking: ['Диалогов пока нет', 'Когда лид ответит, отметьте это в его карточке.', '', ''],
      all: ['Лидов не найдено', 'Измените поиск или фильтры.', 'clear', 'Сбросить фильтры'],
    }[L.segment];
    return `<div class="lead-empty"><strong>${E(text[0])}</strong><p>${E(text[1])}</p>${text[2] ? `<button type="button" class="btn primary" data-empty-action="${text[2]}">${E(text[3])}</button>` : ''}</div>`;
  }

  async function loadFeed(append = false) {
    const data = await call('leads_list', { ...filterQuery(), offset: append ? L.items.length : 0 });
    L.items = append ? L.items.concat(data.items) : data.items;
    L.counts = data.counts;
    L.more = data.more;
  }

  window.leadsPage = async function (node) {
    hideMain();
    setHeader('Лиды', 'Кому написать');
    await loadFeed(false);
    node.innerHTML = `<div class="lead-toolbar"><input id="lead-q" type="search" enterkeyhint="search" autocomplete="off"
        placeholder="Название или телефон" aria-label="Поиск по лидам" value="${E(L.q)}">
      <button type="button" class="ghost lead-filter-btn" data-lead-filters aria-label="Фильтры">Фильтры</button></div>
      ${chips()}${activeFilters()}
      <div class="lead-list-head"><span class="muted small">${E(L.counts[L.segment] || 0)} ${plural(L.counts[L.segment] || 0, 'лид', 'лида', 'лидов')}${L.segment === 'new' ? ' · сначала без сайта и с высоким рейтингом' : ''}</span>
        <span class="lead-head-actions">${L.counts[L.segment] && !L.selecting ? '<button type="button" class="link-btn" data-lead-mailing>Разослать этой пачке</button>' : ''}<button type="button" class="link-btn" data-lead-select>${L.selecting ? 'Готово' : 'Выбрать'}</button></span></div>
      <div class="lead-list" id="lead-list">${L.items.length ? L.items.map(row).join('') : emptyState()}</div>
      ${L.more ? '<button type="button" class="ghost lead-more" data-lead-more>Показать ещё</button>' : ''}`;
    if (L.selecting) selectionBar();
  };

  function selectionBar() {
    const n = L.selected.size;
    if (!n) { hideMain(); return; }
    mainButton(`Удалить ${n} ${plural(n, 'лид', 'лида', 'лидов')}`, () => safe(async () => {
      const ids = [...L.selected];
      await call('leads_hide', ids);
      L.selected.clear(); L.selecting = false;
      LEM.platform.haptic('warning');
      snack(`Удалено: ${ids.length}`, [['Отменить', () => safe(async () => { await call('leads_restore', ids); render(); })]]);
      render();
    }));
  }

  // ---------- card ----------
  function contactRows(b) {
    const rows = [];
    for (const c of b.contacts) {
      rows.push(`<button type="button" class="lead-line" data-open-chat="${E(c.url)}"><span>${c.channel === 'username' ? 'Telegram' : 'Telegram по номеру'}</span><b>${E(c.contact)}</b></button>`);
    }
    for (const phone of (b.phones || [])) {
      rows.push(`<a class="lead-line" href="tel:${E(String(phone).replace(/[^+\d]/g, ''))}"><span>Позвонить</span><b>${E(phone)}</b></a>`);
    }
    const bare = (u) => String(u).replace(/^https?:\/\//, '').replace(/\/$/, '').toLowerCase();
    const shown = new Set(b.contacts.map((c) => bare(c.url)));
    for (const l of (b.links || []).filter((x) => /vk\.com|t\.me|wa\.me|instagram|ok\.ru/.test(x.url) && !shown.has(bare(x.url)))) {
      rows.push(`<button type="button" class="lead-line" data-open-url="${E(l.url)}"><span>${E(l.label || 'Соцсеть')}</span><b>${E(l.url.replace(/^https?:\/\//, ''))}</b></button>`);
    }
    if (b.website) rows.push(`<button type="button" class="lead-line" data-open-url="${E(b.website)}"><span>${E(['Сайт', typeLabel(b.site_type)].filter(Boolean).join(' · '))}</span><b>${E(b.website.replace(/^https?:\/\//, ''))}</b></button>`);
    else rows.push(`<div class="lead-line static"><span>Сайт</span><b>${E(typeLabel(b.site_type) || 'Нет')}</b></div>`);
    if (b.address) rows.push(`<button type="button" class="lead-line" data-open-url="${E(b.map_url)}"><span>Адрес</span><b>${E(b.address)}</b></button>`);
    return rows.join('');
  }
  function more(title, body, open = false) {
    return body ? `<details class="lead-more-block" ${open ? 'open' : ''}><summary>${E(title)}</summary>${body}</details>` : '';
  }
  function details(b) {
    const hours = (b.hours || []).length ? `<div class="lead-kv">${b.hours.map((h) => `<div><span>${E(h.weekday)}</span><b>${h.is_closed ? 'выходной' : E(`${h.opens || ''}–${h.closes || ''}`)}</b></div>`).join('')}</div>` : '';
    const reviews = (b.review_items || []).length ? b.review_items.slice(0, 5).map((r) => `<p class="lead-review">${E(r.text)}${r.rating ? ` <small>${E(r.rating)} из 5</small>` : ''}</p>`).join('') : '';
    const links = (b.links || []).length ? `<div class="lead-kv">${b.links.map((l) => `<div><span>${E(l.label || l.type)}</span><button type="button" class="link-btn" data-open-url="${E(l.url)}">${E(l.url.replace(/^https?:\/\//, ''))}</button></div>`).join('')}</div>` : '';
    const history = (b.history || []).length ? `<div class="lead-kv">${b.history.slice(0, 8).map((h) => `<div><span>${E((h.changed_at || '').slice(0, 10))}</span><b>${E(statusName(h.old_status))} → ${E(statusName(h.new_status))}</b></div>`).join('')}</div>` : '';
    return more('Предложения: сайт, лендинг, бот, карты', typeof offersHtml === 'function' ? offersHtml(b.offers) : '')
      + more(`Отзывы${b.review_items?.length ? ` · ${b.review_items.length}` : ''}`, reviews)
      + more('Часы работы', hours) + more('Ссылки', links) + more('История', history);
  }

  function writeLabel(b) {
    if (!b.contacts.length) return ['Открыть в Яндекс.Картах', true];
    if (b.do_not_contact) return ['Просил не писать', false];
    if (b.budget.left <= 0) return ['Лимит на сегодня исчерпан', false];
    if (b.budget.wait_seconds > 0) return [`Пауза ${b.budget.wait_seconds} с`, false];
    return [b.stage === 'followup' ? 'Напомнить в Telegram' : 'Написать в Telegram', true];
  }

  // The main action is «Написать» when writing is possible right now; otherwise «Следующий лид»,
  // so the admin is never stuck (no contact, pause, limit, lead already handled).
  function armMain(b) {
    clearTimeout(waitTimer);
    const next = nextLeadId(b.id);
    const handled = b.status !== 'not_started' && b.stage !== 'followup';
    const canNow = b.contacts.length && !b.do_not_contact && b.budget.left > 0 && !b.budget.wait_seconds && !handled;
    setPauseNote(b);
    if (canNow) mainButton(writeLabel(b)[0], () => safe(() => writeLead()));
    else if (next) mainButton('Следующий лид', () => openLead(next, true));
    else hideMain();
    if (b.budget.wait_seconds > 0 && b.budget.left > 0 && b.contacts.length && !handled) {
      let left = b.budget.wait_seconds;
      // One-second countdown as a chain of timeouts: Desktop forbids independent intervals.
      const step = () => {
        left -= 1;
        if (state.view !== 'lead' || lead !== b) return;
        b.budget.wait_seconds = Math.max(0, left);
        setPauseNote(b);
        if (left <= 0) { armMain(b); return; }
        waitTimer = setTimeout(step, 1000);
      };
      waitTimer = setTimeout(step, 1000);
    }
  }
  function setPauseNote(b) {
    const node = document.querySelector('#lead-pause');
    if (!node) return;
    const handled = b.status !== 'not_started' && b.stage !== 'followup';
    let text = '';
    if (b.contacts.length && !handled) {
      if (b.budget.left <= 0) text = `Лимит на сегодня: ${b.budget.used} из ${b.budget.limit}. Разбирать и удалять лиды можно, писать — завтра.`;
      else if (b.budget.wait_seconds > 0) text = `Пауза от спама: написать можно через ${b.budget.wait_seconds} с. Пока можно перейти к следующему лиду.`;
    }
    node.textContent = text;
    node.hidden = !text;
  }

  // Sticky bar at the top of the card: offer → copy/write, status, prev/next (09-review-fixes).
  function offers(b) { return (b.offers || []).filter((o) => o.text); }
  function actionBar(b) {
    const list = offers(b);
    if (offerIndex >= list.length) offerIndex = -1;
    const chip = (i, name) => `<button type="button" role="radio" aria-checked="${i === offerIndex}" class="${i === offerIndex ? 'active' : ''}" data-bar-offer="${i}">${E(name)}</button>`;
    const nav = LeadNav.neighbours(L.items.map((i) => i.id), b.id);
    const hasNext = nav.next || (nav.index >= 0 && L.more);
    const auto = LeadNav.readFlag(AUTO_NEXT, true);
    const [label, ok] = writeLabel(b);
    return `<div class="lead-bar" role="toolbar" aria-label="Действия с лидом">
      ${list.length || b.text ? `<div class="lead-bar-row lead-bar-offers" role="radiogroup" aria-label="Предложение">${b.text ? chip(-1, 'Готовый текст') : ''}${list.map((o, i) => chip(i, o.label)).join('')}
        <span class="lead-bar-gap"></span>
        <button type="button" class="btn" data-bar-copy>Копировать</button>
        ${b.contacts.length ? `<button type="button" class="btn primary" data-bar-write ${ok ? '' : 'disabled'}>${E(label)}</button>` : ''}</div>` : ''}
      <div class="lead-bar-row"><div class="lead-status-row" role="radiogroup" aria-label="Статус">${STATUSES.map(([k, name]) =>
        `<button type="button" role="radio" aria-checked="${b.status === k}" class="${b.status === k ? 'active' : ''}" data-lead-set-status="${k}">${E(name)}</button>`).join('')}</div></div>
      <div class="lead-bar-row lead-bar-nav">
        <button type="button" class="ghost" data-lead-prev ${nav.prev ? '' : 'disabled'}>← Пред.</button>
        <span class="muted small">${nav.index >= 0 ? `${nav.index + 1} из ${E(L.counts[L.segment] || L.items.length)}` : 'Не из списка «Лиды»'}</span>
        <button type="button" class="ghost" data-lead-next ${hasNext ? '' : 'disabled'}>След. →</button>
        <label class="lead-bar-auto"><input type="checkbox" data-lead-auto ${auto ? 'checked' : ''}> Дальше после статуса</label></div></div>`;
  }
  async function stepLead(dir) {
    const ids = () => L.items.map((i) => i.id);
    let nav = LeadNav.neighbours(ids(), lead.id);
    if (dir > 0 && !nav.next && nav.index >= 0 && L.more) { await loadFeed(true); nav = LeadNav.neighbours(ids(), lead.id); }
    const id = dir > 0 ? nav.next : nav.prev;
    if (id) openLead(id, true);
    return Boolean(id);
  }
  function selectedOfferText() {
    const list = offers(lead);
    if (offerIndex < 0) return document.querySelector('#lead-text')?.value || lead.text || '';
    const area = document.querySelector(`[data-offer-text="${(lead.offers || []).indexOf(list[offerIndex])}"]`);
    return area ? area.value : (list[offerIndex] || {}).text || '';
  }

  window.leadPage = async function (node) {
    const fresh = await call('lead_card', state.businessId);
    if (!lead || lead.id !== fresh.id) offerIndex = -1;
    lead = fresh;
    const b = lead;
    setHeader('Лид', b.name);
    node.innerHTML = `<article class="lead-card">
      <header class="lead-head"><h2>${E(b.name)}</h2>
        <p>${E([b.category, b.city].filter(Boolean).join(' · '))}</p>
        <p class="lead-rating">${E(fmtRating(b))}${b.details?.photo_count ? ` · ${E(b.details.photo_count)} фото` : ''}</p></header>
      ${actionBar(b)}
      <div class="lead-actions">
        <button type="button" class="btn lead-maps" data-open-url="${E(b.map_url)}">Яндекс.Карты</button>
        <button type="button" class="ghost lead-delete" data-lead-delete>${b.hidden ? 'Вернуть' : 'Удалить'}</button></div>
      <p class="lead-pause" id="lead-pause" hidden></p>
      ${b.contacts.length ? '' : '<p class="lead-note-warn">Нет Telegram. Позвоните, напишите в соцсеть из блока ниже или откройте Яндекс.Карты.</p>'}
      ${b.do_not_contact ? '<p class="lead-note-warn">Этот лид попросил не писать.</p>' : ''}
      ${b.last_outreach ? `<p class="muted small">Последнее сообщение: ${E((b.last_outreach.resolved_at || b.last_outreach.opened_at || '').slice(0, 10))} · ${E(b.last_outreach.contact)}</p>` : ''}
      <div class="lead-lines">${contactRows(b)}</div>
      ${b.contacts.length ? more('Текст сообщения', `<p class="muted small">Подобран по типу сайта и нише. Можно поправить перед отправкой.</p>
        <textarea id="lead-text" rows="7">${E(b.text)}</textarea><p class="muted small">В конце добавится: «${E(b.footer)}»</p>`) : ''}
      ${details(b)}
      <div class="lead-rare"><button type="button" class="link-btn" data-menu="old">Старый вид карточки</button>${LEM.kind === 'desktop' ? '<button type="button" class="link-btn" data-menu="media">Фото и товары</button>' : ''}</div></article>`;
    armMain(b);
  };

  async function writeLead() {
    const b = lead;
    if (!b.acknowledged && !await LEM.platform.confirm('Холодные сообщения могут привести к ограничению аккаунта Telegram за спам (проверка в @SpamBot), а реклама без согласия подпадает под закон о рекламе. Решение за вами. Написать?')) return;
    const text = document.querySelector('#lead-text')?.value || b.text;
    if (/\[[^\]]+\]/.test(text)) { snack('Заполните поля в [квадратных скобках] в тексте сообщения'); document.querySelector('details.lead-more-block')?.setAttribute('open', ''); return; }
    const opened = await call('lead_write', b.id, '', text);
    pending = { outreach_id: opened.outreach_id, business_id: b.id, name: b.name };
    LEM.platform.haptic('light');
    LEM.platform.openLink(opened.url);
    mainButton('Отправил', () => safe(() => confirmSent()));
  }

  async function confirmSent() {
    if (!pending) return;
    const done = pending;
    pending = null;
    await call('lead_mark', done.outreach_id, 'sent');
    LEM.platform.haptic('success');
    snack(`${done.name}: написал`, [
      ['Не отправил', () => safe(async () => { await call('lead_correct', done.outreach_id, 'not_sent'); snack('Отметка снята'); reloadCard(); })],
      ['Нет аккаунта', () => safe(async () => { await call('lead_correct', done.outreach_id, 'no_account'); snack('Контакт больше не предложим'); reloadCard(); })],
    ], 8000);
    await reloadCard();
    const next = nextLeadId(done.business_id);
    // The pause applies to the next message, not to moving on: show «Следующий лид» right away.
    clearTimeout(waitTimer);
    if (next) mainButton('Следующий лид', () => openLead(next, true));
  }
  async function reloadCard() { if (state.view === 'lead') await render(); }

  function nextLeadId(current) {
    const ids = L.items.map((i) => i.id);
    const at = ids.indexOf(current);
    return at >= 0 ? ids[at + 1] : ids.find((id) => id !== current);
  }
  function openLead(id, replace = false) {
    document.querySelector('#snackbar')?.classList.remove('show');
    // «Следующий лид» replaces the card in place, so «Назад» still leads to the list.
    if (replace && state.view === 'lead') { state.businessId = id; render(); return; }
    setView('lead', { businessId: id });
  }

  // The admin comes back from the Telegram chat: assume it was sent, offer a correction.
  function onReturn() { if (pending && state.view === 'lead') safe(() => confirmSent()); }
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') setTimeout(onReturn, 300); });
  LEM.onReady(() => LEM.platform.onVisibility((active) => { if (active) setTimeout(onReturn, 300); }));

  function menuSheet() {
    const b = lead;
    const items = [
      ['delete', b.hidden ? 'Вернуть в список' : 'Удалить лид', 'danger'],
      ['old', 'Подробная карточка', ''],
      ...(LEM.kind === 'desktop' ? [['media', 'Фото и товары', '']] : []),
    ];
    sheet(`<h2>${E(b.name)}</h2>${items.map(([k, label, tone]) => `<button type="button" class="sheet-item ${tone}" data-menu="${k}">${E(label)}</button>`).join('')}`);
  }
  function sheet(html) {
    document.querySelector('.lead-sheet')?.remove();
    const el = document.createElement('div');
    el.className = 'lead-sheet';
    el.innerHTML = `<div class="lead-sheet-panel" role="dialog" aria-modal="true">${html}<button type="button" class="sheet-item muted" data-sheet-close>Закрыть</button></div>`;
    document.body.appendChild(el);
    el.addEventListener('click', (e) => { if (e.target === el || e.target.closest('[data-sheet-close]')) el.remove(); });
  }
  function filtersSheet() {
    safe(async () => {
      const f = await call('leads_filters');
      window.__leadRuns = f.runs;
      const select = (name, label, options) => `<label class="field">${E(label)}<select name="${name}"><option value="">Все</option>${options.map(([v, t]) => `<option value="${E(v)}" ${L[name] === v ? 'selected' : ''}>${E(t)}</option>`).join('')}</select></label>`;
      sheet(`<h2>Фильтры</h2><form id="lead-filter-form">
        ${select('niche', 'Ниша', f.niches.map((n) => [n, n]))}
        ${select('city', 'Город', f.cities.map((c) => [c, c]))}
        ${select('site_type', 'Сайт', Object.entries(TYPE))}
        ${select('run', 'Поиск', f.runs.map((r) => [r.id, `${r.niche} · ${r.city} · ${(r.created_at || '').slice(5, 10).split('-').reverse().join('.')}`]))}
        <button class="btn primary sheet-apply">Показать</button></form>`);
    });
  }

  // ---------- Поиск: two fields, one main action, live progress ----------
  let searchTimer = null;
  const JOB = { queued: 'В очереди', running: 'Идёт', done: 'Готово', failed: 'Ошибка', blocked: 'Яндекс ограничил', agent: 'У агента' };
  function jobRow(j) {
    const p = j.params || {}, r = j.result || {}, x = j.progress || {};
    const found = r.matched ?? x.matched ?? 0;
    const active = ['queued', 'running', 'agent'].includes(j.status);
    const minimum = Number(p.min_reviews) > 0 ? ` · от ${E(p.min_reviews)} ${plural(Number(p.min_reviews), 'отзыва', 'отзывов', 'отзывов')}` : '';
    return `<div class="search-job"><div><b>${E(p.niche)} · ${E(p.city)}${minimum}</b>
      <span class="muted small">${E(JOB[j.status] || j.status)} · найдено ${E(found)} из ${E(p.count || '')}${r.reason && !active ? ' · ' + E(r.reason) : ''}</span>
      ${active ? `<span class="search-progress"><i style="width:${Math.min(100, Math.round((100 * found) / Math.max(1, p.count || 1)))}%"></i></span>` : ''}</div>
      ${found && !active ? `<button type="button" class="link-btn" data-search-results="${E(j.id)}">Открыть лиды</button>` : ''}</div>`;
  }
  window.leadSearchPage = async function (node) {
    setHeader('Поиск', 'Новые лиды из Яндекс.Карт');
    const jobs = await call('search_jobs');
    const countries = state.config?.countries || ['Россия'];
    node.innerHTML = `<form id="lead-search-form" class="lead-search">
        <label class="field">Ниша<input name="niche" required minlength="2" autocomplete="off" placeholder="Например, кофейня" enterkeyhint="next"></label>
        <label class="field">Города<input name="cities" required minlength="2" autocomplete="off" placeholder="Казань, Самара" enterkeyhint="go"></label>
        <details class="lead-more-block"><summary>Ещё параметры</summary>
          <label class="field">Страна<select name="country">${countries.map((c) => `<option>${E(c)}</option>`).join('')}</select></label>
          <label class="field">Сколько новых найти в каждом городе<input name="count" type="number" min="1" max="100" value="20"></label>
          <label class="field">Минимум отзывов у бизнеса<input name="min_reviews" type="number" min="0" max="10000000" step="1" inputmode="numeric" placeholder="Не важно, например 500"></label>
          <label class="field">Сколько текстов отзывов сохранить<input name="review_limit" type="number" min="0" max="100" value="5"></label>
          <div class="checkboxes"><label><input type="checkbox" name="types" value="no_site" checked> Без сайта</label>
            <label><input type="checkbox" name="types" value="weak_site" checked> Конструктор</label>
            <label><input type="checkbox" name="types" value="unavailable" checked> Сайт не открывается</label></div>
        </details></form>
      <h3 class="search-history-title">Поиски</h3>
      <div class="search-jobs">${jobs.length ? jobs.slice(0, 15).map(jobRow).join('') : '<p class="muted">Здесь появятся ваши поиски и их прогресс.</p>'}</div>`;
    mainButton('Начать поиск', () => node.querySelector('#lead-search-form').requestSubmit());
    clearTimeout(searchTimer);
    if (jobs.some((j) => ['queued', 'running', 'agent'].includes(j.status))) {
      searchTimer = setTimeout(async function tick() {
        if (state.view !== 'search' || document.visibilityState === 'hidden') return;
        try { const fresh = await call('search_jobs'); const box = document.querySelector('.search-jobs'); if (box) box.innerHTML = fresh.slice(0, 15).map(jobRow).join('');
          if (fresh.some((j) => ['queued', 'running', 'agent'].includes(j.status))) searchTimer = setTimeout(tick, 5000); } catch (_) { /* retry on next visit */ }
      }, 5000);
    }
  };
  document.addEventListener('submit', (e) => {
    if (e.target.id !== 'lead-search-form') return;
    e.preventDefault(); e.stopImmediatePropagation();
    const f = new FormData(e.target);
    if (!e.target.reportValidity()) return;
    safe(async () => {
      const result = await call('search_start', { country: f.get('country'), cities: String(f.get('cities')), niche: String(f.get('niche')),
        count: Number(f.get('count') || 20), review_limit: Number(f.get('review_limit') || 0),
        min_reviews: Number(f.get('min_reviews') || 0), types: f.getAll('types') });
      LEM.platform.haptic('success');
      snack(`Поиск запущен: ${result.count} ${plural(result.count, 'город', 'города', 'городов')}. Бот напишет, когда будет готово.`);
      render();
    });
  }, true);

  // ---------- Итоги and Ещё ----------
  window.resultsPage = async function (node) {
    hideMain();
    setHeader('Итоги', 'Что сделано и что дальше');
    const s = await call('leads_summary');
    const tile = (label, value, note, action) => `<button type="button" class="result-tile" ${action ? `data-results-go="${action}"` : 'disabled'}>
      <span>${E(label)}</span><b>${E(value)}</b><small>${E(note)}</small></button>`;
    node.innerHTML = `<div class="result-tiles">
        ${tile('Написал сегодня', s.written_today, `ссылок через «Написать»: ${s.budget.used} из ${s.budget.limit}`, '')}
        ${tile('Напомнить', s.remind, s.remind ? 'открыть список' : 'пока некому', s.remind ? 'remind' : '')}
        ${tile('В диалоге', s.talking, s.talking ? 'открыть список' : 'ответов пока нет', s.talking ? 'talking' : '')}
        ${tile('Новые лиды', s.new, s.new ? 'начать писать' : 'запустите поиск', s.new ? 'new' : 'search')}
      </div><div id="results-stats"></div>`;
    try { await statsPage(node.querySelector('#results-stats')); } catch (_) { /* charts are optional */ }
    setHeader('Итоги', 'Что сделано и что дальше');
  };

  window.morePage = async function (node) {
    hideMain();
    setHeader('Ещё', 'Редкие действия');
    const items = [['scripts', 'Скрипты', 'Тексты сообщений, в том числе быстрые'], ['ai', 'ИИ-чат', 'ИИ пишет сообщения под категорию и бизнес'], ['settings', 'Настройки', 'Лимит сообщений, админы, данные'],
      ...(LEM.kind === 'desktop' ? [['media', 'Фото и товары', 'Галерея и товары организаций']] : [])];
    node.innerHTML = `<div class="more-list">${items.map(([view, title, note]) => `<button type="button" class="more-item" data-nav="${view}"><b>${E(title)}</b><span>${E(note)}</span></button>`).join('')}</div>`;
  };

  // ---------- events ----------
  document.addEventListener('click', (e) => {
    const b = e.target.closest('button, a');
    if (!b) return;
    if (b.dataset.segment) { L.segment = b.dataset.segment; L.selected.clear(); save(); LEM.platform.haptic('light'); render(); }
    else if (b.dataset.leadOpen) {
      if (L.selecting) { L.selected.has(b.dataset.leadOpen) ? L.selected.delete(b.dataset.leadOpen) : L.selected.add(b.dataset.leadOpen); b.classList.toggle('selected'); b.querySelector('.lead-check').textContent = L.selected.has(b.dataset.leadOpen) ? '✓' : ''; selectionBar(); return; }
      openLead(b.dataset.leadOpen);
    }
    else if (b.hasAttribute('data-lead-mailing')) { if (window.mailingFromLeads) window.mailingFromLeads(filterQuery()); }
    else if (b.hasAttribute('data-lead-select')) { L.selecting = !L.selecting; L.selected.clear(); render(); }
    else if (b.hasAttribute('data-lead-more')) safe(async () => { await loadFeed(true); document.querySelector('#lead-list').innerHTML = L.items.map(row).join(''); b.hidden = !L.more; });
    else if (b.hasAttribute('data-lead-filters')) filtersSheet();
    else if (b.dataset.clearFilter) { L[b.dataset.clearFilter] = ''; save(); render(); }
    else if (b.dataset.emptyAction) {
      const a = b.dataset.emptyAction;
      if (a === 'search') setView('search'); else if (a === 'clear') { Object.assign(L, { q: '', niche: '', city: '', run: '', site_type: '' }); save(); render(); } else { L.segment = a; save(); render(); }
    }
    else if (b.dataset.openChat) { e.preventDefault(); safe(() => LEM.platform.openLink(b.dataset.openChat)); }
    else if (b.dataset.openUrl) { e.preventDefault(); safe(() => LEM.platform.openLink(b.dataset.openUrl)); }
    else if (b.dataset.leadSetStatus) safe(async () => {
      await call('lead_status', lead.id, b.dataset.leadSetStatus);
      lead.status = b.dataset.leadSetStatus;
      document.querySelectorAll('[data-lead-set-status]').forEach((x) => { const on = x.dataset.leadSetStatus === lead.status; x.classList.toggle('active', on); x.setAttribute('aria-checked', on); });
      LEM.platform.haptic('success');
      armMain(lead);
      const id = lead.id, name = lead.name, refused = b.dataset.leadSetStatus === 'unsuitable';
      const moved = LeadNav.readFlag(AUTO_NEXT, true) && b.dataset.leadSetStatus !== 'not_started' && await stepLead(1);
      if (refused) snack(`${name}: отказ. Удалить из списков?`, [['Удалить', () => hideLead(id, name)]]);
      else if (moved) snack(`${name}: ${statusName(b.dataset.leadSetStatus)}`);
    });
    else if (b.hasAttribute('data-lead-prev')) safe(() => stepLead(-1));
    else if (b.hasAttribute('data-lead-next')) safe(() => stepLead(1));
    else if (b.dataset.barOffer) {
      offerIndex = Number(b.dataset.barOffer);
      document.querySelectorAll('[data-bar-offer]').forEach((x) => { const on = x === b; x.classList.toggle('active', on); x.setAttribute('aria-checked', on); });
      const area = document.querySelector('#lead-text');
      if (area) { if (offerIndex < 0) area.value = lead.text; else area.value = selectedOfferText(); }
    }
    else if (b.hasAttribute('data-bar-copy')) safe(async () => {
      const text = selectedOfferText();
      if (typeof copyText === 'function') await copyText(text); else await navigator.clipboard.writeText(text);
    });
    else if (b.hasAttribute('data-bar-write')) safe(() => writeLead());
    else if (b.hasAttribute('data-lead-delete')) { lead.hidden ? safe(async () => { await call('leads_restore', [lead.id]); snack('Лид возвращён'); render(); }) : hideLead(); }
    else if (b.dataset.menu) {
      document.querySelector('.lead-sheet')?.remove();
      if (b.dataset.menu === 'delete') { lead.hidden ? safe(async () => { await call('leads_restore', [lead.id]); snack('Лид возвращён'); render(); }) : hideLead(); }
      else if (b.dataset.menu === 'old') setView('detail', { businessId: lead.id });
      else if (b.dataset.menu === 'media') setView('media', { mediaBusiness: lead.id, mediaManual: false });
    }
    else if (b.dataset.resultsGo) { const go = b.dataset.resultsGo; if (go === 'search') setView('search'); else { L.segment = go; save(); setView('leads'); } }
  });
  function hideLead(id = lead.id, name = lead.name) {
    const next = state.view === 'lead' && lead && lead.id !== id ? null : nextLeadId(id);
    safe(async () => {
      await call('leads_hide', [id]);
      L.items = L.items.filter((i) => i.id !== id);
      LEM.platform.haptic('warning');
      if (state.view === 'lead' && lead && lead.id !== id) { /* already moved on */ } else if (next) openLead(next, true); else goBack();
      // After moving on, so the undo toast is not cleared by the navigation.
      snack(`${name}: удалён`, [['Отменить', () => safe(async () => { await call('leads_restore', [id]); render(); })]], 7000);
    });
  }
  document.addEventListener('change', (e) => { if (e.target.matches('[data-lead-auto]')) LeadNav.writeFlag(AUTO_NEXT, e.target.checked); });
  document.addEventListener('input', (e) => {
    if (e.target.id !== 'lead-q') return;
    clearTimeout(window.__leadQ);
    window.__leadQ = setTimeout(() => safe(async () => {
      L.q = e.target.value; save();
      await loadFeed(false);
      document.querySelector('#lead-list').innerHTML = L.items.length ? L.items.map(row).join('') : emptyState();
      document.querySelector('.lead-segments').outerHTML = chips();
    }), 300);
  });
  document.addEventListener('submit', (e) => {
    if (e.target.id !== 'lead-filter-form') return;
    e.preventDefault(); e.stopImmediatePropagation();
    const f = new FormData(e.target);
    for (const k of ['niche', 'city', 'site_type', 'run']) L[k] = f.get(k) || '';
    if (L.run) L.segment = 'all';
    save();
    document.querySelector('.lead-sheet')?.remove();
    render();
  }, true);
  // Desktop: «Результаты» in the search history keeps opening the Desktop results screen (U14).

  // ---------- routing ----------
  // Desktop: own search form and start page (Панель); «Ещё» is replaced by the sidebar (docs/unify/DECISIONS.md U14).
  const PAGES = { leads: () => leadsPage, lead: () => leadPage, results: () => resultsPage };
  const baseRender = window.render;
  window.render = async function () {
    const page = PAGES[state.view];
    document.body.classList.toggle('compact-top', Boolean(page));
    if (!page) { hideMain(); return baseRender.apply(this, arguments); }
    const node = document.querySelector('#content');
    try { await page()(node); } catch (error) { node.innerHTML = empty('Не удалось загрузить', error.message); }
    if (typeof syncBackButton === 'function') syncBackButton();
  };
  render = window.render;
})();
