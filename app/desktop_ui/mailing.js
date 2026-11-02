// «Рассылка»: Telegram accounts, campaigns, statistics, stop-list. No automatic refresh:
// data is read on navigation and by the explicit «Обновить» buttons; form drafts live in state.
const ML = {
  account: {needs_login: ['Нужен вход', 'warn'], active: ['Активен', 'green'], paused: ['Пауза', ''], quarantine: ['Карантин', 'warn']},
  campaign: {draft: 'Черновик', running: 'Идёт отправка', paused: 'Пауза', stopped: 'Остановлена', done: 'Завершена'},
  recipient: {queued: 'В очереди', sending: 'Отправляется', sent: 'Отправлено', dry_sent: 'Сухой запуск', skipped: 'Пропущено', unreachable: 'Недоступен', failed: 'Ошибка'},
  site: {'': 'Любой', no_site: 'Без сайта', weak_site: 'Сайт на конструкторе', unavailable: 'Сайт недоступен', other: 'Обычный сайт'},
  stage: {first: 'первое сообщение', reminder: 'напоминание', repeat: 'повтор'},
  tabs: [['campaigns', 'Кампании'], ['tests', 'Тесты'], ['accounts', 'Аккаунты'], ['stoplist', 'Стоп-лист'], ['policy', 'Правила отправки'], ['journal', 'Журнал']],
};
state.mailingTab ||= 'campaigns';
state.mailingCampaign ||= null;
state.mailingDraft ||= {title: '', template: '', kind: 'normal', limit: 3, accounts: [], filters: {contact: 'username', site_type: '', status: '', city: '', niche: ''}};
state.mailingLogin ||= {};

