// Follow-up desktop flows kept separate from the shared page layout.
mediaJob = function (job) {
  const label = job.kind === 'images' ? 'Галерея' : 'Товары';
  const archives = job.result?.archives || [];
  const folder = job.result?.folder;
  const actions = (folder ? `<button class="btn" data-open-folder="${E(job.id)}">Открыть папку ↗</button>
    <button class="ghost" data-copy-folder="${E(job.id)}">Сохранить копию</button>
    <span class="small muted">${E(job.result.files || 0)} файлов · ${E(folder)}</span>` : archives.map(name =>
    `<button class="btn" data-open-archive="${E(job.id)}" data-archive-name="${E(name)}">${E(name)} ↗</button>
     <button class="ghost" data-save-archive="${E(job.id)}" data-archive-name="${E(name)}">Сохранить копию</button>`
  ).join(''));
  const retry = ['done', 'failed'].includes(job.status)
    ? `<button class="ghost" data-repeat-job="${E(job.id)}">Повторить парсинг</button>` : '';
  const remove = !['running', 'queued'].includes(job.status)
    ? `<button class="danger" data-delete-media="${E(job.id)}">Удалить</button>` : '';
  return `<div class="row" data-activity-kind="media" data-activity-id="${E(job.id)}">
    <div class="row-head"><div><span class="row-title">${label}</span>
    <div class="small muted">${E(job.created_at?.slice(0, 16))} · ID ${E(job.organization_id)}</div></div>
    ${jobBadge(job)}</div>
    ${job.error ? `<div class="inline-note error-note">${E(job.error)}</div>` : ''}
    <div class="toolbar" style="margin-top:10px">${actions}${retry}${remove}</div>
  </div>`;
};

const baseSettings = settings;
settings = async function (node) {
  await baseSettings(node);
  if (state.config.migration_notice) {
    node.querySelector('.two-cols')?.insertAdjacentHTML('afterbegin',
      `<div class="inline-note wide">${E(state.config.migration_notice)}</div>`);
  }
  const panel = [...node.querySelectorAll('.panel')].find(x => x.textContent.includes('Данные и резервная копия'));
  panel?.querySelector('.toolbar')?.insertAdjacentHTML('beforeend',
    '<button class="btn" data-import-sqlite>Импорт старой SQLite</button><button class="ghost" data-open-logs>Открыть папку логов</button>');
  const telegramPanel = [...node.querySelectorAll('.panel')].find(x => x.textContent.includes('Личный Telegram'));
  telegramPanel?.insertAdjacentHTML('beforeend', `<div class="inline-note">
    <strong>Если Telegram не выдаёт API ID</strong><p>Создавайте черновики без ключей: в рассылке можно скопировать текст, открыть публичный чат и после фактической отправки отметить результат в CRM.</p>
    <p style="margin-top:8px">Для автоматической отправки нужны личные api_id и api_hash из <a href="https://my.telegram.org">my.telegram.org</a>. Если форма отвечает только «error», проверьте раздел API development tools на этом же аккаунте: у номера может быть только один API ID. Если ключей нет, отправьте скриншот ошибки в <a href="https://telegram.org/support">поддержку Telegram</a>. Бот не может первым написать незнакомому пользователю.</p>
  </div>`);
  const telegram = await call('telegram_state');
  const phoneCheckStatus = telegram.configured
    ? (telegram.session_exists
      ? 'API ID настроен. Проверка номера доступна в карточке организации; согласие на рассылку для неё не требуется.'
      : 'API ID настроен, но сначала войдите в Telegram по коду из этого раздела. Проверка не отправляет сообщений и не зависит от согласий.')
    : `${telegram.configuration_error || 'API ID не указан. Введите собственные api_id и api_hash в форме выше.'} Проверка номера пока недоступна; сбоя приложения не будет.`;
  telegramPanel?.querySelector('#telegram-config')?.insertAdjacentHTML('afterend',
    `<div class="inline-note" role="status"><strong>Проверка номера</strong><p>${E(phoneCheckStatus)}</p>
    <p>Проверка запускается отдельно из карточки организации, не требует согласия на рассылку и не отправляет сообщение.</p></div>`);
  node.querySelector('.two-cols')?.insertAdjacentHTML('beforeend', `<section class="panel">
    <h2>Безопасность отправки</h2>
    <p>По умолчанию разрешено не более пяти сообщений в день с паузой 60 секунд. Telegram может ограничить аккаунт и при меньшей нагрузке.</p>
    <form id="send-policy-form" class="form-grid" style="margin-top:14px">
      <label class="field">Лимит в день (1–20)<input name="daily_limit" type="number" min="1" max="20" value="${E(telegram.daily_limit)}" required></label>
      <label class="field">Пауза, секунд (30–3600)<input name="delay_seconds" type="number" min="30" max="3600" value="${E(telegram.delay_seconds)}" required></label>
      <div class="wide"><button class="btn primary">Сохранить ограничения</button></div>
    </form>
    <div class="inline-note">Отправляйте только тем, кто дал согласие или первым написал вам. Отказ «СТОП» сохраняется навсегда в этом приложении.</div>
  </section>`);
  const blocked = await call('contact_permissions', 'blocked');
  node.querySelector('.two-cols')?.insertAdjacentHTML('beforeend', `<section class="panel">
    <h2>Список отказов</h2><p>Эти контакты автоматически исключаются из будущих кампаний. Запись нельзя снять из интерфейса.</p>
    <div class="list" style="margin-top:12px">${blocked.length ? blocked.map(item => `<div class="row">
      <strong>${E(item.peer)}</strong>${item.phone ? `<div class="small muted">${E(item.phone)}</div>` : ''}
      <div class="small muted">${E(item.business_name || item.business_id || 'Контакт')} · ${E(item.note || 'Отказ от сообщений')} · ${E(item.updated_at)}</div>
    </div>`).join('') : empty('Отказов пока нет')}</div>
  </section>`);
};

