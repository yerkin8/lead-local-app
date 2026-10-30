// «Рассылка»: Telegram accounts, campaigns, statistics, stop-list. No automatic refresh:
// data is read on navigation and by the explicit «Обновить» buttons; form drafts live in state.
const ML = {
  account: {needs_login: ['Нужен вход', 'warn'], active: ['Активен', 'green'], paused: ['Пауза', ''], quarantine: ['Карантин', 'warn']},
  campaign: {draft: 'Черновик', running: 'Идёт отправка', paused: 'Пауза', stopped: 'Остановлена', done: 'Завершена'},
  recipient: {queued: 'В очереди', sending: 'Отправляется', sent: 'Отправлено', dry_sent: 'Сухой запуск', skipped: 'Пропущено', unreachable: 'Недоступен', failed: 'Ошибка'},
  site: {'': 'Любой', no_site: 'Без сайта', weak_site: 'Сайт на конструкторе', unavailable: 'Сайт недоступен', other: 'Обычный сайт'},
  tabs: [['campaigns', 'Кампании'], ['accounts', 'Аккаунты'], ['stoplist', 'Стоп-лист'], ['policy', 'Правила отправки'], ['journal', 'Журнал']],
};
state.mailingTab ||= 'campaigns';
state.mailingCampaign ||= null;
state.mailingDraft ||= {title: '', template: '', kind: 'normal', limit: 3, accounts: [], filters: {contact: 'username', site_type: '', status: '', city: '', niche: ''}};
state.mailingLogin ||= {};

const mailingSnapshot = snapshotNavigation;
snapshotNavigation = function () { return {...mailingSnapshot(), mailingTab: state.mailingTab, mailingCampaign: state.mailingCampaign}; };
const mailingSamePlace = samePlace;
samePlace = function (a, b) { return mailingSamePlace(a, b) && a.mailingTab === b.mailingTab && a.mailingCampaign === b.mailingCampaign; };
const mailingRender = render;
render = async function () {
  if (state.view !== 'mailing') return mailingRender();
  const node = document.querySelector('#content');
  try { await mailingPage(node); } catch (error) { node.innerHTML = empty('Не удалось открыть раздел «Рассылка»', error.message); }
  syncBackButton();
};
function mlGo(extra) { pushNavigation(); Object.assign(state, extra); render(); }
function mlChip(text, tone = '') { return `<span class="chip ${tone}">${E(text)}</span>`; }
function mlTime(value) { return value ? String(value).replace('T', ' ').slice(0, 16) : '—'; }
function mlEmergency(o) {
  return o.emergency_stop
    ? `<div class="inline-note error-note ml-banner"><strong>Аварийный стоп включён.</strong> Все отправки остановлены, кампании на паузе. <button type="button" class="btn" data-ml="emergency-clear">Снять аварийный стоп</button></div>`
    : `<div class="ml-emergency"><button type="button" class="danger" data-ml="emergency">■ Аварийный стоп</button><span class="small muted">Останавливает все отправки сразу</span></div>`;
}

async function mailingPage(node) {
  const o = await call('mailing_overview');
  state.mailingOverview = o;
  if (!o.keys_configured) return mailingSetup(node, o);
  setHeader('Рассылка', 'Сообщения в Telegram с ваших аккаунтов по собранной базе');
  if (state.mailingCampaign) return mailingCampaignPage(node, o);
  if (state.mailingTab === 'new') return mailingNew(node, o);
  const tabs = `<div class="tabs ml-tabs">${ML.tabs.map(([key, label]) => `<button type="button" class="tab ${state.mailingTab === key ? 'active' : ''}" data-ml-tab="${key}">${label}</button>`).join('')}</div>`;
  node.innerHTML = `${mlEmergency(o)}${tabs}<div id="ml-body">${empty('Загрузка…')}</div>`;
  const body = node.querySelector('#ml-body');
  if (state.mailingTab === 'accounts') return mailingAccounts(body, o);
  if (state.mailingTab === 'stoplist') return mailingStoplist(body);
  if (state.mailingTab === 'policy') return mailingPolicy(body, o);
  if (state.mailingTab === 'journal') return mailingJournal(body, o);
  return mailingCampaigns(body, o);
}

