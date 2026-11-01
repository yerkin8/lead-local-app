// «Настройки»: оформление, ключи, поиск по умолчанию, импорт. docs/unify/redesign/05-settings.md
(function () {
  const THEMES = [['light', 'Светлая'], ['dark', 'Тёмная'], ['system', 'Как в системе']];
  const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function applyTheme(pref) {
    const root = document.documentElement;
    root.dataset.themePref = pref;
    root.dataset.theme = pref === 'system' ? (media && media.matches ? 'dark' : 'light') : pref;
    try { localStorage.setItem('lem-theme', pref); } catch (_) { /* storage off: the page address still carries it */ }
  }
  if (media && media.addEventListener) {
    media.addEventListener('change', () => { if (document.documentElement.dataset.themePref === 'system') applyTheme('system'); });
  }

  // Saved preferences: theme (also applied before paint from ?theme=) and the default reviews for search.
  LEM.onReady(() => safe(async () => {
    state.prefs = await call('ui_settings');
    if (state.prefs.theme !== document.documentElement.dataset.themePref) applyTheme(state.prefs.theme);
  }));

  async function telegramKeys() {
    if (LEM.kind !== 'desktop') return '';
    let o = null;
    try { o = await call('mailing_overview'); } catch (_) { o = null; }
    const status = !o ? 'модуль рассылки недоступен' : o.keys_configured ? `сохранены (api_id ${E(o.api_id)})` : 'не заданы';
    return `<section class="panel" id="tg-keys-panel"><h2>Telegram API (рассылка)</h2>
      <p>Ключи api_id и api_hash: <strong>${status}</strong>.</p>
      ${o ? `<form id="settings-tg-form" class="ml-form"><div class="form-grid">
        <label class="field">api_id<input name="api_id" inputmode="numeric" autocomplete="off" required placeholder="число"></label>
        <label class="field">api_hash<input name="api_hash" type="password" autocomplete="off" required placeholder="32 символа"></label></div>
        <div class="toolbar"><button class="btn primary">${o.keys_configured ? 'Заменить ключи' : 'Сохранить ключи'}</button>
        ${o.keys_configured ? '<button type="button" class="ghost" data-tg-keys-clear>Удалить ключи</button>' : ''}</div></form>` : ''}
      <p class="muted small">Ключи берутся на my.telegram.org, шифруются для текущего пользователя Windows и не показываются целиком.</p></section>`;
  }

  settings = async function (node) {
    setHeader('Настройки', 'Оформление, ключи и импорт');
    const pref = document.documentElement.dataset.themePref || 'light';
    const prefs = state.prefs || await safe(() => call('ui_settings')) || { review_limit: 20 };
    const desktop = LEM.kind === 'desktop';
    node.innerHTML = `<div class="settings-page">
      <section class="panel"><h2>Оформление</h2>
        <div class="tabs settings-theme" role="group" aria-label="Тема">${THEMES.map(([key, label]) =>
          `<button type="button" class="tab ${key === pref ? 'active' : ''}" aria-pressed="${key === pref}" data-theme-set="${key}">${label}</button>`).join('')}</div></section>
      <div class="settings-group" id="settings-keys"><h2 class="settings-group-title">Ключи</h2>${await telegramKeys()}</div>
      <div class="settings-group" id="settings-work"><h2 class="settings-group-title">Работа</h2>
        <section class="panel"><h2>Поиск</h2><form id="settings-search-form" class="form-grid">
          <label class="field">Отзывов на организацию по умолчанию<input name="review_limit" type="number" min="0" max="100" value="${E(prefs.review_limit)}"><small class="muted">0 — не собирать; максимум 100</small></label>
          <div><button class="btn primary">Сохранить</button></div></form></section></div>
      <div class="settings-group" id="settings-mailing-group"><h2 class="settings-group-title">Рассылка</h2><div id="settings-mailing"></div></div>
      <section class="panel" id="settings-data"><h2>Данные</h2>
        <div class="toolbar"><button class="btn" data-action="import">Импорт базы (XLSX)</button>${desktop ? '<button class="btn" data-import-sqlite>Импорт SQLite</button><button class="ghost" data-open-data-dir>Открыть папку с данными</button>' : ''}</div></section>
    </div>`;
  };

  // ai.js and today.js append their panels after the page; put them into their groups.
  const baseRender = render;
  render = async function () {
    const result = await baseRender.apply(this, arguments);
    if (state.view === 'settings') {
      const node = document.querySelector('#content');
      const ai = node.querySelector('#ai-key-panel');
      if (ai) { ai.querySelector('h2').textContent = 'OpenRouter (ИИ-чат)'; node.querySelector('#settings-keys h2')?.after(ai); }
      const outreach = node.querySelector('#settings-outreach');
      if (outreach) node.querySelector('#settings-work')?.append(outreach);
      const mount = node.querySelector('#settings-mailing');
      if (mount && typeof mailingSettingsBlock === 'function') { try { await mailingSettingsBlock(mount); } catch (_) { /* rest of settings stays usable */ } }
      else node.querySelector('#settings-mailing-group')?.remove();
      const data = node.querySelector('#settings-data');
      if (data) node.querySelector('.settings-page')?.append(data);
    }
    return result;
  };
  window.render = render;

  document.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.themeSet) {
      e.stopImmediatePropagation();
      const theme = b.dataset.themeSet;
      applyTheme(theme);
      document.querySelectorAll('[data-theme-set]').forEach((x) => {
        x.classList.toggle('active', x === b); x.setAttribute('aria-pressed', String(x === b));
      });
      safe(async () => { state.prefs = await call('ui_settings_save', { theme }); });
    } else if (b.hasAttribute('data-tg-keys-clear')) {
      e.stopImmediatePropagation();
      safe(async () => {
        if (!await LEM.platform.confirm('Удалить ключи Telegram API? Рассылка остановится, пока вы не введёте их снова.')) return;
        await call('mailing_clear_keys'); toast('Ключи Telegram удалены'); render();
      });
    } else if (b.hasAttribute('data-open-data-dir')) {
      e.stopImmediatePropagation();
      safe(() => call('open_data_dir'));
    }
  }, true);

  document.addEventListener('submit', (e) => {
    const f = e.target;
    if (f.id !== 'settings-tg-form' && f.id !== 'settings-search-form') return;
    e.preventDefault(); e.stopImmediatePropagation();
    const x = new FormData(f);
    if (f.id === 'settings-tg-form') {
      f.reset();  // the values leave the page right away
      safe(async () => { await call('mailing_save_keys', x.get('api_id'), x.get('api_hash')); toast('Ключи Telegram сохранены и зашифрованы'); render(); });
    } else {
      safe(async () => { state.prefs = await call('ui_settings_save', { review_limit: Number(x.get('review_limit')) }); toast('Настройки сохранены'); });
    }
  }, true);
})();