const baseDetail = detail;
detail = async function (node) {
  await baseDetail(node);
  const business = await call('business', state.businessId);
  if (business.phones?.length) {
    node.querySelector('.detail-stack .panel')?.insertAdjacentHTML('beforeend',
      `<div class="inline-note"><div class="panel-head"><div><strong>Проверить Telegram по телефону</strong>
      <p>При проверке номер временно передаётся Telegram как контакт, затем удаляется. Сообщение не отправляется; согласие на рассылку не требуется.</p></div></div>
      <div class="toolbar"><select id="phone-check-select" aria-label="\u041d\u043e\u043c\u0435\u0440 \u0434\u043b\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438">${business.phones.map(phone =>
        `<option value="${E(phone)}">${E(phone)}</option>`).join('')}</select>
      <button class="btn" data-check-phone>Проверить номер</button></div>
      ${business.telegram ? `<div class="small" style="margin-top:8px">Последняя проверка: ${E(business.telegram.checked_at)} · ID ${E(business.telegram.telegram_id)}</div>` : ''}
      </div>`);
  }
  node.querySelector('#crm-form')?.insertAdjacentHTML('beforeend',
    `<div class="inline-note ${business.do_not_contact ? 'error-note' : ''}">
      <label><input id="do-not-contact" type="checkbox" ${business.do_not_contact ? 'checked' : ''}>
      Не писать этой организации</label>
      <input id="suppression-reason" aria-label="\u041f\u0440\u0438\u0447\u0438\u043d\u0430 \u043e\u0442\u043a\u0430\u0437\u0430 \u0438\u043b\u0438 \u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u043a\u0438" placeholder="Причина (необязательно)"
        value="${E(business.suppression_reason)}" style="width:100%;margin-top:8px">
      <button class="btn" type="button" data-save-suppression style="margin-top:8px">Сохранить правило</button>
    </div>`);
};

const baseCampaigns = campaigns;
campaigns = async function (node) {
  await baseCampaigns(node);
  if (state.campaignId) {
    const campaign = await call('campaign_get', state.campaignId);
    if (campaign.error) node.querySelector('.panel-head')?.insertAdjacentHTML('afterend',
      `<div class="inline-note error-note">${E(campaign.error)}</div>`);
    if (campaign.status === 'draft') {
      node.querySelector('.panel-head .toolbar')?.insertAdjacentHTML('afterbegin',
        '<button class="btn" data-check-campaign>Проверить адресатов</button>');
    }
  }
};

const baseScripts = scripts;
scripts = async function (node) {
  await baseScripts(node);
  if (!state.scriptBusiness) return;
  const business = await call('business', state.scriptBusiness);
  const preview = await call('template_render', state.script, state.scriptBusiness);
  node.querySelector('.two-cols')?.insertAdjacentHTML('beforeend',
    `<section class="panel" style="grid-column:1 / -1"><div class="panel-head">
      <div><h2>Текст для ${E(business.name)}</h2><p>Проверьте факты и заполните поля в квадратных скобках.</p></div>
      <button class="btn" data-copy-script>Копировать</button></div>
      <textarea id="script-preview" aria-label="\u0422\u0435\u043a\u0441\u0442 \u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043b\u0435\u043d\u043d\u043e\u0433\u043e \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u044f" readonly>${E(preview)}</textarea></section>`);
};

const baseBusinesses = businesses;
businesses = async function (node) {
  await baseBusinesses(node);
  const input = node.querySelector('#business-filter');
  const list = node.querySelector('#business-list');
  if (!input || !list) return;
  list.insertAdjacentHTML('afterend',
    '<button class="btn" data-load-businesses style="margin-top:12px">Показать ещё</button>');
  state.businessQuery = '';
  state.businessOffset = 50;
  let timer;
  input.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      state.businessQuery = input.value.trim();
      const rows = await safe(() => call('businesses', {q: state.businessQuery, limit: 50}));
      if (!rows) return;
      state.businessOffset = rows.length;
      list.innerHTML = rows.length ? rows.map(b => businessRow(b, true)).join('')
        : empty('Ничего не найдено');
      node.querySelector('[data-load-businesses]').hidden = rows.length < 50;
    }, 250);
  };
  node.querySelector('[data-load-businesses]').hidden = list.children.length < 50;
};