function mailingKeysForm(o) {
  return `<form id="ml-keys-form" class="ml-form"><div class="form-grid"><label class="field">api_id<input name="api_id" inputmode="numeric" autocomplete="off" required placeholder="${o.api_id ? 'сохранён: ' + E(o.api_id) : 'число'}"></label>
    <label class="field">api_hash<input name="api_hash" type="password" autocomplete="off" required placeholder="32 символа"></label></div>
    <div class="toolbar"><button class="btn primary">Сохранить ключи</button><button type="button" class="btn" data-ml="import-file">Загрузить из файла…</button><button type="button" class="ghost" data-ml="open-apps">Где взять ключи ↗</button></div></form>`;
}

function mailingSetup(node, o) {
  setHeader('Рассылка · первый запуск', 'Шаг 1 из 2: ваши ключи Telegram API');
  node.innerHTML = `<section class="panel"><span class="section-kicker">МАСТЕР ПЕРВОГО ЗАПУСКА</span><h2>Подключите свой Telegram</h2>
    <p>Для отправки нужны <strong>ваши собственные</strong> api_id и api_hash: войдите на my.telegram.org → API development tools. Telegram запрещает передавать эти ключи другим людям, поэтому в программе их нет.</p>
    <div class="inline-note">Ключи и сессии шифруются вашей учётной записью Windows и хранятся только на этом компьютере. Файл владельца <code>secrets\\accounts.json</code> можно загрузить кнопкой — значения сразу шифруются, сам файл не копируется.</div>
    ${mailingKeysForm(o)}</section>`;
}

// ------------------------------------------------------------------ accounts
function mailingAccountRow(a) {
  const [label, tone] = ML.account[a.status] || [a.status, ''];
  const login = state.mailingLogin[a.id];
  const loginForm = !login ? '' : login.step === 'code'
    ? `<form class="ml-login" data-ml-login-code="${a.id}"><label class="field">Код из Telegram<input name="code" inputmode="numeric" autocomplete="one-time-code" required></label><button class="btn primary">Подтвердить</button><button type="button" class="ghost" data-ml-login-cancel="${a.id}">Отмена</button></form>`
    : `<form class="ml-login" data-ml-login-password="${a.id}"><label class="field">Пароль двухэтапной проверки<input name="password" type="password" autocomplete="current-password" required></label><button class="btn primary">Войти</button><button type="button" class="ghost" data-ml-login-cancel="${a.id}">Отмена</button></form>`;
  const actions = [
    a.status === 'needs_login' && !login ? `<button type="button" class="btn primary" data-ml-login="${a.id}">Войти</button>` : '',
    a.status === 'active' ? `<button type="button" class="btn" data-ml-account-status="${a.id}" data-status="paused">Пауза</button>` : '',
    a.status === 'paused' || a.status === 'quarantine' ? `<button type="button" class="btn" data-ml-account-status="${a.id}" data-status="active" ${a.status === 'quarantine' ? 'data-confirm="Аккаунт был в карантине из-за ограничений Telegram. Снимайте карантин только после проверки в @SpamBot. Продолжить?"' : ''}>${a.status === 'quarantine' ? 'Снять карантин' : 'Включить'}</button>` : '',
    a.status !== 'needs_login' ? `<button type="button" class="ghost" data-ml-logout="${a.id}">Выйти</button>` : '',
  ].join('');
  return `<div class="row"><div class="row-head"><div><span class="row-title">${E(a.label)}</span><div class="small muted">${E(a.phone_mask)} · сегодня ${E(a.sent_today)} из ${E(a.limit_today)} · всего ${E(a.sent_total)}</div></div><div class="row-actions">${actions}</div></div>
    <div class="chips">${mlChip(label, tone)}${a.is_test ? mlChip('Тестовый', 'purple') : ''}</div>
    ${a.status_reason ? `<div class="inline-note ${a.status === 'quarantine' ? 'error-note' : ''}">${E(a.status_reason)}${a.paused_until && a.status === 'paused' ? ` · до ${E(mlTime(a.paused_until))}` : ''}</div>` : ''}${loginForm}</div>`;
}