const mailingSnapshot = snapshotNavigation;
snapshotNavigation = function () { return {...mailingSnapshot(), mailingTab: state.mailingTab, mailingCampaign: state.mailingCampaign, mailingFromLeads: state.mailingFromLeads}; };
const mailingSamePlace = samePlace;
samePlace = function (a, b) { return mailingSamePlace(a, b) && a.mailingTab === b.mailingTab && a.mailingCampaign === b.mailingCampaign; };
const mailingRender = render;
render = async function () {
  if (state.view !== 'mailing') return mailingRender();
  const node = document.querySelector('#content');
  try { await mailingPage(node); } catch (error) { setHeader('Рассылка', 'Раздел не открылся'); node.innerHTML = empty('Не удалось открыть раздел «Рассылка»', error.message); }
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
  if (state.mailingFromLeads) return mailingCompose(node, o);
  if (state.mailingTab === 'new') return mailingNew(node, o);
  const tabs = `<div class="tabs ml-tabs">${ML.tabs.map(([key, label]) => `<button type="button" class="tab ${state.mailingTab === key ? 'active' : ''}" data-ml-tab="${key}">${label}</button>`).join('')}</div>`;
  node.innerHTML = `${mlEmergency(o)}${tabs}<div id="ml-body">${empty('Загрузка…')}</div>`;
  const body = node.querySelector('#ml-body');
  if (state.mailingTab === 'accounts') return mailingAccounts(body, o);
  if (state.mailingTab === 'tests') return mailingTests(body, o);
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
  body.innerHTML = `<section class="panel"><div class="panel-head"><div><h2>Кампании</h2><p>Новая кампания всегда начинается с сухого запуска — без отправки.</p></div><div class="toolbar"><button type="button" class="btn" data-ml="to-leads">Пачка из «Лидов»</button><button type="button" class="btn primary" data-ml-tab="new">Новая кампания</button></div></div>
    <div class="inline-note">Быстрее всего: в «Лидах» задайте вкладку и фильтры и нажмите «Разослать этой пачке» — каждому лиду текст подставится сам, запуск одной кнопкой. Тайминг и количество — в «Настройках».</div>
    <div class="list">${rows.length ? rows.map(c => `<div class="row"><div class="row-head"><div><span class="row-title">${E(c.title)}</span><div class="small muted">${E(mlTime(c.created_at))} · ${c.kind === 'test' ? 'тестовая' : 'обычная'} · ${c.mode === 'live' ? 'реальная отправка' : 'сухой запуск'} · пачка №${E(c.batch_id)} · ${E(ML.stage[c.stage] || 'первое сообщение')}</div></div><div class="row-actions">${mlChip(ML.campaign[c.status] || c.status, c.status === 'running' ? 'green' : c.status === 'stopped' ? 'warn' : '')}<button type="button" class="btn" data-ml-campaign="${c.id}">Открыть</button></div></div><div class="chips">${mailingCounts(c)}</div>${c.stop_reason ? `<div class="inline-note ${c.status === 'stopped' ? 'error-note' : ''}">${E(c.stop_reason)}</div>` : ''}</div>`).join('') : empty('Кампаний пока нет', 'Откройте «Лиды» и нажмите «Разослать этой пачке»')}</div></section>`;
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
      <p class="small muted">Переменные: {название}, {город}, {ниша}, {тип_сайта}. Поля в [квадратных скобках] нужно заменить — с ними кампания не создаётся.</p>
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

// ------------------------------------------------ campaign from «Лиды» (redesign 02)
window.mailingFromLeads = function (filters) {
  const prev = state.mailingFromLeads || {};
  setView('mailing', {mailingCampaign: null, mailingTab: 'campaigns',
    mailingFromLeads: {filters: {...(filters || {})}, ids: null, title: prev.title || '', script: 'auto', accounts: prev.accounts || []}});
};

const ML_SEGMENTS = {new: 'Новые', remind: 'Напомнить', written: 'Написал', talking: 'В диалоге', all: 'Все'};
async function mailingBatchIds(filters, limit) {
  const ids = [];
  for (let offset = 0; ids.length < limit; ) {
    const page = await call('leads_list', {...filters, offset});
    page.items.forEach(x => ids.length < limit && ids.push(x.id));
    if (!page.more || !page.items.length) break;
    offset += page.items.length;
  }
  return ids;
}

async function mailingCompose(node, o) {
  const f = state.mailingFromLeads;
  const limit = o.policy.batch_limit || 100;
  if (!f.ids) f.ids = await mailingBatchIds(f.filters, limit);
  const scripts = await call('mailing_scripts', '');
  const usable = o.accounts.filter(a => a.status !== 'needs_login' && a.status !== 'quarantine' && !a.is_test);
  if (!f.accounts.length) f.accounts = usable.map(a => String(a.id));
  const fl = f.filters || {};
  const what = [ML_SEGMENTS[fl.segment] || 'Новые', fl.niche, fl.city, fl.site_type ? ML.site[fl.site_type] : '', fl.q ? `«${fl.q}»` : ''].filter(Boolean).join(' · ');
  setHeader('Рассылка по пачке лидов', `${what} · ${f.ids.length} ${f.ids.length === limit ? `(лимит пачки ${limit} из «Настроек»)` : 'лидов'}`);
  node.innerHTML = `${mlEmergency(o)}<form id="ml-compose-form" class="detail-stack"><section class="panel"><span class="section-kicker">ПАЧКА ИЗ «ЛИДОВ»</span><h2>${E(f.ids.length)} получателей · ${E(what)}</h2>
      <div class="form-grid"><label class="field">Название кампании<input name="title" maxlength="120" value="${E(f.title)}" placeholder="${E(what)}"></label>
      <label class="field">Скрипт<select name="script"><option value="auto" ${f.script === 'auto' ? 'selected' : ''}>Авто: под тип сайта лида</option>${scripts.items.map(s => `<option value="${E(s.slug)}" ${s.slug === f.script ? 'selected' : ''}>${E(s.title)}</option>`).join('')}</select></label></div>
      <p class="small muted">Каждому лиду текст подставится сам: название, город, ниша, тип сайта. Стоп-лист, «Не писать», дубли и лиды без username Telegram пропускаются.</p>
      <h3>С каких аккаунтов</h3><div class="checkboxes">${usable.length ? usable.map(a => `<label><input type="checkbox" name="accounts" value="${a.id}" ${f.accounts.includes(String(a.id)) ? 'checked' : ''}> ${E(a.label)} · сегодня ${E(a.sent_today)} из ${E(a.limit_today)}</label>`).join('') : '<span class="muted">Нет готовых аккаунтов — добавьте и войдите во вкладке «Аккаунты».</span>'}</div>
      <div class="ml-live"><div>Сообщения получат настоящие организации. Паузы, лимиты в день и в час и окно отправки — из «Настроек». Для запуска введите слово <code>${E(o.confirm_word)}</code>.</div><input name="confirm" autocomplete="off" aria-label="Слово подтверждения"></div>
      <div class="toolbar"><button class="btn primary" ${f.ids.length && usable.length ? '' : 'disabled'}>Запустить рассылку</button><button type="button" class="btn" data-ml="compose-review" ${f.ids.length && usable.length ? '' : 'disabled'}>Сначала посмотреть тексты</button><button type="button" class="ghost" data-ml="compose-cancel">Отмена</button></div></section></form>`;
}

function mailingComposeCollect(form) {
  const x = new FormData(form), f = state.mailingFromLeads;
  f.title = x.get('title') || ''; f.script = x.get('script') || 'auto'; f.accounts = x.getAll('accounts');
  const fl = f.filters || {};
  const title = f.title || ['Пачка', fl.niche, fl.city].filter(Boolean).join(' · ');
  return {title, ids: f.ids, template: f.script === 'auto' ? 'auto' : '', script: f.script, accounts: f.accounts.map(Number), confirm: x.get('confirm') || ''};
}

async function mailingComposeTemplate(payload) {
  if (payload.template) return payload.template;
  const scripts = await call('mailing_scripts', '');
  return (scripts.items.find(s => s.slug === payload.script) || {}).body || 'auto';
}

// «уйдёт через 1 ч 20 мин» to the planned moment (an estimate: pauses are taken at their middle).
function mlCountdown(iso) {
  const left = Math.round((new Date(iso).getTime() - Date.now()) / 60000);
  if (left <= 0) return 'уходит сейчас';
  const d = Math.floor(left / 1440), h = Math.floor((left % 1440) / 60), m = left % 60;
  return 'уйдёт через ' + [d ? `${d} дн` : '', h ? `${h} ч` : '', !d && m ? `${m} мин` : ''].filter(Boolean).join(' ');
}
// One chain of timeouts while a campaign is on screen (no intervals in the desktop UI).
let mlDueTimer = null;
function mlTickDue() {
  clearTimeout(mlDueTimer);
  const nodes = document.querySelectorAll('.ml-due[data-due]');
  if (state.view !== 'mailing' || !nodes.length) return;
  nodes.forEach(n => { n.textContent = mlCountdown(n.dataset.due); });
  mlDueTimer = setTimeout(mlTickDue, 30000);
}

// What happened to one business of the batch, in the owner's words.
function mlResult(r) {
  if (r.replied_at) return ['Ответил', 'green'];
  if (r.reason_kind === 'excluded') return ['Убран из пачки', ''];
  if (r.status === 'sent') return ['Отправлено', 'green'];
  if (r.status === 'dry_sent') return ['Сухой запуск', 'purple'];
  if (r.status === 'queued' || r.status === 'sending') return ['В очереди', ''];
  if (r.status === 'unreachable' || r.status === 'failed') return ['Не дошло', 'warn'];
  if (r.status === 'skipped' && r.reason_kind === 'stoplist') return ['В стоп-листе', 'warn'];
  return ['Пропущено', ''];
}
function mlResultCounts(rows) {
  const counts = {};
  rows.forEach(r => { const [label] = mlResult(r); counts[label] = (counts[label] || 0) + 1; });
  return Object.entries(counts).map(([label, n]) => mlChip(`${label}: ${n}`)).join('');
}

function mailingRecipient(c, r) {
  const editable = c.status !== 'running' && ['queued', 'dry_sent'].includes(r.status);
  const who = r.username ? (String(r.username).startsWith('+') || String(r.username).includes('•') ? E(r.username) : '@' + E(r.username)) : 'нет username';
  const text = editable
    ? `<div class="ml-edit" data-ml-rid="${r.id}"><textarea rows="5" aria-label="Текст для ${E(r.business_name)}">${E(r.message)}</textarea><div class="toolbar"><button type="button" class="btn" data-ml-msg-save="${r.id}">Сохранить текст</button>${r.edited ? `<button type="button" class="ghost" data-ml-msg-reset="${r.id}">Вернуть шаблон</button>` : ''}</div></div>`
    : `<details><summary>Текст</summary><pre class="ml-text">${E(r.message)}</pre></details>`;
  const [result, tone] = mlResult(r);
  const lead = (state.config?.statuses || {})[r.lead_status];
  const batchAction = c.kind === 'normal' || c.kind === 'test'
    ? (r.excluded ? `<button type="button" class="ghost" data-ml-include="${E(r.business_id)}">Вернуть в пачку</button>`
      : `<button type="button" class="ghost" data-ml-exclude="${E(r.business_id)}" title="Не писать ему в повторах этой пачки">Убрать из пачки</button>`) : '';
  return `<div class="row small ml-recipient"><div class="row-head"><div><span class="row-title">${E(r.business_name)}</span><div class="muted">${who}${r.msg_id ? ` · сообщение № ${E(r.msg_id)}` : ''}${r.sent_when ? ` · ${E(r.sent_when)}` : r.sent_at ? ` · ${E(mlTime(r.sent_at))}` : ''}${r.status === 'dry_sent' && r.reason && !r.due_at ? ` · ${E(r.reason)}` : ''}</div>${r.due_at ? `<div class="ml-due-line"><b class="ml-due" data-due="${E(r.due_at)}">${E(mlCountdown(r.due_at))}</b> · ${E(r.due_when || '')}${c.mode === 'live' ? '' : ' · если включить отправку сейчас'}</div>` : ''}</div><div class="row-actions"><div class="chips">${r.edited ? mlChip('Текст изменён', 'purple') : ''}${lead ? mlChip(`В «Лидах»: ${lead}`) : ''}${mlChip(result, tone)}</div>${batchAction}<button type="button" class="ghost" data-open="${E(r.business_id)}">Карточка</button></div></div>${r.reason && !['Отправлено'].includes(result) ? `<div class="muted">${E(r.reason)}</div>` : ''}${text}</div>`;
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
  const parent = c.parent_id ? (c.batch || []).find(x => x.id === c.parent_id) : null;
  const dryRepeat = Boolean(parent && parent.mode !== 'live');
  const goLive = !live && dryRepeat ? '<div class="inline-note">Это повтор сухого запуска: предыдущее сообщение никто не получил, поэтому реальная отправка для него закрыта.</div>'
    : !live ? `<form id="ml-live-form" class="ml-live"><div><strong>Реальная отправка выключена.</strong> Чтобы включить её осознанно, введите слово <code>${E(o.confirm_word)}</code>.${c.kind === 'normal' ? ' Сообщения получат настоящие организации.' : ' Сообщения получат только ваши тестовые адресаты.'}</div><input name="confirm" autocomplete="off" aria-label="Слово подтверждения" value="${E(state.mailingConfirm || '')}"><button class="danger">Включить реальную отправку</button></form>` : '';
  node.innerHTML = `${mlEmergency(o)}<section class="panel"><div class="panel-head"><div><span class="section-kicker">${E(ML.campaign[c.status] || c.status)}</span><h2>${E(c.title)}</h2><p>Создана ${E(mlTime(c.created_at))}${c.started_at ? ` · запуск ${E(mlTime(c.started_at))}` : ''}</p></div><div class="toolbar">${controls}</div></div>
      ${c.stop_reason ? `<div class="inline-note ${c.status === 'stopped' ? 'error-note' : ''}">${E(c.stop_reason)}</div>` : ''}${goLive}<div id="ml-stats">${mailingStats(c)}</div></section>
    ${mailingBatchPanel(c)}
    <section class="panel" id="ml-repeat">${empty('Готовлю повтор…')}</section>
    <section class="panel" id="ml-recipients"><div class="panel-head"><div><h2>Бизнесы пачки и результат</h2><p>${c.status === 'running' ? 'Кампания идёт — тексты можно менять на паузе.' : 'По каждому бизнесу видно, что произошло. Пока сообщение не ушло, текст можно поправить — он уйдёт именно таким.'}</p></div></div><div class="chips">${mlResultCounts(c.recipients)}</div><div class="list">${c.recipients.map(r => mailingRecipient(c, r)).join('')}</div></section>`;
  await mailingRepeatPanel(node.querySelector('#ml-repeat'), c, o);
  mlTickDue();
  if (state.mailingJustCreated === c.id) { state.mailingJustCreated = null; setHeader(c.title, 'Тексты подставлены. Поправьте любой при желании, затем сухой запуск или реальная отправка'); node.querySelector('#ml-recipients')?.scrollIntoView(); }
}

function mailingBatchPanel(c) {
  const items = c.batch || [];
  return `<section class="panel"><span class="section-kicker">ПАЧКА №${E(c.batch_id)}</span><h2>Что стало с пачкой</h2>
    <p class="small muted">Пачка — это первая рассылка и все её повторы. Результат хранится здесь: у каждого бизнеса видно «Отправлено», «Не дошло», «В стоп-листе», «Пропущено» или «Ответил». После реальной отправки лид в «Лидах» получает «Написал», через 3 дня он появится в напоминаниях «Сегодня».</p>
    <div class="list">${items.map(x => `<div class="row small"><div class="row-head"><div><span class="row-title">${x.id === c.id ? '▸ ' : ''}${E(x.title)}</span><div class="muted">${E(mlTime(x.created_at))} · ${E(ML.stage[x.stage] || 'первое сообщение')} · ${x.mode === 'live' ? 'реальная отправка' : 'сухой запуск'}</div></div><div class="row-actions"><div class="chips">${mailingCounts(x)}</div>${x.id === c.id ? '' : `<button type="button" class="btn" data-ml-campaign="${x.id}">Открыть</button>`}</div></div></div>`).join('')}</div></section>`;
}

async function mailingRepeatPanel(box, c, o) {
  if (!box) return;
  if (c.status === 'running' || !(c.counts.sent || c.counts.dry_sent)) {
    box.innerHTML = `<h2>Повторить пачку</h2><p class="muted">${c.status === 'running' ? 'Повтор можно сделать, когда кампания закончится или встанет на паузу.' : 'Повторять пока некому: никто не получил сообщение этой кампании.'}</p>`;
    return;
  }
  const [plan, scripts] = await Promise.all([call('mailing_repeat_preview', c.id), call('mailing_scripts', '')]);
  const r = state.mailingRepeat?.campaign === c.id ? state.mailingRepeat : (state.mailingRepeat = {campaign: c.id, template: 'auto:reminder', accounts: plan.accounts.map(String), confirm: ''});
  const usable = o.accounts.filter(a => a.status !== 'needs_login' && a.status !== 'quarantine' && (c.kind === 'test' ? a.is_test : !a.is_test));
  const reasons = {};
  plan.skip.forEach(x => { reasons[x.reason] = (reasons[x.reason] || 0) + 1; });
  const liveAllowed = c.mode === 'live';
  box.innerHTML = `<span class="section-kicker">ПОВТОР</span><h2>Повторить пачку другим сообщением</h2>
    <p>Получат <strong>${E(plan.include.length)}</strong> из ${E(plan.include.length + plan.skip.length)}. Не попадут: ${plan.skip.length ? Object.entries(reasons).map(([k, n]) => `${E(k)} — ${E(n)}`).join(' · ') : 'никто'}.</p>
    <p class="small muted">По умолчанию не попадают ответившие, убранные из пачки, стоп-лист, «Не писать» и лиды со статусом «Ответили», «В работе», «Договорились», «Отказ». Лимиты, паузы и окно отправки — как у любой кампании.</p>
    <form id="ml-repeat-form" class="ml-form"><div class="form-grid">
      <label class="field">Сообщение<select name="template"><option value="auto:reminder" ${r.template === 'auto:reminder' ? 'selected' : ''}>Напоминание — подставится каждому сам</option>${scripts.items.map(s => `<option value="${E(s.body)}" ${s.body === r.template ? 'selected' : ''}>${E(s.title)}</option>`).join('')}</select></label>
      <label class="field">Название<input name="title" maxlength="120" value="${E(r.title || '')}" placeholder="${E(c.title)} · напоминание"></label></div>
      <h3>С каких аккаунтов</h3><div class="checkboxes">${usable.length ? usable.map(a => `<label><input type="checkbox" name="accounts" value="${a.id}" ${r.accounts.includes(String(a.id)) ? 'checked' : ''}> ${E(a.label)}${a.is_test ? ' · тестовый' : ''} · сегодня ${E(a.sent_today)} из ${E(a.limit_today)}</label>`).join('') : '<span class="muted">Нет готовых аккаунтов</span>'}</div>
      ${liveAllowed ? `<div class="ml-live"><div>Для реальной отправки введите слово <code>${E(o.confirm_word)}</code>. ${c.kind === 'normal' ? 'Сообщения получат настоящие организации.' : 'Сообщения получат только ваши тестовые адресаты.'}</div><input name="confirm" autocomplete="off" aria-label="Слово подтверждения" value="${E(r.confirm || '')}"></div>` : '<p class="small muted">Эта кампания была сухим запуском — повтор тоже только сухой.</p>'}
      <div class="toolbar"><button type="button" class="btn" data-ml="repeat-dry" ${plan.include.length ? '' : 'disabled'}>Сначала сухой запуск повтора</button>${liveAllowed ? `<button type="button" class="btn primary" data-ml="repeat-live" ${plan.include.length && usable.length ? '' : 'disabled'}>Запустить повтор</button>` : ''}</div></form>
    ${plan.skip.length ? `<details><summary>Кто не попадёт в повтор (${E(plan.skip.length)})</summary><div class="list">${plan.skip.map(x => `<div class="row small"><span class="row-title">${E(x.name)}</span> <span class="muted">${E(x.reason)}</span></div>`).join('')}</div></details>` : ''}`;
}

function mailingRepeatCollect() {
  const form = document.querySelector('#ml-repeat-form');
  const x = new FormData(form), r = state.mailingRepeat;
  r.template = x.get('template') || 'auto:reminder'; r.title = x.get('title') || ''; r.accounts = x.getAll('accounts'); r.confirm = x.get('confirm') || '';
  return {campaign_id: r.campaign, template: r.template, title: r.title, accounts: r.accounts.map(Number), confirm: r.confirm};
}

function mailingStats(c) {
  const n = c.counts;
  const metrics = [['Отправлено', n.sent], ['Сухой запуск', n.dry_sent], ['В очереди', n.queued + n.sending], ['Пропущено по стоп-листу', n.stoplist], ['Недоступны', n.unreachable], ['Ошибки', n.failed], ['Ответили', n.replied], ['Всего', n.total]];
  return `<div class="metrics ml-metrics">${metrics.map(([label, value]) => metric(label, value)).join('')}</div>
    <div class="two-cols"><div><h3>Причины пропусков и ошибок</h3>${c.reasons.length ? `<table class="ml-table"><tbody>${c.reasons.map(r => `<tr><td>${E(ML.recipient[r.status] || r.status)}</td><td>${E(r.reason)}</td><td>${E(r.count)}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">Нет</p>'}</div>
    <div><h3>По аккаунтам</h3><table class="ml-table"><thead><tr><th>Аккаунт</th><th>Состояние</th><th>Отправлено</th><th>Осталось</th><th>Ошибки</th></tr></thead><tbody>${c.accounts.map(a => `<tr><td>${E(a.label)}${a.is_test ? ' · тест' : ''}</td><td>${E((ML.account[a.status] || [a.status])[0])}</td><td>${E(a.sent || 0)}</td><td>${E(a.left || 0)}</td><td>${E(a.errors || 0)}</td></tr>`).join('')}</tbody></table></div></div>
    <h3>События</h3>${c.events.length ? `<div class="list">${c.events.slice(0, 15).map(e => `<div class="row small"><span class="muted">${E(mlTime(e.at))}</span> ${E(e.message)}</div>`).join('')}</div>` : '<p class="muted">Событий нет</p>'}`;
}

// ------------------------------------------------------------ «Тесты»
// The same scheme as for businesses (window 10–19 Moscow time, pauses, limits) from the test account to the
// owner's own accounts, with texts of real leads. «Без ожидания» is only a quick delivery check.
async function mailingTests(body, o) {
  const rows = await call('mailing_tests');
  const testAccounts = o.accounts.filter(a => a.is_test);
  const ready = testAccounts.filter(a => a.status !== 'needs_login' && a.status !== 'quarantine');
  const t = state.mailingTest ||= {count: 2, no_wait: false, account: ready[0] ? String(ready[0].id) : ''};
  const campaigns = (await call('mailing_list')).filter(c => c.kind === 'test').slice(0, 8);
  body.innerHTML = `<div class="detail-stack"><section class="panel"><span class="section-kicker">1 · КОМУ</span><h2>Тестовые адресаты</h2>
      <p class="small muted">Ваши собственные аккаунты Telegram. Тестовые кампании пишут только им — настоящие бизнесы ничего не получат.</p>
      <div class="list">${rows.length ? rows.map(r => `<div class="row small"><div class="row-head"><span class="row-title">${E(r.shown)}</span><button type="button" class="ghost" data-ml-test-remove="${r.index}">Убрать</button></div></div>`).join('') : '<p class="muted">Пока никого. Добавьте username своего второго аккаунта.</p>'}</div>
      <form id="ml-test-add-form" class="ml-form"><div class="form-grid"><label class="field">Username или номер из контактов<input name="value" autocomplete="off" placeholder="@мой_второй_аккаунт"></label></div>
        <div class="toolbar"><button class="btn">Добавить</button><button type="button" class="ghost" data-ml="import-file">Загрузить из файла…</button></div></form></section>
    <section class="panel"><span class="section-kicker">2 · ОТКУДА</span><h2>Тестовый аккаунт</h2>
      ${testAccounts.length ? `<div class="checkboxes">${testAccounts.map(a => `<label><input type="radio" name="ml-test-account" value="${a.id}" ${String(a.id) === t.account ? 'checked' : ''} ${a.status === 'needs_login' || a.status === 'quarantine' ? 'disabled' : ''}> ${E(a.label)} · ${E(a.phone_mask)} · ${E((ML.account[a.status] || [a.status])[0])}</label>`).join('')}</div>`
        : '<p class="muted">Тестового аккаунта нет. Добавьте его во вкладке «Аккаунты» с галочкой «тестовый».</p>'}</section>
    <section class="panel"><span class="section-kicker">3 · ТЕСТ</span><h2>Запустить тест</h2>
      <p class="small muted">Берутся первые лиды из «Лидов» (текущая вкладка и фильтры): их настоящие тексты уйдут вашим тестовым адресатам. Окно отправки 10:00–19:00 по Москве, только будни, паузы и лимиты — как у бизнесов. Сначала будет сухой запуск: покажет, когда уйдёт каждое сообщение.</p>
      <form id="ml-test-run-form" class="ml-form"><div class="form-grid"><label class="field">Сколько сообщений (1–5)<input name="count" type="number" min="1" max="5" value="${E(t.count)}"></label></div>
        <label class="ml-no-wait"><input type="checkbox" name="no_wait" ${t.no_wait ? 'checked' : ''}> Без ожидания — только для быстрой проверки доставки: без окна 10–19 и пауз между сообщениями (дневные лимиты остаются)</label>
        <div class="toolbar"><button class="btn primary" ${rows.length && ready.length ? '' : 'disabled'}>Создать тест — сухой запуск</button></div></form>
      ${campaigns.length ? `<h3>Последние тесты</h3><div class="list">${campaigns.map(c => `<div class="row small"><div class="row-head"><div><span class="row-title">${E(c.title)}</span><div class="muted">${E(mlTime(c.created_at))} · ${c.mode === 'live' ? 'реальная отправка' : 'сухой запуск'} · ${E(ML.campaign[c.status] || c.status)}</div></div><button type="button" class="btn" data-ml-campaign="${c.id}">Открыть</button></div></div>`).join('')}</div>` : ''}</section></div>`;
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
    <form id="ml-policy-form"><div class="form-grid">${field('first_day_limit', 'Лимит в первый день', 1, 50)}${field('daily_increment', 'Прирост лимита в день', 0, 10)}${field('daily_cap', 'Потолок в день', 1, 50)}${field('hourly_cap', 'Не больше в час', 1, 30)}${field('delay_min', 'Пауза от, с', 45, 3600)}${field('delay_max', 'Пауза до, с', 45, 7200)}${field('window_start', 'Окно с (час получателя)', 0, 23)}${field('window_end', 'Окно до (час)', 1, 24)}${field('flood_wait_max', 'Ждать FloodWait не дольше, с', 30, 3600)}${field('batch_limit', 'Лидов в одной пачке', 1, 500)}</div>
    <label class="ml-check"><input type="checkbox" name="weekdays_only" ${p.weekdays_only ? 'checked' : ''}> Только будни</label>
    <div class="inline-note">При ограничении аккаунта (PeerFlood, бан, заморозка) он уходит в карантин, его кампания останавливается. Трафик на другие аккаунты не переносится.</div>
    <div class="toolbar"><button class="btn primary">Сохранить правила</button></div></form></section>`;
}

function mailingJournal(body, o) {
  body.innerHTML = `<section class="panel"><div class="panel-head"><div><h2>Журнал</h2><p>Последние события модуля. Номера и ключи в журнал не попадают.</p></div><button type="button" class="btn" data-ml-tab="journal">Обновить</button></div><div class="list">${o.events.length ? o.events.map(e => `<div class="row small"><span class="chip ${e.level === 'error' ? 'warn' : ''}">${E(e.kind)}</span> <span class="muted">${E(mlTime(e.at))}</span> ${E(e.message)}</div>`).join('') : empty('Событий нет')}</div></section>`;
}

// «Настройки» → блок «Рассылка: тайминг и количество». Self-contained: renders into any node and
// saves by itself (the Settings screen mounts it; safety bounds are enforced by Policy in Python).
async function mailingSettingsBlock(node) {
  const o = await call('mailing_overview');
  const p = o.policy;
  const field = (name, label, min, max, hint = '') => `<label class="field">${label}<input name="${name}" type="number" min="${min}" max="${max}" value="${E(p[name])}">${hint ? `<span class="small muted">${hint}</span>` : ''}</label>`;
  node.innerHTML = `<section class="panel ml-settings"><h2>Рассылка: тайминг и количество</h2><p class="small muted">Границы жёсткие: Telegram ограничивает аккаунты, которые пишут слишком часто.</p>
    <form class="ml-settings-form"><h3>Тайминг</h3><div class="form-grid">${field('delay_min', 'Пауза между сообщениями от, с', 45, 3600)}${field('delay_max', 'Пауза до, с', 45, 7200)}${field('window_start', 'Писать с (час получателя)', 0, 23)}${field('window_end', 'Писать до (час)', 1, 24)}</div>
    <label class="ml-check"><input type="checkbox" name="weekdays_only" ${p.weekdays_only ? 'checked' : ''}> Только будни</label>
    <h3>Количество</h3><div class="form-grid">${field('batch_limit', 'Лидов в одной пачке', 1, 500)}${field('daily_cap', 'Сообщений в день с аккаунта, не больше', 1, 50)}${field('hourly_cap', 'В час, не больше', 1, 30)}${field('first_day_limit', 'Новый аккаунт: в первый день', 1, 50)}${field('daily_increment', 'Прирост лимита в день', 0, 10)}</div>
    <div class="toolbar"><button class="btn primary">Сохранить</button><span class="small muted" data-ml-settings-note></span></div></form></section>`;
  const form = node.querySelector('.ml-settings-form');
  form.addEventListener('submit', event => {
    event.preventDefault(); event.stopPropagation();
    safe(async () => {
      const x = new FormData(form);
      const values = Object.fromEntries([...x.entries()].filter(([k]) => k !== 'weekdays_only').map(([k, v]) => [k, Number(v)]));
      values.weekdays_only = x.get('weekdays_only') === 'on';
      await call('mailing_policy_save', values);
      form.querySelector('[data-ml-settings-note]').textContent = 'Сохранено';
      toast('Настройки рассылки сохранены');
    });
  });
}
window.mailingSettingsBlock = mailingSettingsBlock;

// ---------------------------------------------------------------- handlers
document.addEventListener('input', event => {
  const form = event.target.closest?.('#ml-new-form');
  if (form) mailingCollect(form);
  const compose = event.target.closest?.('#ml-compose-form');
  if (compose) mailingComposeCollect(compose);
  if (event.target.closest?.('#ml-live-form')) state.mailingConfirm = event.target.value;
  if (event.target.closest?.('#ml-repeat-form')) mailingRepeatCollect();
});
document.addEventListener('change', event => {
  const form = event.target.closest?.('#ml-new-form');
  if (form) mailingCollect(form);
  const compose = event.target.closest?.('#ml-compose-form');
  if (compose) mailingComposeCollect(compose);
});

document.addEventListener('click', event => {
  const b = event.target.closest('button');
  if (!b || state.view !== 'mailing') return;
  const d = b.dataset;
  if (!(d.ml || d.mlTab || d.mlCampaign || d.mlTestRemove !== undefined || d.mlLogin || d.mlLoginCancel || d.mlAccountStatus || d.mlLogout || d.mlStopRemove || d.mlMsgSave || d.mlMsgReset || d.mlExclude || d.mlInclude)) return;
  event.preventDefault(); event.stopImmediatePropagation();
  safe(async () => {
    if (d.mlTab) { const form = document.querySelector('#ml-new-form'); if (form) mailingCollect(form); mlGo({mailingTab: d.mlTab, mailingCampaign: null}); return; }
    if (d.mlCampaign) { mlGo({mailingCampaign: Number(d.mlCampaign)}); return; }
    if (d.mlTestRemove !== undefined) { await call('mailing_test_remove', Number(d.mlTestRemove)); toast('Тестовый адресат убран'); render(); return; }
    if (d.mlLogin) { await call('mailing_login_request', d.mlLogin); state.mailingLogin[d.mlLogin] = {step: 'code'}; toast('Код отправлен в Telegram на этот номер'); render(); return; }
    if (d.mlLoginCancel) { await call('mailing_login_cancel', d.mlLoginCancel); delete state.mailingLogin[d.mlLoginCancel]; render(); return; }
    if (d.mlAccountStatus) { if (d.confirm && !confirm(d.confirm)) return; await call('mailing_account_status', d.mlAccountStatus, d.status); render(); return; }
    if (d.mlLogout) { if (!confirm('Удалить сессию этого аккаунта с компьютера? Для отправки снова понадобится вход.')) return; await call('mailing_account_logout', d.mlLogout); render(); return; }
    if (d.mlMsgSave) { const text = document.querySelector(`[data-ml-rid="${d.mlMsgSave}"] textarea`).value; await call('mailing_message_set', state.mailingCampaign, d.mlMsgSave, text); toast('Текст сохранён для этого получателя'); render(); return; }
    if (d.mlMsgReset) { await call('mailing_message_reset', state.mailingCampaign, d.mlMsgReset); toast('Текст снова по шаблону'); render(); return; }
    if (d.mlStopRemove) { await call('mailing_stop_remove', d.mlStopRemove); render(); return; }
    if (d.mlExclude) { const r = await call('mailing_exclude', state.mailingCampaign, d.mlExclude); toast(r.withdrawn ? 'Убран из пачки, его сообщение снято с очереди' : 'Убран из пачки — в повторы не попадёт'); render(); return; }
    if (d.mlInclude) { await call('mailing_include', state.mailingCampaign, d.mlInclude); toast('Возвращён в пачку для следующих повторов'); render(); return; }
    const id = state.mailingCampaign;
    switch (d.ml) {
      case 'emergency': await call('mailing_emergency'); toast('Аварийный стоп: все отправки остановлены', true); render(); break;
      case 'emergency-clear': if (confirm('Снять аварийный стоп? Кампании останутся на паузе, их нужно продолжить вручную.')) { await call('mailing_emergency_clear'); render(); } break;
      case 'import-file': { const r = await call('mailing_import_file'); if (r) toast(`Ключи: ${r.keys ? 'сохранены' : 'нет в файле'} · аккаунтов добавлено ${r.accounts_added} · тестовых адресатов ${r.test_recipients}`); render(); break; }
      case 'to-leads': setView('leads'); toast('Задайте вкладку и фильтры и нажмите «Разослать этой пачке»'); break;
      case 'compose-cancel': mlGo({mailingFromLeads: null}); break;
      case 'compose-review': { const payload = mailingComposeCollect(document.querySelector('#ml-compose-form')); payload.template = await mailingComposeTemplate(payload); const c = await call('mailing_new', {title: payload.title, template: payload.template, kind: 'normal', accounts: payload.accounts, filters: {ids: payload.ids}}); toast('Черновик создан — ничего не отправлено'); state.mailingJustCreated = c.id; mlGo({mailingFromLeads: null, mailingTab: 'campaigns', mailingCampaign: c.id}); break; }
      case 'open-apps': await call('open_external', 'https://my.telegram.org/apps'); break;
      case 'use-script': { const form = document.querySelector('#ml-new-form'); const slug = document.querySelector('#ml-script').value; const scripts = await call('mailing_scripts', state.mailingDraft.filters.site_type || ''); const item = scripts.items.find(s => s.slug === slug); if (item) { form.querySelector('[name="template"]').value = item.body; mailingCollect(form); toast('Подсказка подставлена. Замените поля в [скобках]'); } break; }
      case 'preview': { const form = document.querySelector('#ml-new-form'); const payload = mailingCollect(form); const p = await call('mailing_preview', payload); document.querySelector('#ml-preview').innerHTML = `<div class="inline-note">Подходит организаций: ${E(p.total)}, с username: ${E(p.with_username)}${p.unfilled.length ? ` · <strong>замените: ${E(p.unfilled.join(', '))}</strong>` : ''}</div>${p.items.length ? p.items.map(x => `<div class="row"><span class="row-title">${E(x.name)}</span> <span class="muted">${E(x.city)} · ${E(ML.site[x.site_type] || x.site_type)} · ${x.username ? '@' + E(x.username) : 'нет username'}</span><pre class="ml-text">${E(x.text)}</pre></div>`).join('') : empty('Нет записей для предпросмотра')}`; break; }
      case 'dry': await call('mailing_run', id); toast('Сухой запуск завершён — ничего не отправлено'); render(); break;
      case 'run': if (!confirm('Начать реальную отправку?')) return; await call('mailing_run', id); toast('Отправка запущена'); render(); break;
      case 'pause': await call('mailing_pause', id); toast('Кампания на паузе'); render(); break;
      case 'refresh-campaign': await render(); toast('Статистика обновлена'); break;
      case 'repeat-dry': { const c = await call('mailing_repeat', {...mailingRepeatCollect(), live: false}); state.mailingRepeat = null; toast('Повтор: сухой запуск готов — ничего не отправлено'); state.mailingJustCreated = c.id; mlGo({mailingCampaign: c.id}); break; }
      case 'repeat-live': { const payload = mailingRepeatCollect(); const plan = await call('mailing_repeat_preview', payload.campaign_id); if (!confirm(`Отправить повтор ${plan.include.length} получателям?\n\n${plan.include.slice(0, 15).map(x => '• ' + x.name).join('\n')}${plan.include.length > 15 ? '\n…' : ''}`)) return; const c = await call('mailing_repeat', {...payload, live: true}); state.mailingRepeat = null; toast('Повтор запущен', true); mlGo({mailingCampaign: c.id}); break; }
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
    else if (f.id === 'ml-compose-form') { const payload = mailingComposeCollect(f); payload.template = await mailingComposeTemplate(payload); if (!confirm(`Запустить рассылку на ${payload.ids.length} лидов?`)) return; const c = await call('mailing_launch', payload); toast('Рассылка запущена', true); mlGo({mailingFromLeads: null, mailingTab: 'campaigns', mailingCampaign: c.id}); }
    else if (f.id === 'ml-new-form') { const c = await call('mailing_new', mailingCollect(f)); toast('Кампания создана в режиме сухого запуска'); state.mailingDraft.title = ''; mlGo({mailingTab: 'campaigns', mailingCampaign: c.id}); }
    else if (f.id === 'ml-live-form') { await call('mailing_go_live', state.mailingCampaign, x.get('confirm')); state.mailingConfirm = ''; toast('Реальная отправка включена. Нажмите «Начать отправку»', true); render(); }
    else if (f.id === 'ml-test-add-form') { const r = await call('mailing_test_add', x.get('value')); toast(r.added ? 'Тестовый адресат добавлен' : 'Этот адресат уже в списке'); render(); }
    else if (f.id === 'ml-test-run-form') {
      const t = state.mailingTest; t.count = Math.min(5, Math.max(1, Number(x.get('count') || 2))); t.no_wait = x.get('no_wait') === 'on';
      t.account = document.querySelector('[name=ml-test-account]:checked')?.value || t.account;
      if (!t.account) { toast('Выберите тестовый аккаунт', true); return; }
      const leads = JSON.parse(localStorage.getItem('lem-leads') || '{}');
      const filters = {segment: leads.segment || 'new', q: leads.q || '', niche: leads.niche || '', city: leads.city || '', run: leads.run || '', site_type: leads.site_type || ''};
      const ids = await mailingBatchIds(filters, t.count);
      if (!ids.length) { toast('В «Лидах» нет лидов для теста', true); return; }
      const c = await call('mailing_new', {title: `Тест · ${ids.length} ${ids.length === 1 ? 'сообщение' : 'сообщения'}${t.no_wait ? ' · без ожидания' : ''}`, filters: {ids}, template: 'auto',
        accounts: [Number(t.account)], kind: 'test', limit: ids.length, no_wait: t.no_wait});
      await call('mailing_run', c.id);
      toast('Тест создан, сухой запуск готов — ничего не отправлено');
      mlGo({mailingTab: 'campaigns', mailingCampaign: c.id});
    }
    else if (f.id === 'ml-stop-form') { await call('mailing_stop_add', x.get('kind'), x.get('value')); toast('Добавлено в стоп-лист'); render(); }
    else if (f.id === 'ml-policy-form') { const values = Object.fromEntries([...x.entries()].filter(([k]) => k !== 'weekdays_only').map(([k, v]) => [k, Number(v)])); values.weekdays_only = x.get('weekdays_only') === 'on'; await call('mailing_policy_save', values); toast('Правила сохранены'); render(); }
  });
}, true);

const mailingSetView = setView;
setView = function (view, extra = {}) {
  if (view === 'mailing' && extra.mailingCampaign === undefined) Object.assign(extra, {mailingCampaign: null, mailingFromLeads: null, mailingTab: state.mailingTab === 'new' ? 'campaigns' : state.mailingTab});
  return mailingSetView(view, extra);
};
// app.js binds #refresh to the very first render(); later sections (scripts, mailing) were left
// on «Загрузка…». Bind it to the current render chain.
document.querySelector('#refresh').onclick = () => render();