const baseMedia = media;
media = async function (node) {
  await baseMedia(node);
  if (node.querySelector('#media-url')) node.querySelector('#media-url').value = state.mediaUrl || '';
  if (node.querySelector('#media-count')) node.querySelector('#media-count').value = state.mediaCount || 10;
  if (state.mediaSections) node.querySelectorAll('[data-section]').forEach(input => {
    input.checked = state.mediaSections.includes(input.dataset.section);
  });
  const select = node.querySelector('#media-business');
  if (!select) return;
  if (state.mediaBusiness && ![...select.options].some(x => x.value === state.mediaBusiness)) {
    const selected = await call('business', state.mediaBusiness);
    if (selected) {
      select.insertAdjacentHTML('beforeend',
        `<option value="${E(selected.id)}">${E(selected.name)} · ${E(selected.address || selected.id)}</option>`);
      select.value = selected.id;
      select.closest('.form-grid')?.insertAdjacentHTML('afterend',
        `<div class="inline-note">Яндекс.Карты: ${E(selected.map_url)}</div>`);
    }
  }
  select.closest('label')?.insertAdjacentHTML('beforebegin',
    '<label class="field wide">Найти в базе<input id="media-search" placeholder="Название или адрес"></label>');
  const input = node.querySelector('#media-search');
  let timer;
  input.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const rows = await safe(() => call('businesses', {q: input.value.trim(), limit: 50}));
      if (!rows) return;
      select.innerHTML = '<option value="">Выберите из базы…</option>' + rows.map(b =>
        `<option value="${E(b.id)}">${E(b.name)} · ${E(b.address || b.id)}</option>`).join('');
    }, 250);
  };
};

document.addEventListener('input', event => {
  if (event.target.id === 'media-url') state.mediaUrl = event.target.value;
  if (event.target.id === 'media-count') state.mediaCount = Number(event.target.value);
});
document.addEventListener('change', event => {
  if (event.target.hasAttribute('data-section')) {
    state.mediaSections = [...document.querySelectorAll('[data-section]:checked')].map(x => x.dataset.section);
  }
});

document.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.repeatJob) {
    event.stopImmediatePropagation();
    safe(async () => {
      const jobs = await call('media_jobs');
      const old = jobs.find(item => item.id === button.dataset.repeatJob);
      if (!old) throw Error('Задание не найдено');
      const payload = old.business_id
        ? {business_id: old.business_id} : {url: old.params.url};
      if (old.kind === 'images') payload.sections = old.params.sections;
      payload.repeat = true;
      const result = await call('media_start', old.kind, payload);
      toast(result.created ? 'Повторное задание создано' : 'Задание уже выполняется');
      render();
    });
  } else if (button.dataset.saveArchive) {
    event.stopImmediatePropagation();
    safe(async () => {
      const target = await call('media_save', button.dataset.saveArchive, button.dataset.archiveName);
      if (target) toast(`Копия сохранена: ${target}`);
    });
  } else if (button.dataset.action === 'script-for-business') {
    event.stopImmediatePropagation();
    state.scriptBusiness = state.businessId;
    setView('scripts');
  } else if (button.dataset.action === 'campaign-start') {
    event.stopImmediatePropagation();
    safe(async () => {
      const draft = await call('campaign_get', state.campaignId);
      if (!confirm(`Отправить ${draft.recipients.length} проверенных сообщений с вашего личного Telegram?`)) return;
      await call('campaign_start', state.campaignId);
      toast('Рассылка запущена'); render();
    });
  } else if (button.hasAttribute('data-copy-script')) {
    event.stopImmediatePropagation();
    safe(async () => {
      const field = document.querySelector('#script-preview');
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(field.value);
      else { field.select(); document.execCommand('copy'); }
      toast('Текст скопирован');
    });
  } else if (button.hasAttribute('data-load-businesses')) {
    event.stopImmediatePropagation();
    safe(async () => {
      const rows = await call('businesses', {q: state.businessQuery || '', limit: 50,
        offset: state.businessOffset || 0});
      document.querySelector('#business-list').insertAdjacentHTML('beforeend',
        rows.map(b => businessRow(b, true)).join(''));
      state.businessOffset += rows.length;
      button.hidden = rows.length < 50;
    });
  } else if (button.dataset.deleteMedia) {
    event.stopImmediatePropagation();
    safe(async () => {
      if (!confirm('Удалить этот архив и запись задания? Организация останется в базе.')) return;
      await call('media_delete', button.dataset.deleteMedia);
      toast('Архив удалён'); render();
    });
  } else if (button.hasAttribute('data-import-sqlite')) {
    event.stopImmediatePropagation();
    safe(async () => {
      const result = await call('import_sqlite');
      if (result) toast(`Перенесено ${result.imported} карточек и ${result.archive_folders} папок медиа`);
      render();
    });
  } else if (button.hasAttribute('data-open-logs')) {
    event.stopImmediatePropagation();
    safe(() => call('open_logs'));
  } else if (button.hasAttribute('data-check-campaign')) {
    event.stopImmediatePropagation();
    safe(async () => {
      const result = await call('campaign_check', state.campaignId);
      const ready = result.recipients.filter(item => item.status === 'ready').length;
      toast(`Проверено: ${ready} из ${result.recipients.length}`);
      render();
    });
  } else if (button.hasAttribute('data-save-suppression')) {
    event.stopImmediatePropagation();
    safe(async () => {
      await call('business_suppress', state.businessId,
        document.querySelector('#do-not-contact').checked,
        document.querySelector('#suppression-reason').value);
      toast('Правило контакта сохранено'); render();
    });
  } else if (button.hasAttribute('data-check-phone')) {
    event.stopImmediatePropagation();
    safe(async () => {
      toast('Проверяю номер через Telegram…');
      const result = await call('telegram_check_phone', state.businessId,
        document.querySelector('#phone-check-select').value);
      toast(result.found ? `Telegram найден: ID ${result.telegram_id}` : 'Telegram для этого номера не найден');
      render();
    });
  }
}, true);