function mailingAccounts(body, o) {
  body.innerHTML = `<div class="two-cols"><section class="panel"><div class="panel-head"><div><h2>Аккаунты</h2><p>Отправка идёт только с аккаунтов со статусом «Активен».</p></div></div>
      <div class="list">${o.accounts.length ? o.accounts.map(mailingAccountRow).join('') : empty('Аккаунтов нет', 'Добавьте аккаунт справа или загрузите файл владельца')}</div></section>
    <div class="detail-stack"><section class="panel"><h2>Добавить аккаунт</h2><form id="ml-account-form" class="ml-form"><label class="field">Название<input name="label" required maxlength="40" placeholder="main-1"></label>
      <label class="field">Телефон<input name="phone" required inputmode="tel" autocomplete="off" placeholder="+7…"></label>
      <label class="ml-check"><input type="checkbox" name="is_test"> Тестовый аккаунт (пишет только тестовым адресатам)</label><button class="btn primary">Добавить</button></form></section>
      <section class="panel"><h2>Ключи и тестовые адресаты</h2><p>api_id: ${E(o.api_id || '—')} · тестовых адресатов: ${E(o.test_recipients)}</p><p class="small muted">Тестовые адресаты берутся из <code>secrets\\accounts.local.json</code> (поле <code>test_recipients</code>) кнопкой «Загрузить из файла».</p>${mailingKeysForm(o)}
      ${o.private_folder ? '' : '<div class="inline-note error-note">Не удалось ограничить права на папку данных рассылки. Данные всё равно зашифрованы.</div>'}</section></div></div>`;
}

// ----------------------------------------------------------------- campaigns
function mailingCounts(c) {
  const n = c.counts || {};
  return `${mlChip(`Отправлено ${n.sent || 0}`, 'green')}${n.dry_sent ? mlChip(`Сухой запуск ${n.dry_sent}`, 'purple') : ''}${mlChip(`В очереди ${(n.queued || 0) + (n.sending || 0)}`)}${mlChip(`Стоп-лист ${n.stoplist || 0}`)}${mlChip(`Недоступны ${n.unreachable || 0}`)}${n.failed ? mlChip(`Ошибки ${n.failed}`, 'warn') : ''}${n.replied ? mlChip(`Ответили ${n.replied}`, 'green') : ''}`;
}

async function mailingCampaigns(body) {
  const rows = await call('mailing_list');
  body.innerHTML = `<section class="panel"><div class="panel-head"><div><h2>Кампании</h2><p>Новая кампания всегда начинается с сухого запуска — без отправки.</p></div><button type="button" class="btn primary" data-ml-tab="new">Новая кампания</button></div>
    <div class="list">${rows.length ? rows.map(c => `<div class="row"><div class="row-head"><div><span class="row-title">${E(c.title)}</span><div class="small muted">${E(mlTime(c.created_at))} · ${c.kind === 'test' ? 'тестовая' : 'обычная'} · ${c.mode === 'live' ? 'реальная отправка' : 'сухой запуск'}</div></div><div class="row-actions">${mlChip(ML.campaign[c.status] || c.status, c.status === 'running' ? 'green' : c.status === 'stopped' ? 'warn' : '')}<button type="button" class="btn" data-ml-campaign="${c.id}">Открыть</button></div></div><div class="chips">${mailingCounts(c)}</div>${c.stop_reason ? `<div class="inline-note ${c.status === 'stopped' ? 'error-note' : ''}">${E(c.stop_reason)}</div>` : ''}</div>`).join('') : empty('Кампаний пока нет', 'Создайте первую кампанию и проверьте её сухим запуском')}</div></section>`;
}

