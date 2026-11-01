// «ИИ-чат»: a free OpenRouter model writes a message for the chosen type and business and refines it on request.
// The conversation lives in this page's memory only and is sent whole on every turn; nothing is stored on the server.
// A reply takes 30–90 s, so ai_chat_start answers at once with an id and the page polls ai_chat_result.
// Layout follows the owner's chat-card reference (docs/unify/redesign/04-ai-chat.md), ported to vanilla JS/CSS.
(function () {
  const AI = {
    status: null, category: 'general', lead: null, messages: [], draft: '',
    pending: null, error: '', saved: new Set(),
  };
  const POLL_MS = 2000;
  const MAX_POLL_FAILURES = 5;
  const QUICK = [['Короче', 'Сделай короче'], ['Другой вариант', 'Напиши другой вариант'], ['Начни с …', 'Начни с ']];
  const EXAMPLES = [
    'Первое сообщение автосервису без сайта',
    'Человек ответил «дорого». Что написать?',
    'Ответ ресторану: предложить сайт с бронью и меню',
    'Напомнить о себе через неделю, без давления',
  ];
  const { plain, composerKey, autosize, typeStep, typeText, greeting } = window.LEM_AI_CORE;
  let searchTimer = null;
  let tickTimer = null;

  const isOpen = () => state.view === 'ai' && document.querySelector('#ai-thread');
  const categoryLabel = (key) => (AI.status?.categories || []).find((x) => x.key === key)?.label || 'Общие';

  // Desktop: every user enters their own key; it is encrypted for this Windows user and never comes back to the page.
  const keyForm = () => `<form class="form-grid ai-key-form" data-ai-key-form autocomplete="off">
      <label class="field wide">Ключ OpenRouter<input name="key" type="password" autocomplete="off" spellcheck="false" placeholder="sk-or-v1-…" required></label>
      <div class="toolbar"><button class="btn primary">Сохранить ключ</button></div></form>`;
  const keyNote = '<p class="muted small">Ключ шифруется для текущего пользователя Windows и хранится только на этом компьютере. В чат уходят публичные данные карточки без телефонов; бесплатные модели OpenRouter могут учиться на запросах.</p>';
  async function settingsKeyBlock(node) {
    const s = await call('ai_status');
    const where = s.key_source === 'env' ? 'задан переменной окружения OPENROUTER_API_KEY' : s.configured ? 'сохранён' : 'не задан';
    node.insertAdjacentHTML('beforeend', `<section class="panel" id="ai-key-panel"><h2>ИИ-чат</h2>
      <p>Ключ OpenRouter: <strong>${E(where)}</strong>.</p>
      <p class="muted small">Ключ можно создать на <a href="https://openrouter.ai/settings/keys">openrouter.ai/settings/keys</a>; подойдут бесплатные модели. Перезапуск не нужен.</p>${keyForm()}
      ${s.key_source === 'saved' ? '<div class="toolbar"><button type="button" class="ghost" data-ai-key-clear>Удалить ключ</button></div>' : ''}${keyNote}</section>`);
  }
  document.addEventListener('submit', (e) => {
    if (!e.target.matches('[data-ai-key-form]')) return;
    e.preventDefault(); e.stopImmediatePropagation();
    const input = e.target.querySelector('[name=key]');
    const key = input.value;
    input.value = '';  // the key leaves the page right away
    safe(async () => { AI.status = await call('ai_key_save', key); toast('Ключ сохранён'); render(); });
  }, true);
  document.addEventListener('click', (e) => {
    if (!e.target.closest('[data-ai-key-clear]')) return;
    safe(async () => {
      if (!await LEM.platform.confirm('Удалить сохранённый ключ OpenRouter? ИИ-чат перестанет работать, пока вы не введёте ключ снова.')) return;
      AI.status = await call('ai_key_clear'); toast('Ключ удалён'); render();
    });
  });

  // Inline icons drawn like lucide (refresh-cw, message-circle-dashed, plus, arrow-up, …): no icon font, works offline.
  const ICON = {
    refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    dashed: '<path d="M13.5 3.1c-.5 0-1-.1-1.5-.1s-1 .1-1.5.1"/><path d="M19.3 6.8a10.45 10.45 0 0 0-2.1-2.1"/><path d="M20.9 13.5c.1-.5.1-1 .1-1.5s-.1-1-.1-1.5"/><path d="M17.2 19.3a10.45 10.45 0 0 0 2.1-2.1"/><path d="M10.5 20.9c.5.1 1 .1 1.5.1s1-.1 1.5-.1"/><path d="M3.5 17.5 2 22l4.5-1.5"/><path d="M3.1 10.5c0 .5-.1 1-.1 1.5s.1 1 .1 1.5"/><path d="M6.8 4.7a10.45 10.45 0 0 0-2.1 2.1"/>',
    plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
    up: '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    save: '<path d="M19 21l-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>',
    tag: '<path d="M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4z"/><circle cx="7.5" cy="7.5" r=".5"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  };
  const icon = (name, size = 16) => `<svg class="ai-ico" aria-hidden="true" focusable="false" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON[name]}</svg>`;
  const reducedMotion = () => Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);

  // The two selectors live in the composer row: type of message (native select styled as a chip) and business (searchable).
  function contextHtml() {
    const cats = (AI.status?.categories || []).map((x) => `<option value="${E(x.key)}" ${x.key === AI.category ? 'selected' : ''}>${E(x.label)}</option>`).join('');
    const lead = AI.lead
      ? `<span class="ai-chip is-set">${icon('plus', 14)}<span class="ai-chip-text">${E(AI.lead.name)}</span>
          <button type="button" class="ai-chip-x" data-ai-lead-clear aria-label="Убрать бизнес">${icon('x', 12)}</button></span>`
      : `<button type="button" class="ai-chip" data-ai-lead-open aria-expanded="false" aria-controls="ai-lead-pop">${icon('plus', 14)}<span class="ai-chip-text">Бизнес</span></button>`;
    return `<label class="ai-chip ai-chip-select" title="Тип сообщения">${icon('tag', 14)}
        <select id="ai-category" aria-label="Тип сообщения">${cats}</select></label>${lead}
      <div class="ai-pop" id="ai-lead-pop" hidden>
        <input id="ai-lead-search" type="search" autocomplete="off" placeholder="Найти бизнес по названию" aria-label="Найти бизнес">
        <div id="ai-lead-results" class="ai-lead-results"><p class="ai-muted">Начните вводить название</p></div>
      </div>`;
  }

  function messageHtml(message, index) {
    if (message.role === 'user') return `<div class="ai-row user"><div class="ai-bubble user">${E(message.content)}</div></div>`;
    const last = index === AI.messages.length - 1;
    const saved = AI.saved.has(index);
    return `<div class="ai-row bot"><div class="ai-bot-wrap">
      <div class="ai-bubble bot">${E(plain(message.content))}</div>
      <div class="ai-msg-actions">
        <button type="button" class="ai-act" data-ai-copy="${index}">${icon('copy', 14)}<span>Копировать</span></button>
        <button type="button" class="ai-act" data-ai-save="${index}" ${saved ? 'disabled' : ''}>${icon('save', 14)}<span>${saved ? 'Сохранено' : 'В скрипты'}</span></button>
        <span class="ai-meta">${E(message.model || 'ИИ')}${message.seconds ? ` · ${E(message.seconds)} с` : ''}</span>
      </div>
      ${last && !AI.pending ? `<div class="ai-quick">${QUICK.map(([label, text]) => `<button type="button" class="ai-quick-btn" data-ai-quick="${E(text)}">${E(label)}</button>`).join('')}</div>` : ''}
    </div></div>`;
  }

  const pendingLabel = () => `печатает… ${Math.round((Date.now() - AI.pending.started) / 1000)} с`;

  function threadHtml() {
    if (!AI.status?.configured) {
      return `<div class="ai-empty"><div class="ai-empty-icon">${icon('dashed', 20)}</div>
        <h3 class="ai-greet">Нужен ключ OpenRouter</h3>
        <p class="ai-hint">Вставьте свой ключ в «Настройках» — и чат заработает. Подойдут бесплатные модели.</p>
        <button type="button" class="btn primary" data-ai-settings>Открыть настройки</button></div>`;
    }
    const parts = AI.messages.map(messageHtml);
    if (AI.pending) {
      parts.push(`<div class="ai-row bot"><div class="ai-bubble bot ai-typing"><span class="ai-dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="ai-meta" data-ai-pending>${pendingLabel()}</span></div></div>`);
    }
    if (AI.error) parts.push(`<div class="ai-row bot"><div class="ai-bubble error" role="alert">${E(AI.error)}</div></div>`);
    if (!parts.length) {
      return `<div class="ai-empty"><div class="ai-empty-icon ai-float">${icon('dashed', 20)}</div>
        <h3 class="ai-greet ai-fade">${E(greeting(new Date().getHours()))}!</h3>
        <p class="ai-hint ai-fade ai-fade-2">Выберите тип сообщения и бизнес внизу и напишите задачу. Ответ можно уточнять.</p></div>`;
    }
    return parts.join('');
  }

  async function aiPage(node) {
    setHeader('ИИ-чат', 'Бесплатная модель OpenRouter пишет сообщение под тип и бизнес. Удачное сохраните в «Скрипты».');
    stopTyper();
    AI.status = await call('ai_status');
    const on = Boolean(AI.status.configured);
    node.innerHTML = `<div class="ai-chat"><section class="ai-card" aria-label="ИИ-чат">
      <header class="ai-head"><div><h2 class="ai-title">ИИ-чат</h2><p class="ai-sub">Чем помочь сегодня?</p></div>
        <button type="button" class="ai-round" data-ai-new aria-label="Новый чат" title="Новый чат">${icon('refresh', 16)}</button></header>
      <div class="ai-thread" id="ai-thread" aria-live="polite">${threadHtml()}</div>
      <form id="ai-form" class="ai-compose-wrap">
        <div class="ai-composer${on ? '' : ' is-off'}">
          <div class="ai-input-wrap">
            <textarea id="ai-input" maxlength="2000" rows="2" placeholder="Напишите задачу…" aria-label="Сообщение для ИИ" ${on ? '' : 'disabled'}>${E(AI.draft)}</textarea>
            <div class="ai-typer" id="ai-typer" aria-hidden="true" hidden><span></span><i class="ai-caret"></i></div>
          </div>
          <div class="ai-compose-row"><div class="ai-context">${on ? contextHtml() : ''}</div>
            <button class="ai-send" id="ai-send" aria-label="Отправить" title="Отправить (Enter)" ${AI.pending || !on ? 'disabled' : ''}>${icon('up', 16)}</button></div>
        </div>
        <p class="ai-note">Enter — отправить, Shift+Enter — новая строка. Уходят тип, ваш текст и публичные данные карточки без телефонов; бесплатные модели могут учиться на запросах.</p>
      </form></section></div>`;
    sizeInput();
    scrollThread();
    if (on) startTyper();
  }

  // Typewriter: example prompts are typed into the empty composer until the user clicks in.
  let typer = null;
  function stopTyper() {
    if (typer) clearTimeout(typer.timer);
    typer = null;
    const box = document.querySelector('#ai-typer');
    if (box) box.hidden = true;
    document.querySelector('#ai-send')?.classList.remove('ai-pulse');
  }
  function startTyper() {
    stopTyper();
    const box = document.querySelector('#ai-typer');
    const input = document.querySelector('#ai-input');
    if (!box || !input || input.value || AI.messages.length || AI.pending || document.activeElement === input) return;
    input.placeholder = '';
    box.hidden = false;
    const text = box.querySelector('span');
    if (reducedMotion()) { // no animation: show one example still
      typer = { s: { i: 0, len: EXAMPLES[0].length, phase: 'hold' }, timer: null };
      text.textContent = EXAMPLES[0];
      box.classList.add('still');
      return;
    }
    typer = { s: { i: 0, len: 0, phase: 'type' }, timer: null };
    const step = () => {  // chain of timeouts: Desktop forbids independent intervals
      if (!typer || !isOpen()) { typer = null; return; }
      const { next, delay } = typeStep(typer.s, EXAMPLES);
      typer.s = next;
      text.textContent = typeText(next, EXAMPLES);
      document.querySelector('#ai-send')?.classList.toggle('ai-pulse', next.phase === 'hold');
      typer.timer = setTimeout(step, delay);
    };
    typer.timer = setTimeout(step, 600);
  }
  // Clicking in takes over: the example being shown becomes editable text.
  function takeOverTyper() {
    if (!typer) return;
    const input = document.querySelector('#ai-input');
    const shown = typer.s.len > 0 ? EXAMPLES[typer.s.i % EXAMPLES.length] : '';
    stopTyper();
    if (!input) return;
    input.placeholder = 'Напишите задачу…';
    if (shown && !input.value) {
      input.value = shown; AI.draft = shown; sizeInput();
      input.setSelectionRange(shown.length, shown.length);
    }
  }

  function sizeInput() {
    const input = document.querySelector('#ai-input');
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = `${autosize(input.scrollHeight)}px`;
  }

  function scrollThread() {
    const thread = document.querySelector('#ai-thread');
    if (thread) thread.scrollTop = thread.scrollHeight;
  }

  function paint() {
    if (!isOpen()) return;
    document.querySelector('#ai-thread').innerHTML = threadHtml();
    const sendButton = document.querySelector('#ai-send');
    if (sendButton) sendButton.disabled = Boolean(AI.pending) || !AI.status?.configured;
    scrollThread();
  }

  function tick() {
    clearTimeout(tickTimer);
    const step = () => {  // chain of timeouts: Desktop forbids independent intervals
      if (!AI.pending) return;
      const meta = document.querySelector('[data-ai-pending]');
      if (meta) meta.textContent = pendingLabel();
      tickTimer = setTimeout(step, 1000);
    };
    tickTimer = setTimeout(step, 1000);
  }

  function fail(message, text) {
    // The question goes back into the box so it can be sent again, unless something new was typed meanwhile;
    // the history stays as it was before it.
    AI.messages = AI.messages.slice(0, -1);
    AI.pending = null;
    AI.error = message;
    if (!AI.draft.trim()) {
      AI.draft = text;
      const input = document.querySelector('#ai-input');
      if (input) { input.value = text; sizeInput(); }
    }
    if (!isOpen()) toast(`ИИ-чат: ${message}`, true); // on the chat page the error is shown in the thread
    paint();
  }

  async function poll(id, text) {
    let failures = 0;
    while (AI.pending?.id === id) {
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      if (AI.pending?.id !== id) return; // «Новый чат» dropped this reply
      let reply;
      try {
        reply = await call('ai_chat_result', id);
        failures = 0;
      } catch (error) {
        if (AI.pending?.id !== id) return;
        if (++failures < MAX_POLL_FAILURES) continue;
        fail(error.message, text);
        return;
      }
      if (AI.pending?.id !== id) return; // dropped while the request was on its way
      if (reply.status === 'running') continue;
      if (reply.status === 'error') { fail(reply.error, text); return; }
      // The category the reply was written for: the user may switch it before pressing «В скрипты».
      AI.messages.push({ role: 'assistant', content: reply.text, model: reply.model, seconds: reply.seconds,
        category: AI.pending.category });
      AI.pending = null;
      LEM.platform.haptic('success');
      if (!isOpen()) toast('ИИ-чат: ответ готов');
      paint();
      return;
    }
  }

  async function send(raw) {
    const fromBox = raw === undefined; // quick buttons leave a half-typed draft alone
    const text = String(fromBox ? document.querySelector('#ai-input')?.value ?? AI.draft : raw).trim();
    if (AI.pending || !AI.status?.configured) return;
    if (!text) { document.querySelector('#ai-input')?.focus(); return; }
    stopTyper();
    AI.messages.push({ role: 'user', content: text });
    AI.error = '';
    if (fromBox) {
      AI.draft = '';
      const input = document.querySelector('#ai-input');
      if (input) { input.value = ''; input.placeholder = 'Напишите задачу…'; sizeInput(); }
    }
    const pending = { id: null, started: Date.now(), category: AI.category };
    AI.pending = pending;
    paint();
    tick();
    const history = AI.messages.map(({ role, content }) => ({ role, content }));
    try {
      const { id } = await call('ai_chat_start', history, AI.category, AI.lead?.id || null);
      if (AI.pending !== pending) return; // «Новый чат» was pressed meanwhile
      pending.id = id;
      poll(id, text);
    } catch (error) {
      if (AI.pending === pending) fail(error.message, text);
    }
  }

  function scriptTitle(index) {
    const question = [...AI.messages.slice(0, index)].reverse().find((m) => m.role === 'user')?.content || '';
    const subject = AI.lead?.name || question;
    const title = `${categoryLabel(AI.messages[index]?.category || AI.category)} · ${subject}`.replace(/\s+/g, ' ').trim();
    return title.length > 60 ? `${title.slice(0, 59).trim()}…` : title;
  }

  async function saveScript(button) {
    const index = Number(button.dataset.aiSave);
    const message = AI.messages[index];
    if (!message || AI.saved.has(index)) return;
    button.disabled = true;
    const ok = await safe(() => call('ai_script_save', scriptTitle(index), plain(message.content),
      message.category || AI.category));
    if (ok) {
      AI.saved.add(index);
      const label = button.querySelector('span');
      if (label) label.textContent = 'Сохранено';
      toast('Сохранено в Скрипты · ✦ ИИ');
      LEM.platform.haptic('success');
    } else {
      button.disabled = false; // failed: let the user retry
    }
  }

  function toggleLeadPop(open) {
    const pop = document.querySelector('#ai-lead-pop');
    const opener = document.querySelector('[data-ai-lead-open]');
    if (!pop) return;
    pop.hidden = !open;
    opener?.setAttribute('aria-expanded', String(open));
    if (open) document.querySelector('#ai-lead-search')?.focus();
  }

  async function findLeads(q) {
    const box = document.querySelector('#ai-lead-results');
    if (!box) return;
    if (q.length < 2) { box.innerHTML = '<p class="ai-muted">Начните вводить название</p>'; return; }
    const rows = (await safe(() => call('businesses', { q, limit: 8 }))) || [];
    if (document.querySelector('#ai-lead-search')?.value.trim() !== q) return; // a newer search is running
    box.innerHTML = rows.length
      ? rows.map((b) => `<button type="button" class="ai-lead-option" data-ai-lead="${E(b.id)}" data-ai-lead-name="${E(b.name)}"><b>${E(b.name)}</b><span>${E(b.address || '')}</span></button>`).join('')
      : '<p class="ai-muted">Ничего не найдено</p>';
  }

  function refreshContext() {
    const ctx = document.querySelector('.ai-context');
    if (ctx) ctx.innerHTML = contextHtml();
  }

  function pickLead(option) {
    AI.lead = { id: option.dataset.aiLead, name: option.dataset.aiLeadName };
    refreshContext();
  }

  function newChat(button) {
    if (button && !reducedMotion()) {
      button.classList.remove('spin');
      void button.offsetWidth; // restart the spin on every click
      button.classList.add('spin');
    }
    clearTimeout(tickTimer);
    Object.assign(AI, { messages: [], pending: null, error: '', draft: '', saved: new Set() });
    const input = document.querySelector('#ai-input');
    if (input) { input.value = ''; sizeInput(); }
    paint();
    startTyper();
  }

  document.addEventListener('submit', (e) => {
    if (e.target.id !== 'ai-form') return;
    e.preventDefault(); e.stopImmediatePropagation(); send();
  }, true);

  document.addEventListener('input', (e) => {
    const id = e.target.id;
    if (id === 'ai-input') { AI.draft = e.target.value; sizeInput(); }
    else if (id === 'ai-lead-search') {
      clearTimeout(searchTimer);
      const q = e.target.value.trim();
      searchTimer = setTimeout(() => findLeads(q), 300);
    }
  });

  document.addEventListener('change', (e) => {
    if (e.target.id === 'ai-category') AI.category = e.target.value;
  });

  document.addEventListener('focusin', (e) => {
    if (e.target.id === 'ai-input') takeOverTyper();
  });
  document.addEventListener('focusout', (e) => {
    if (e.target.id === 'ai-input' && !e.target.value) setTimeout(() => { if (document.activeElement?.id !== 'ai-input') startTyper(); }, 0);
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.querySelector('#ai-lead-pop:not([hidden])')) { toggleLeadPop(false); return; }
    if (e.key !== 'Enter') return;
    if (e.target.id === 'ai-lead-search') { // Enter picks the first match instead of submitting anything
      e.preventDefault();
      const first = document.querySelector('.ai-lead-option');
      if (first) pickLead(first);
    } else if (e.target.id === 'ai-input' && composerKey(e) === 'send') {
      e.preventDefault(); send();
    }
  }, true);

  document.addEventListener('click', (e) => {
    const chat = document.querySelector('.ai-chat');
    if (!chat) return;
    const pop = document.querySelector('#ai-lead-pop');
    if (pop && !pop.hidden && !e.target.closest('#ai-lead-pop, [data-ai-lead-open]')) toggleLeadPop(false);
    if (e.target.closest('.ai-input-wrap') && typer) { document.querySelector('#ai-input')?.focus(); return; }
    const b = e.target.closest('button');
    if (!b || !b.closest('.ai-chat')) return;
    const d = b.dataset;
    if (d.aiLead) pickLead(b);
    else if (b.hasAttribute('data-ai-lead-open')) toggleLeadPop(document.querySelector('#ai-lead-pop')?.hidden);
    else if (b.hasAttribute('data-ai-lead-clear')) { AI.lead = null; refreshContext(); }
    else if (b.hasAttribute('data-ai-settings')) setView('settings');
    else if (d.aiQuick !== undefined) {
      if (d.aiQuick.endsWith(' ')) { // «Начни с …» needs the user's words: put it into the box
        const input = document.querySelector('#ai-input');
        if (input) { stopTyper(); input.value = d.aiQuick; AI.draft = d.aiQuick; input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
      } else send(d.aiQuick);
    } else if (d.aiCopy !== undefined) copyText(plain(AI.messages[Number(d.aiCopy)]?.content));
    else if (d.aiSave !== undefined) saveScript(b);
    else if (b.hasAttribute('data-ai-new')) newChat(b);
    else return;
    e.stopImmediatePropagation();
  }, true);

  const aiBaseRender = render;
  render = async function () {
    if (state.view === 'settings' && LEM.kind === 'desktop') {
      const result = await aiBaseRender.apply(this, arguments);
      try { await settingsKeyBlock(document.querySelector('#content')); } catch (_) { /* settings still usable */ }
      return result;
    }
    if (state.view !== 'ai') { stopTyper(); return aiBaseRender.apply(this, arguments); }
    const node = document.querySelector('#content');
    try { await aiPage(node); } catch (error) { node.innerHTML = empty('Не удалось загрузить раздел', error.message); }
    syncBackButton();
  };
  window.render = render;
})();