document.addEventListener('click', event => {
  const anchor = event.target.closest('a[href]');
  if (!anchor) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  safe(() => call('open_external', anchor.href));
}, true);

// The media source is exclusive: a selected card never reads a stale manual URL.
const baseDashboard = dashboard;
dashboard = async function (node) {
  await baseDashboard(node);
  const label = [...node.querySelectorAll('.metric label')].find(x => x.textContent === 'Медиа-архивов');
  if (label) label.textContent = 'Результатов медиа';
};

media = async function (node) {
  setHeader('Медиа', 'Галерея и товары для организации, сохранённые в обычных папках');
  const [rows, jobs] = await Promise.all([
    call('businesses', {q: state.mediaQuery || '', limit: 50}), call('media_jobs')
  ]);
  let selected = rows.find(row => row.id === state.mediaBusiness);
  if (!selected && state.mediaBusiness) selected = await call('business', state.mediaBusiness);
  const choices = selected && !rows.some(row => row.id === selected.id) ? [selected, ...rows] : rows;
  node.innerHTML = `<section class="panel task-panel">
    <div class="panel-head"><div><span class="section-kicker">НОВОЕ ЗАДАНИЕ</span><h2>Источник медиа</h2>
      <p>${state.mediaManual ? 'Вставьте ссылку на карточку Яндекс.Карт' : 'Найдите организацию; ссылка будет взята из базы'}</p></div>
      <div class="source-switch"><button class="tab ${!state.mediaManual ? 'active' : ''}" data-media-source="base">Из базы</button>
      <button class="tab ${state.mediaManual ? 'active' : ''}" data-media-source="manual">По ссылке</button></div></div>
    ${state.mediaManual ? `<label class="field">Ссылка на организацию<input id="media-url" placeholder="https://yandex.ru/maps/org/…" value="${E(state.mediaUrl || '')}"></label>` :
      `<div class="form-grid"><label class="field wide">Найти организацию<input id="media-search" placeholder="Название или адрес" value="${E(state.mediaQuery || '')}"></label>
      <label class="field wide">Организация<select id="media-business"><option value="">Выберите из базы…</option>${choices.map(row =>
        `<option value="${E(row.id)}" ${row.id === state.mediaBusiness ? 'selected' : ''}>${E(row.name)} · ${E(row.city || row.address || row.id)}</option>`).join('')}</select></label></div>
      ${selected ? `<div class="inline-note">Выбрана: <strong>${E(selected.name)}</strong> · ${E(selected.map_url)}</div>` : ''}`}
    <div class="tabs"><button class="tab ${state.mediaKind === 'images' ? 'active' : ''}" data-media-kind="images">Галерея</button>
      <button class="tab ${state.mediaKind === 'products' ? 'active' : ''}" data-media-kind="products">Товары</button></div>
    ${state.mediaKind === 'images' ? `<div class="checkboxes"><label><input type="checkbox" data-section="Внутри" ${!state.mediaSections || state.mediaSections.includes('Внутри') ? 'checked' : ''}> Внутри</label>
      <label><input type="checkbox" data-section="Еда" ${state.mediaSections?.includes('Еда') ? 'checked' : ''}> Еда</label>
      <label><input type="checkbox" data-section="Меню" ${state.mediaSections?.includes('Меню') ? 'checked' : ''}> Меню</label>
      <label class="field">Фото на секцию<input id="media-count" type="number" min="1" max="50" value="${E(state.mediaCount || 10)}"></label></div>` :
      '<p>Собрать названия товаров, цены и доступные фотографии в отдельную папку.</p>'}
    <div class="task-actions"><button class="btn primary" data-action="start-media">Запустить ${state.mediaKind === 'images' ? 'галерею' : 'товары'}</button>
      <span class="small muted">Результат: папка с именем организации и города</span></div>
  </section><section class="panel"><div class="panel-head"><div><span class="section-kicker">РЕЗУЛЬТАТЫ</span><h2>История медиа</h2></div><span class="result-count">${jobs.length} заданий</span></div>
    <div class="list">${jobs.length ? jobs.map(mediaJob).join('') : empty('Заданий пока нет')}</div></section>`;
  const search = node.querySelector('#media-search');
  if (search) {
    let timer;
    search.oninput = () => {
      state.mediaQuery = search.value;
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const found = await safe(() => call('businesses', {q: state.mediaQuery.trim(), limit: 50}));
        if (!found) return;
        const select = node.querySelector('#media-business');
        if (!select) return;
        select.innerHTML = '<option value="">Выберите из базы…</option>' + found.map(row =>
          `<option value="${E(row.id)}">${E(row.name)} · ${E(row.city || row.address || row.id)}</option>`).join('');
        select.value = found.some(row => row.id === state.mediaBusiness) ? state.mediaBusiness : '';
      }, 250);
    };
  }
};