async function mailingNew(node, o) {
  setHeader('Новая кампания', 'Получатели из базы, аккаунты, текст и предпросмотр на трёх записях');
  const d = state.mailingDraft;
  const [options, scripts] = await Promise.all([call('business_filter_options'), call('mailing_scripts', d.filters.site_type || '')]);
  const select = (name, values, labels = {}) => `<select name="${name}"><option value="">Любой</option>${values.map(v => `<option value="${E(v)}" ${d.filters[name] === v ? 'selected' : ''}>${E(labels[v] || v)}</option>`).join('')}</select>`;
  const usable = o.accounts.filter(a => a.status !== 'needs_login' && a.status !== 'quarantine');
  node.innerHTML = `${mlEmergency(o)}<form id="ml-new-form" class="detail-stack"><section class="panel"><span class="section-kicker">1 · ПОЛУЧАТЕЛИ</span><h2>Кому пишем</h2><div class="form-grid">
      <label class="field">Название кампании<input name="title" maxlength="120" value="${E(d.title)}" placeholder="Кафе без сайта · Казань"></label>
      <label class="field">Тип сайта<select name="site_type">${Object.entries(ML.site).map(([k, v]) => `<option value="${k}" ${d.filters.site_type === k ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
      <label class="field">Статус работы${select('status', options.status || [], state.config?.statuses || {})}</label>
      <label class="field">Город${select('city', options.city || [])}</label>
      <label class="field">Ниша${select('niche', options.niche || [])}</label>
      <label class="field">Контакт<select name="contact"><option value="username" ${d.filters.contact === 'username' ? 'selected' : ''}>Есть username Telegram</option><option value="any" ${d.filters.contact === 'any' ? 'selected' : ''}>Любой</option></select></label>
      <label class="field">Тип кампании<select name="kind"><option value="normal" ${d.kind === 'normal' ? 'selected' : ''}>Обычная — организациям</option><option value="test" ${d.kind === 'test' ? 'selected' : ''}>Тестовая — на свои аккаунты</option></select></label>
      <label class="field">Сколько организаций (пусто — все)<input name="limit" type="number" min="1" max="2000" value="${E(d.limit || '')}"></label></div>
      <p class="small muted">Отправка возможна только по username из ссылки t.me в карточке. Номера используются для дедупликации и стоп-листа.</p></section>
    <section class="panel"><span class="section-kicker">2 · АККАУНТЫ</span><h2>С каких аккаунтов</h2><div class="checkboxes">${usable.length ? usable.map(a => `<label><input type="checkbox" name="accounts" value="${a.id}" ${d.accounts.includes(String(a.id)) ? 'checked' : ''}> ${E(a.label)}${a.is_test ? ' · тестовый' : ''} · ${E((ML.account[a.status] || [a.status])[0])}</label>`).join('') : '<span class="muted">Нет готовых аккаунтов — войдите во вкладке «Аккаунты».</span>'}</div>
      <p class="small muted">Получатели распределяются по аккаунтам при создании и не переносятся, если аккаунт ограничен.</p></section>
    <section class="panel"><span class="section-kicker">3 · СООБЩЕНИЕ</span><h2>Текст</h2>
      <div class="toolbar"><select id="ml-script" aria-label="Подсказка скрипта">${scripts.items.map(s => `<option value="${E(s.slug)}" ${s.slug === scripts.preferred ? 'selected' : ''}>${E(s.title)}${s.slug === scripts.preferred ? ' · подходит к типу сайта' : ''}</option>`).join('')}</select><button type="button" class="btn" data-ml="use-script">Подставить подсказку</button></div>
      <label class="field">Шаблон<textarea name="template" rows="8" placeholder="Здравствуйте! Посмотрел карточку {название} в {город}…">${E(d.template)}</textarea></label>
      <p class="small muted">Переменные: {название}, {город}, {ниша}, {тип_сайта}. Поля в [квадратных скобках] нужно заменить — с ними кампания не создаётся. Строка про «СТОП» добавляется автоматически.</p>
      <div class="toolbar"><button type="button" class="btn" data-ml="preview">Предпросмотр на 3 записях</button><button class="btn primary">Создать — сухой запуск</button><button type="button" class="ghost" data-ml-tab="campaigns">Отмена</button></div>
      <div id="ml-preview"></div></section></form>`;
}

function mailingCollect(form) {
  const x = new FormData(form);
  const d = state.mailingDraft;
  d.title = x.get('title') || ''; d.template = x.get('template') || ''; d.kind = x.get('kind') || 'normal';
  d.limit = x.get('limit') ? Number(x.get('limit')) : '';
  d.accounts = x.getAll('accounts');
  for (const key of ['site_type', 'status', 'city', 'niche', 'contact']) d.filters[key] = x.get(key) || '';
  const filters = Object.fromEntries(Object.entries(d.filters).filter(([, v]) => v));
  return {title: d.title, template: d.template, kind: d.kind, limit: d.limit || null, accounts: d.accounts.map(Number), filters};
}

async function mailingCampaignPage(node, o) {
  const c = await call('mailing_detail', state.mailingCampaign);
  setHeader(c.title, `${c.kind === 'test' ? 'Тестовая' : 'Обычная'} кампания · ${c.mode === 'live' ? 'реальная отправка' : 'сухой запуск'}`);
  const live = c.mode === 'live';
  const controls = [
    !live && c.status === 'draft' ? '<button type="button" class="btn primary" data-ml="dry">Сухой запуск</button>' : '',
    live && ['draft', 'paused', 'stopped'].includes(c.status) ? `<button type="button" class="btn primary" data-ml="run">${c.status === 'draft' ? 'Начать отправку' : 'Продолжить'}</button>` : '',
    c.status === 'running' ? '<button type="button" class="btn" data-ml="pause">Пауза</button>' : '',
    '<button type="button" class="btn" data-ml="refresh-campaign">Обновить статистику</button>',
    !c.counts.sent && c.status !== 'running' ? '<button type="button" class="ghost" data-ml="delete">Удалить</button>' : '',
  ].join('');
  const goLive = !live ? `<form id="ml-live-form" class="ml-live"><div><strong>Реальная отправка выключена.</strong> Чтобы включить её осознанно, введите слово <code>${E(o.confirm_word)}</code>.${c.kind === 'normal' ? ' Сообщения получат настоящие организации.' : ' Сообщения получат только ваши тестовые адресаты.'}</div><input name="confirm" autocomplete="off" aria-label="Слово подтверждения"><button class="danger">Включить реальную отправку</button></form>` : '';
  node.innerHTML = `${mlEmergency(o)}<section class="panel"><div class="panel-head"><div><span class="section-kicker">${E(ML.campaign[c.status] || c.status)}</span><h2>${E(c.title)}</h2><p>Создана ${E(mlTime(c.created_at))}${c.started_at ? ` · запуск ${E(mlTime(c.started_at))}` : ''}</p></div><div class="toolbar">${controls}</div></div>
      ${c.stop_reason ? `<div class="inline-note ${c.status === 'stopped' ? 'error-note' : ''}">${E(c.stop_reason)}</div>` : ''}${goLive}<div id="ml-stats">${mailingStats(c)}</div></section>
    <section class="panel"><h2>Получатели</h2><div class="list">${c.recipients.map(r => `<div class="row small"><div class="row-head"><div><span class="row-title">${E(r.business_name)}</span><div class="muted">${r.username ? (String(r.username).startsWith('+') || String(r.username).includes('•') ? E(r.username) : '@' + E(r.username)) : 'нет username'}${r.msg_id ? ` · сообщение № ${E(r.msg_id)}` : ''}${r.sent_at ? ` · ${E(mlTime(r.sent_at))}` : ''}</div></div>${mlChip(ML.recipient[r.status] || r.status, r.status === 'sent' ? 'green' : ['failed', 'unreachable'].includes(r.status) ? 'warn' : '')}</div>${r.reason ? `<div class="muted">${E(r.reason)}</div>` : ''}<details><summary>Текст</summary><pre class="ml-text">${E(r.message)}</pre></details></div>`).join('')}</div></section>`;
}

function mailingStats(c) {
  const n = c.counts;
  const metrics = [['Отправлено', n.sent], ['Сухой запуск', n.dry_sent], ['В очереди', n.queued + n.sending], ['Пропущено по стоп-листу', n.stoplist], ['Недоступны', n.unreachable], ['Ошибки', n.failed], ['Ответили', n.replied], ['Всего', n.total]];
  return `<div class="metrics ml-metrics">${metrics.map(([label, value]) => metric(label, value)).join('')}</div>
    <div class="two-cols"><div><h3>Причины пропусков и ошибок</h3>${c.reasons.length ? `<table class="ml-table"><tbody>${c.reasons.map(r => `<tr><td>${E(ML.recipient[r.status] || r.status)}</td><td>${E(r.reason)}</td><td>${E(r.count)}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">Нет</p>'}</div>
    <div><h3>По аккаунтам</h3><table class="ml-table"><thead><tr><th>Аккаунт</th><th>Состояние</th><th>Отправлено</th><th>Осталось</th><th>Ошибки</th></tr></thead><tbody>${c.accounts.map(a => `<tr><td>${E(a.label)}${a.is_test ? ' · тест' : ''}</td><td>${E((ML.account[a.status] || [a.status])[0])}</td><td>${E(a.sent || 0)}</td><td>${E(a.left || 0)}</td><td>${E(a.errors || 0)}</td></tr>`).join('')}</tbody></table></div></div>
    <h3>События</h3>${c.events.length ? `<div class="list">${c.events.slice(0, 15).map(e => `<div class="row small"><span class="muted">${E(mlTime(e.at))}</span> ${E(e.message)}</div>`).join('')}</div>` : '<p class="muted">Событий нет</p>'}`;
}

// ------------------------------------------------------------ other tabs
async function mailingStoplist(body) {
  const rows = await call('mailing_stoplist');
  const kinds = {business: 'Организация', username: 'Username', phone: 'Телефон', telegram_user: 'Аккаунт Telegram'};
  body.innerHTML = `<section class="panel"><div class="panel-head"><div><h2>Стоп-лист</h2><p>Эти получатели никогда не попадают в очередь. Ответ «стоп» добавляет сюда автоматически. Учитывается и отметка «Не писать» из CRM.</p></div></div>
    <form id="ml-stop-form" class="toolbar"><select name="kind" aria-label="Тип записи"><option value="username">Username</option><option value="phone">Телефон</option><option value="business">ID организации</option></select><input name="value" required placeholder="@username, телефон или ID"><button class="btn primary">Добавить</button></form>
    <div class="list">${rows.length ? rows.map(r => `<div class="row small"><div class="row-head"><div><span class="row-title">${E(kinds[r.kind] || r.kind)}: ${E(r.value)}</span><div class="muted">${E(r.label || '')} · ${r.source === 'reply' ? 'ответ «стоп»' : 'вручную'} · ${E(mlTime(r.created_at))}</div></div>${r.source === 'manual' ? `<button type="button" class="ghost" data-ml-stop-remove="${r.id}">Убрать</button>` : ''}</div></div>`).join('') : empty('Стоп-лист пуст')}</div></section>`;
}

function mailingPolicy(body, o) {
  const p = o.policy;
  const field = (name, label, min, max) => `<label class="field">${label}<input name="${name}" type="number" min="${min}" max="${max}" value="${E(p[name])}"></label>`;
  body.innerHTML = `<section class="panel"><h2>Правила отправки</h2><p>Значения по умолчанию консервативные. Новый аккаунт начинает с малого и наращивает лимит каждый день.</p>
    <form id="ml-policy-form"><div class="form-grid">${field('first_day_limit', 'Лимит в первый день', 1, 50)}${field('daily_increment', 'Прирост лимита в день', 0, 10)}${field('daily_cap', 'Потолок в день', 1, 50)}${field('hourly_cap', 'Не больше в час', 1, 30)}${field('delay_min', 'Пауза от, с', 45, 3600)}${field('delay_max', 'Пауза до, с', 45, 7200)}${field('window_start', 'Окно с (час получателя)', 0, 23)}${field('window_end', 'Окно до (час)', 1, 24)}${field('flood_wait_max', 'Ждать FloodWait не дольше, с', 30, 3600)}</div>
    <label class="ml-check"><input type="checkbox" name="weekdays_only" ${p.weekdays_only ? 'checked' : ''}> Только будни</label>
    <div class="inline-note">При ограничении аккаунта (PeerFlood, бан, заморозка) он уходит в карантин, его кампания останавливается. Трафик на другие аккаунты не переносится.</div>
    <div class="toolbar"><button class="btn primary">Сохранить правила</button></div></form></section>`;
}

function mailingJournal(body, o) {
  body.innerHTML = `<section class="panel"><div class="panel-head"><div><h2>Журнал</h2><p>Последние события модуля. Номера и ключи в журнал не попадают.</p></div><button type="button" class="btn" data-ml-tab="journal">Обновить</button></div><div class="list">${o.events.length ? o.events.map(e => `<div class="row small"><span class="chip ${e.level === 'error' ? 'warn' : ''}">${E(e.kind)}</span> <span class="muted">${E(mlTime(e.at))}</span> ${E(e.message)}</div>`).join('') : empty('Событий нет')}</div></section>`;
}

// ---------------------------------------------------------------- handlers
document.addEventListener('input', event => {
  const form = event.target.closest?.('#ml-new-form');
  if (form) mailingCollect(form);
});
document.addEventListener('change', event => {
  const form = event.target.closest?.('#ml-new-form');
  if (form) mailingCollect(form);
});

document.addEventListener('click', event => {
  const b = event.target.closest('button');
  if (!b || state.view !== 'mailing') return;
  const d = b.dataset;
  if (!(d.ml || d.mlTab || d.mlCampaign || d.mlLogin || d.mlLoginCancel || d.mlAccountStatus || d.mlLogout || d.mlStopRemove)) return;
  event.preventDefault(); event.stopImmediatePropagation();
  safe(async () => {
    if (d.mlTab) { const form = document.querySelector('#ml-new-form'); if (form) mailingCollect(form); mlGo({mailingTab: d.mlTab, mailingCampaign: null}); return; }
    if (d.mlCampaign) { mlGo({mailingCampaign: Number(d.mlCampaign)}); return; }
    if (d.mlLogin) { await call('mailing_login_request', d.mlLogin); state.mailingLogin[d.mlLogin] = {step: 'code'}; toast('Код отправлен в Telegram на этот номер'); render(); return; }
    if (d.mlLoginCancel) { await call('mailing_login_cancel', d.mlLoginCancel); delete state.mailingLogin[d.mlLoginCancel]; render(); return; }
    if (d.mlAccountStatus) { if (d.confirm && !confirm(d.confirm)) return; await call('mailing_account_status', d.mlAccountStatus, d.status); render(); return; }
    if (d.mlLogout) { if (!confirm('Удалить сессию этого аккаунта с компьютера? Для отправки снова понадобится вход.')) return; await call('mailing_account_logout', d.mlLogout); render(); return; }
    if (d.mlStopRemove) { await call('mailing_stop_remove', d.mlStopRemove); render(); return; }
    const id = state.mailingCampaign;
    switch (d.ml) {
      case 'emergency': await call('mailing_emergency'); toast('Аварийный стоп: все отправки остановлены', true); render(); break;
      case 'emergency-clear': if (confirm('Снять аварийный стоп? Кампании останутся на паузе, их нужно продолжить вручную.')) { await call('mailing_emergency_clear'); render(); } break;
      case 'import-file': { const r = await call('mailing_import_file'); if (r) toast(`Ключи: ${r.keys ? 'сохранены' : 'нет в файле'} · аккаунтов добавлено ${r.accounts_added} · тестовых адресатов ${r.test_recipients}`); render(); break; }
      case 'open-apps': await call('open_external', 'https://my.telegram.org/apps'); break;
      case 'use-script': { const form = document.querySelector('#ml-new-form'); const slug = document.querySelector('#ml-script').value; const scripts = await call('mailing_scripts', state.mailingDraft.filters.site_type || ''); const item = scripts.items.find(s => s.slug === slug); if (item) { form.querySelector('[name="template"]').value = item.body; mailingCollect(form); toast('Подсказка подставлена. Замените поля в [скобках]'); } break; }
      case 'preview': { const form = document.querySelector('#ml-new-form'); const payload = mailingCollect(form); const p = await call('mailing_preview', payload); document.querySelector('#ml-preview').innerHTML = `<div class="inline-note">Подходит организаций: ${E(p.total)}, с username: ${E(p.with_username)}${p.unfilled.length ? ` · <strong>замените: ${E(p.unfilled.join(', '))}</strong>` : ''}</div>${p.items.length ? p.items.map(x => `<div class="row"><span class="row-title">${E(x.name)}</span> <span class="muted">${E(x.city)} · ${E(ML.site[x.site_type] || x.site_type)} · ${x.username ? '@' + E(x.username) : 'нет username'}</span><pre class="ml-text">${E(x.text)}</pre></div>`).join('') : empty('Нет записей для предпросмотра')}`; break; }
      case 'dry': await call('mailing_run', id); toast('Сухой запуск завершён — ничего не отправлено'); render(); break;
      case 'run': if (!confirm('Начать реальную отправку?')) return; await call('mailing_run', id); toast('Отправка запущена'); render(); break;
      case 'pause': await call('mailing_pause', id); toast('Кампания на паузе'); render(); break;
      case 'refresh-campaign': { const c = await call('mailing_detail', id); document.querySelector('#ml-stats').innerHTML = mailingStats(c); toast('Статистика обновлена'); break; }
      case 'delete': if (!confirm('Удалить кампанию?')) return; await call('mailing_remove', id); state.mailingCampaign = null; render(); break;
    }
  });
}, true);

document.addEventListener('submit', event => {
  const f = event.target;
  if (state.view !== 'mailing' || !(f.id?.startsWith('ml-') || f.dataset.mlLoginCode || f.dataset.mlLoginPassword)) return;
  event.preventDefault(); event.stopImmediatePropagation();
  safe(async () => {
    const x = new FormData(f);
    if (f.id === 'ml-keys-form') { await call('mailing_save_keys', x.get('api_id'), x.get('api_hash')); toast('Ключи сохранены и зашифрованы'); state.mailingTab = 'accounts'; render(); }
    else if (f.id === 'ml-account-form') { await call('mailing_account_add', x.get('label'), x.get('phone'), x.get('is_test') === 'on'); toast('Аккаунт добавлен. Нажмите «Войти»'); render(); }
    else if (f.dataset.mlLoginCode) { const r = await call('mailing_login_code', f.dataset.mlLoginCode, x.get('code')); if (r.status === 'password_needed') state.mailingLogin[f.dataset.mlLoginCode] = {step: 'password'}; else { delete state.mailingLogin[f.dataset.mlLoginCode]; toast('Вход выполнен'); } render(); }
    else if (f.dataset.mlLoginPassword) { await call('mailing_login_password', f.dataset.mlLoginPassword, x.get('password')); delete state.mailingLogin[f.dataset.mlLoginPassword]; toast('Вход выполнен'); render(); }
    else if (f.id === 'ml-new-form') { const c = await call('mailing_new', mailingCollect(f)); toast('Кампания создана в режиме сухого запуска'); state.mailingDraft.title = ''; mlGo({mailingTab: 'campaigns', mailingCampaign: c.id}); }
    else if (f.id === 'ml-live-form') { await call('mailing_go_live', state.mailingCampaign, x.get('confirm')); toast('Реальная отправка включена. Нажмите «Начать отправку»', true); render(); }
    else if (f.id === 'ml-stop-form') { await call('mailing_stop_add', x.get('kind'), x.get('value')); toast('Добавлено в стоп-лист'); render(); }
    else if (f.id === 'ml-policy-form') { const values = Object.fromEntries([...x.entries()].filter(([k]) => k !== 'weekdays_only').map(([k, v]) => [k, Number(v)])); values.weekdays_only = x.get('weekdays_only') === 'on'; await call('mailing_policy_save', values); toast('Правила сохранены'); render(); }
  });
}, true);

const mailingSetView = setView;
setView = function (view, extra = {}) {
  if (view === 'mailing' && extra.mailingCampaign === undefined) Object.assign(extra, {mailingCampaign: null, mailingTab: state.mailingTab === 'new' ? 'campaigns' : state.mailingTab});
  return mailingSetView(view, extra);
};
// app.js binds #refresh to the very first render(); later sections (scripts, mailing) were left
// on «Загрузка…». Bind it to the current render chain.
document.querySelector('#refresh').onclick = () => render();