const campaignBaseWithChecks = campaigns;
campaigns = async function (node) {
  await campaignBaseWithChecks(node);
  const telegram = await call('telegram_state');
  state.telegramConfigured = Boolean(telegram.configured);
  if (state.campaignId) {
    const draft = await call('campaign_get', state.campaignId);
    if ((!telegram.configured || draft.recipients.some(row => row.status === 'sent')) && draft.status === 'draft') {
      node.querySelector('[data-action="campaign-start"]')?.remove();
    }
    if (!telegram.configured && draft.status === 'draft') {
      node.querySelector('[data-check-campaign]')?.remove();
    }
    if (draft.status === 'draft') {
      node.querySelector('.panel-head')?.insertAdjacentHTML('afterend',
        `<div class="inline-note">${telegram.configured ? 'Автоматическая отправка доступна после проверки адресатов. ' : 'API Telegram пока не настроен. '}
        Для ручной отправки скопируйте текст, откройте чат в Telegram и подтвердите отправку здесь.</div>`);
      node.querySelectorAll('[data-recipient-message]').forEach(field => {
        const id = field.dataset.recipientMessage;
        const recipient = draft.recipients.find(row => row.business_id === id);
        const row = field.closest('.row');
        if (!recipient || !row) return;
        const heading = row.querySelector('.row-head strong');
        if (heading) heading.textContent = `${recipient.business_name || recipient.business_id} · ${recipient.peer.startsWith('phone:') ? 'проверенный номер' : '@' + recipient.peer}`;
        if (recipient.status === 'sent') {
          field.readOnly = true;
          row.querySelector('[data-save-recipient]')?.remove();
        }
        if (recipient.status === 'prepared') {
          field.readOnly = true;
          row.querySelector('[data-save-recipient]')?.remove();
        }
        const statusText = recipient.permission_status === 'allowed'
          ? `Согласие: ${recipient.permission_source === 'inbound_message' ? 'человек написал первым' : 'получено явно'} · ${recipient.permission_note}`
          : recipient.permission_status === 'blocked' ? 'Контакт отказался — будущая отправка запрещена.'
          : 'Сначала подтвердите согласие или входящее сообщение.';
        row.insertAdjacentHTML('beforeend', `<div class="permission-box ${recipient.permission_status === 'blocked' ? 'error-note' : ''}">
          <strong>${E(statusText)}</strong>${recipient.permission_status === 'allowed' || recipient.permission_status === 'blocked' ? '' : `
          <div class="form-grid" style="margin-top:10px">
            <label class="field">Основание<select data-permission-source="${E(id)}">
              <option value="explicit_consent">Человек явно согласился</option>
              <option value="inbound_message">Человек написал первым</option>
            </select></label>
            <label class="field">Где/когда получено согласие<input data-permission-note="${E(id)}" placeholder="Например: заявка 16.10.2026"></label>
          </div><button class="btn" data-permission-allow="${E(id)}" style="margin-top:9px">Сохранить согласие</button>`}
          <div class="toolbar" style="margin-top:9px"><button class="danger" data-permission-block="${E(id)}" ${recipient.permission_status === 'blocked' ? 'disabled' : ''}>${recipient.permission_status === 'blocked' ? 'Отказ сохранён' : 'Больше не писать'}</button></div>
        </div>`);
        if (recipient.permission_status === 'allowed' && !recipient.peer.startsWith('phone:') && recipient.status === 'prepared') {
          row.insertAdjacentHTML('beforeend', `<div class="toolbar manual-actions">
            <button class="btn" data-manual-sent="${E(id)}">Я отправил сообщение</button>
            <button class="ghost" data-manual-not-sent="${E(id)}">Я не отправлял</button>
          </div>`);
        } else if (recipient.permission_status === 'allowed' && !recipient.peer.startsWith('phone:') && recipient.status !== 'sent') {
          row.insertAdjacentHTML('beforeend', `<div class="toolbar manual-actions">
            <button class="btn" data-manual-open="${E(id)}">Скопировать текст и открыть чат</button>
          </div>`);
        }
      });
    } else {
      node.querySelectorAll('[data-recipient-message]').forEach(field => {
        const id = field.dataset.recipientMessage;
        const recipient = draft.recipients.find(row => row.business_id === id);
        const row = field.closest('.row');
        if (!recipient || !row || recipient.permission_status === 'blocked') return;
        row.insertAdjacentHTML('beforeend', `<div class="toolbar permission-box">
          <span>Если адресат попросил больше не писать, сохраните отказ здесь.</span>
          <button class="danger" data-permission-block="${E(id)}">Больше не писать</button>
        </div>`);
      });
    }
    return;
  }
  const form = node.querySelector('#campaign-form');
  if (!form) return;
  const options = await call('business_filter_options');
  const filters = state.campaignFilters || {};
  const filterSelect = (name, label) => `<label class="field">${label}<select data-campaign-filter="${name}">
    <option value="">Все</option>${(options[name] || []).filter(Boolean).map(value =>
      `<option value="${E(value)}" ${filters[name] === value ? 'selected' : ''}>${E(name === 'status' ? state.config.statuses[value] || value : value)}</option>`).join('')}</select></label>`;
  form.querySelector('.toolbar')?.insertAdjacentHTML('beforebegin', `<div class="recipient-picker">
    <div class="panel-head"><div><span class="section-kicker">ШАГ 1 · АДРЕСАТЫ</span><h2>Выберите организации</h2>
      <p>Поиск по базе, фильтры и выбор находятся здесь же.</p></div><strong id="campaign-selected">${state.selected.size} выбрано</strong></div>
    <div class="form-grid"><label class="field wide">Название или адрес<input id="campaign-query" value="${E(filters.q || '')}" placeholder="Поиск по базе"></label>
      ${filterSelect('city', 'Город')}${filterSelect('niche', 'Ниша')}${filterSelect('status', 'Статус')}</div>
    <div class="toolbar picker-toolbar"><button type="button" class="ghost" data-campaign-select-page>Выбрать доступных в списке</button>
      <button type="button" class="ghost" data-campaign-clear>Снять выбор</button>
      <span class="small muted">Для автоматической отправки нужен Telegram-адрес или проверенный номер.</span></div>
    <div class="list" id="campaign-business-list"></div><button type="button" class="btn" data-campaign-more hidden>Показать ещё</button>
  </div>`);
  const picker = form.querySelector('.recipient-picker');
  if (picker) form.prepend(picker);
  form.querySelector(':scope > .form-grid')?.insertAdjacentHTML('beforebegin',
    '<span class="section-kicker">ШАГ 2 · СООБЩЕНИЕ</span>');
  const helper = form.closest('.panel')?.querySelector('.panel-head p');
  if (helper) helper.textContent = 'Выберите адресатов ниже, затем скрипт. Черновик можно проверить перед отправкой.';
  form.querySelector(':scope > p')?.remove();
  await loadCampaignBusinesses(false);
};

async function loadCampaignBusinesses(more) {
  const list = document.querySelector('#campaign-business-list');
  if (!list) return;
  const filters = state.campaignFilters || {};
  const offset = more ? (state.campaignOffset || 0) : 0;
  const rows = await call('businesses', {...filters, limit: 50, offset});
  if (!more) list.innerHTML = '';
  list.insertAdjacentHTML('beforeend', rows.map(row => {
    const hasPublic = row.socials?.some(x => /https:\/\/(t\.me|telegram\.me)\//i.test(x.url || ''));
    const eligible = hasPublic || (state.telegramConfigured && Boolean(row.telegram));
    return `<label class="recipient-row"><input type="checkbox" data-select="${E(row.id)}" ${state.selected.has(row.id) ? 'checked' : ''} ${eligible ? '' : 'disabled'}>
      <span><strong>${E(row.name)}</strong><small>${E(row.city || '')} · ${E(row.address || '')}</small></span>
      <span class="chip ${eligible ? 'green' : 'warn'}">${eligible ? hasPublic ? 'Telegram' : 'Номер проверен' : row.telegram ? 'Нужен API' : 'Нет адресата'}</span>
      ${eligible ? '' : `<button type="button" class="ghost" data-open="${E(row.id)}">Карточка</button>`}</label>`;
  }).join('') || (!more ? empty('Организации не найдены') : ''));
  state.campaignOffset = offset + rows.length;
  state.campaignPage = rows;
  const button = document.querySelector('[data-campaign-more]');
  if (button) button.hidden = rows.length < 50;
  updateCampaignSelected();
}

function updateCampaignSelected() {
  const count = document.querySelector('#campaign-selected');
  if (count) count.textContent = `${state.selected.size} выбрано`;
}

document.addEventListener('change', event => {
  if (event.target.hasAttribute('data-select')) updateCampaignSelected();
  if (!event.target.hasAttribute('data-campaign-filter')) return;
  state.campaignFilters = {...(state.campaignFilters || {}), [event.target.dataset.campaignFilter]: event.target.value};
  safe(() => loadCampaignBusinesses(false));
});
document.addEventListener('input', event => {
  if (event.target.id !== 'campaign-query') return;
  state.campaignFilters = {...(state.campaignFilters || {}), q: event.target.value.trim()};
  clearTimeout(state.campaignTimer);
  state.campaignTimer = setTimeout(() => safe(() => loadCampaignBusinesses(false)), 250);
});
document.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.mediaSource) {
    event.stopImmediatePropagation();
    state.mediaManual = button.dataset.mediaSource === 'manual';
    if (state.mediaManual) state.mediaBusiness = null;
    render();
  } else if (button.dataset.action === 'start-media') {
    event.stopImmediatePropagation();
    safe(async () => {
      const payload = state.mediaManual
        ? {url: document.querySelector('#media-url')?.value.trim()}
        : {business_id: document.querySelector('#media-business')?.value};
      if (state.mediaKind === 'images') {
        const count = Number(document.querySelector('#media-count')?.value);
        payload.sections = Object.fromEntries([...document.querySelectorAll('[data-section]:checked')].map(x => [x.dataset.section, count]));
      }
      const job = await call('media_start', state.mediaKind, payload);
      toast(job.created ? 'Задание создано' : job.status === 'done' ? 'Результат уже готов. Используйте «Повторить парсинг».' : 'Задание уже выполняется');
      render();
    });
  } else if (button.dataset.openFolder || button.dataset.copyFolder) {
    event.stopImmediatePropagation();
    safe(async () => {
      const path = button.dataset.openFolder ? await call('media_open', button.dataset.openFolder, '')
        : await call('media_save', button.dataset.copyFolder, '');
      if (path && button.dataset.copyFolder) toast(`Папка сохранена: ${path}`);
    });
  } else if (button.hasAttribute('data-campaign-more')) {
    event.stopImmediatePropagation(); safe(() => loadCampaignBusinesses(true));
  } else if (button.hasAttribute('data-campaign-select-page')) {
    event.stopImmediatePropagation();
    document.querySelectorAll('#campaign-business-list input[data-select]:not(:disabled)').forEach(input => {
      input.checked = true; state.selected.add(input.dataset.select);
    });
    updateCampaignSelected();
  } else if (button.hasAttribute('data-campaign-clear')) {
    event.stopImmediatePropagation(); state.selected.clear();
    document.querySelectorAll('#campaign-business-list input[data-select]').forEach(input => input.checked = false);
    updateCampaignSelected();
  } else if (button.dataset.manualOpen) {
    event.stopImmediatePropagation();
    safe(async () => {
      const field = document.querySelector(`[data-recipient-message="${CSS.escape(button.dataset.manualOpen)}"]`);
      const value = field?.value || '';
      if (!value.trim() || /\[[^\]]+\]/.test(value)) throw Error('Заполните поля в квадратных скобках');
      const prepared = await call('campaign_manual_prepare', state.campaignId, button.dataset.manualOpen, value);
      try {
        if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(prepared.message);
        else { field.select(); document.execCommand('copy'); }
      } catch (_) { field.select(); document.execCommand('copy'); }
      try {
        await call('open_external', prepared.url);
        toast('Текст скопирован. Вставьте и отправьте его в Telegram.');
      } finally { render(); }
    });
  } else if (button.dataset.permissionAllow) {
    event.stopImmediatePropagation();
    safe(async () => {
      const id = button.dataset.permissionAllow;
      const source = document.querySelector(`[data-permission-source="${CSS.escape(id)}"]`)?.value || '';
      const note = document.querySelector(`[data-permission-note="${CSS.escape(id)}"]`)?.value || '';
      if (note.trim().length < 3) throw Error('Укажите, где или когда получено согласие');
      await call('campaign_permission', state.campaignId, id, 'allowed', source, note);
      toast('Согласие сохранено'); render();
    });
  } else if (button.dataset.permissionBlock) {
    event.stopImmediatePropagation();
    safe(async () => {
      if (!confirm('Сохранить отказ и исключить этот контакт из будущих кампаний?')) return;
      await call('campaign_permission', state.campaignId, button.dataset.permissionBlock,
        'blocked', 'opt_out', 'Отказ/просьба больше не писать');
      toast('Отказ сохранён. Контакт исключён из рассылок.'); render();
    });
  } else if (button.dataset.manualSent) {
    event.stopImmediatePropagation();
    safe(async () => {
      if (!confirm('Вы уже отправили это сообщение в Telegram вручную?')) return;
      await call('campaign_mark_manual_sent', state.campaignId, button.dataset.manualSent);
      toast('Отправка отмечена в CRM'); render();
    });
  } else if (button.dataset.manualNotSent) {
    event.stopImmediatePropagation();
    safe(async () => {
      if (!confirm('Подтвердите, что сообщение не было отправлено. Подготовленная попытка останется в дневном журнале.')) return;
      await call('campaign_manual_not_sent', state.campaignId, button.dataset.manualNotSent);
      toast('Отмечено: сообщение не отправлялось. Дневной лимит и пауза учитывают подготовленную попытку.'); render();
    });
  }
}, true);

document.addEventListener('submit', event => {
  if (event.target.id === 'telegram-config') {
    event.preventDefault(); event.stopImmediatePropagation();
    safe(async () => {
      const form = event.target;
      const values = new FormData(form);
      const apiId = String(values.get('api_id') || '').trim();
      const apiHash = String(values.get('api_hash') || '').trim();
      const phone = String(values.get('phone') || '').trim();
      if (!/^\d+$/.test(apiId) || Number(apiId) <= 0) throw Error('API ID должен быть положительным целым числом.');
      if (!/^[a-fA-F0-9]{32}$/.test(apiHash)) throw Error('API hash должен содержать 32 шестнадцатеричных символа.');
      if (!/^\+\d{10,15}$/.test(phone)) throw Error('Введите номер в международном формате, например +79990000000.');
      await call('telegram_configure', Number(apiId), apiHash, phone);
      toast('Настройки Telegram сохранены'); render();
    });
    return;
  }
  if (event.target.id !== 'send-policy-form') return;
  event.preventDefault();
  event.stopImmediatePropagation();
  safe(async () => {
    const values = new FormData(event.target);
    const policy = await call('telegram_policy_save', Number(values.get('daily_limit')),
      Number(values.get('delay_seconds')));
    toast(`Ограничения сохранены: ${policy.daily_limit} в день, пауза ${policy.delay_seconds} секунд`);
  });
}, true);

function showFirstRunWizard() {
  if (!state.config?.first_run || document.querySelector('#first-run-dialog')) return;
  const dialog = document.createElement('dialog');
  dialog.id = 'first-run-dialog';
  dialog.setAttribute('aria-labelledby', 'setup-title');
  dialog.innerHTML = `<section class="setup-card">
    <span class="section-kicker">ПЕРВЫЙ ЗАПУСК · ${E(state.config.version)}</span>
    <h2 id="setup-title">Добро пожаловать в LeadEqualMoney</h2>
    <p>Можно сразу начать с локальной базы и ручной подготовки сообщений. Для этого API-ключи не нужны.</p>
    <div class="inline-note"><strong>BotFather token не нужен.</strong> Локальная версия не использует Bot API. Для входа в личный Telegram нужны только ваши собственные API ID и API hash; вводите их только в этом окне.</div>
    <form id="first-run-form" class="form-grid" novalidate>
      <label class="field">API ID<input name="api_id" type="text" inputmode="numeric" autocomplete="off"></label>
      <label class="field">API hash<input name="api_hash" type="password" autocomplete="off"></label>
      <label class="field wide">Номер телефона в международном формате<input name="phone" type="tel" placeholder="+79990000000" autocomplete="tel"></label>
      <div class="wide setup-error" role="alert" aria-live="assertive" tabindex="-1" hidden></div>
      <div class="toolbar wide"><button class="btn primary" type="submit">Сохранить API-ключи</button>
        <button class="ghost" type="button" data-setup-manual>Продолжить без API</button></div>
    </form>
    <p class="small muted">Папка данных: ${E(state.config.data_dir)}. Вход по коду можно выполнить позже в «Настройках».</p>
  </section>`;
  document.body.append(dialog);
  dialog.addEventListener('cancel', event => event.preventDefault());
  dialog.showModal();
  dialog.querySelector('[name="api_id"]').focus();
}

async function finishFirstRun(dialog, values = null) {
  const error = dialog.querySelector('.setup-error');
  error.hidden = true;
  try {
    if (values) {
      const apiId = values.get('api_id').trim();
      const apiHash = values.get('api_hash').trim();
      const phone = values.get('phone').trim();
      if (!/^\d+$/.test(apiId) || Number(apiId) <= 0) throw Error('API ID должен быть положительным целым числом.');
      if (!/^[a-fA-F0-9]{32}$/.test(apiHash)) throw Error('API hash должен содержать 32 шестнадцатеричных символа.');
      if (!/^\+\d{10,15}$/.test(phone)) throw Error('Введите номер в международном формате, например +79990000000.');
      await call('telegram_configure', Number(apiId), apiHash, phone);
    }
    await call('complete_setup');
    state.config.first_run = false;
    dialog.close(); dialog.remove();
    if (values) toast('API-ключи сохранены. Код входа можно запросить в «Настройках».');
    else toast('Готово. Ручной режим можно использовать без API-ключей.');
    render();
  } catch (exception) {
    error.textContent = exception.message || 'Не удалось сохранить настройки. Проверьте поля и повторите.';
    error.hidden = false;
    error.focus?.();
  }
}

window.addEventListener('pywebviewready', () => setTimeout(showFirstRunWizard, 250));
document.addEventListener('submit', event => {
  if (event.target.id !== 'first-run-form') return;
  event.preventDefault(); event.stopImmediatePropagation();
  finishFirstRun(event.target.closest('dialog'), new FormData(event.target));
}, true);
document.addEventListener('click', event => {
  const button = event.target.closest('[data-setup-manual]');
  if (!button) return;
  event.preventDefault(); event.stopImmediatePropagation();
  finishFirstRun(button.closest('dialog'));
}, true);
