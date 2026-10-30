// Follow-up desktop flows kept separate from the shared page layout.
mediaJob = function (job) {
  const label = job.kind === 'images' ? 'Галерея' : 'Товары';
  const archives = job.result?.archives || [];
  const folder = job.result?.folder;
  const actions = (folder ? `<button class="btn" data-open-folder="${E(job.id)}">Открыть папку ↗</button>
    <button class="ghost" data-copy-folder="${E(job.id)}">Сохранить копию</button>
    <span class="small muted">${E(job.result.photos ?? job.result.files ?? 0)} фото · ${E(folder)}</span>` : archives.map(name =>
    `<button class="btn" data-open-archive="${E(job.id)}" data-archive-name="${E(name)}">${E(name)} ↗</button>
     <button class="ghost" data-save-archive="${E(job.id)}" data-archive-name="${E(name)}">Сохранить копию</button>`
  ).join(''));
  const retry = ['done', 'failed'].includes(job.status)
    ? `<button class="ghost" data-repeat-job="${E(job.id)}">Повторить парсинг</button>` : '';
  const resume = job.kind === 'images' && job.status === 'failed'
    ? `<button class="btn" data-resume-job="${E(job.id)}">Продолжить с сохранённого</button>` : '';
  const remove = !['running', 'queued'].includes(job.status)
    ? `<button class="danger" data-delete-media="${E(job.id)}">Удалить</button>` : '';
  return `<div class="row" data-activity-kind="media" data-activity-id="${E(job.id)}">
    <div class="row-head"><div><span class="row-title">${label}</span>
    <div class="small muted">${E(job.created_at?.slice(0, 16))} · ID ${E(job.organization_id)}</div></div>
    ${jobBadge(job)}</div>
    ${job.error ? `<div class="inline-note error-note">${E(job.error)}</div>` : ''}
    <div class="toolbar" style="margin-top:10px">${actions}${resume}${retry}${remove}</div>
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
  const diagnostics = await safe(() => call('debug_html_enabled'));
  panel?.insertAdjacentHTML('beforeend', `<label class="field" style="margin-top:14px"><span><input type="checkbox" id="debug-html" ${diagnostics ? 'checked' : ''}> Сохранять HTML карточки при ошибке парсинга</span></label><div class="inline-note">По умолчанию выключено. HTML может содержать телефоны и текст отзывов; файлы сохраняются только в локальной папке debug.</div>`);
};

const baseSearch = search;
search = async function (node) {
  await baseSearch(node);
  node.querySelector('#search-form .form-grid')?.insertAdjacentHTML('beforeend',
    '<label class="field">Отзывы на организацию<input name="review_limit" type="number" min="0" max="100" value="20"><small class="muted">0 отключает сбор; максимум 100</small></label>');
};

const baseCall = call;
call = async function (method, ...args) {
  if (method === 'search_start' && args[0] && args[0].review_limit === undefined) {
    args[0].review_limit = Number(document.querySelector('#search-form [name="review_limit"]')?.value ?? 20);
  }
  return baseCall(method, ...args);
};

const baseDetail = detail;
detail = async function (node) {
  await baseDetail(node);
  const business = await call('business', state.businessId);
  const detail = business?.details || {};
  const hours = business?.hours || [];
  const features = business?.features || [];
  const links = business?.links || [];
  const reviews = business?.review_items || [];
  const detailNode = node.querySelector('.detail-stack .panel .business-grid');
  detailNode?.insertAdjacentHTML('afterend', `<section class="org-details-section" aria-label="Подробности карточки">
    <div class="inline-note"><strong>Оценка:</strong> ${E(business.rating ?? '—')} · ${E(business.reviews ?? '—')} отзывов · <strong>Фото:</strong> ${E(detail.photo_count ?? 'нет данных')}</div>
    ${detail.description ? `<p>${E(detail.description)}</p>` : ''}
    ${detail.latitude != null && detail.longitude != null ? `<p class="small muted">Координаты: ${E(detail.latitude)}, ${E(detail.longitude)}</p>` : ''}
    ${detail.categories?.length ? `<p class="small">Категории: ${detail.categories.map(E).join(' · ')}</p>` : ''}
    ${hours.length ? `<h3>Часы работы</h3><div class="org-details-list">${hours.map(x => `<div><strong>${E(x.weekday)}</strong><span>${x.is_closed ? 'Закрыто' : `${E(x.opens || '—')}–${E(x.closes || '—')}`}</span></div>`).join('')}</div>` : ''}
    ${features.length ? `<h3>Особенности</h3><div class="org-details-list">${features.map(x => `<div><strong>${E(x.name)}</strong><span>${E(x.value || '—')}</span></div>`).join('')}</div>` : ''}
    ${links.length ? `<h3>Ссылки</h3><div class="org-details-list">${links.map(x => `<div><strong>${E(x.type)}</strong><a href="${E(x.url)}" target="_blank" rel="noopener noreferrer">${E(x.label || x.url)}</a></div>`).join('')}</div>` : ''}
    <h3>Отзывы · сохранено ${reviews.length}</h3>
    <p class="small muted">${detail.reviews_complete ? 'Запрошенный лимит достигнут или карточка пройдена.' : 'Сбор частичный и может продолжиться при повторном обновлении карточки.'}</p>
    ${reviews.length ? `<div class="org-review-list">${reviews.map(x => `<article><div class="small muted">${E(x.review_date || 'Дата не указана')} · ${E(x.rating ?? 'без оценки')}</div><p>${E(x.text)}</p>${x.company_response ? `<div class="inline-note"><strong>Ответ организации:</strong> ${E(x.company_response)}</div>` : ''}</article>`).join('')}</div>` : '<p class="small muted">Подробные отзывы пока не сохранены.</p>'}
  </section>`);
  if (business.phones?.length) {
    node.querySelector('.detail-stack .panel')?.insertAdjacentHTML('beforeend',
      `<section class="inline-note" aria-labelledby="telegram-links-title"><div class="panel-head"><div><strong id="telegram-links-title">Проверить ТГ</strong>
      <p>Сформировать ручные ссылки по телефонам карточки. Приложение не обращается к Telegram и не проверяет наличие аккаунта.</p></div></div>
      <div class="toolbar"><button class="btn" data-show-telegram-links>Сформировать ссылки</button><button class="ghost" data-copy-all-telegram-links hidden>Копировать все</button></div>
      <p class="small muted">Если номер не открывается в Telegram, это может означать и скрытую видимость номера у владельца, а не отсутствие аккаунта.</p>
      <div class="list" data-telegram-link-list aria-live="polite" style="margin-top:10px"></div></section>`);
  }
};

document.addEventListener('change', event => {
  if (event.target.id !== 'debug-html') return;
  safe(async () => {
    await call('set_debug_html_enabled', event.target.checked);
    toast(event.target.checked ? 'Диагностическое сохранение включено' : 'Диагностическое сохранение выключено');
  });
});

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
      list.innerHTML = rows.length ? rows.map(b => businessRow(b)).join('')
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
  if (event.target.id === 'media-total-count') state.mediaTotalCount = Number(event.target.value);
});
document.addEventListener('change', event => {
  if (event.target.id === 'media-business') {
    state.mediaSectionsFor = null;
    state.mediaAvailableSections = [];
  }
  if (event.target.hasAttribute('data-section-all')) {
    state.mediaSections = event.target.checked ? ['__all__'] : [];
    document.querySelectorAll('[data-section]').forEach(input => { input.checked = event.target.checked; });
  } else if (event.target.hasAttribute('data-section')) {
    const available = state.mediaAvailableSections || [];
    const selected = (state.mediaSections || []).includes('__all__')
      ? [...available] : [...(state.mediaSections || [])];
    const label = event.target.dataset.section;
    const index = selected.indexOf(label);
    if (event.target.checked && index < 0) selected.push(label);
    if (!event.target.checked && index >= 0) selected.splice(index, 1);
    state.mediaSections = selected.length === available.length && available.length
      ? ['__all__'] : selected;
    const all = document.querySelector('[data-section-all]');
    if (all) all.checked = state.mediaSections.includes('__all__');
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
      if (old.kind === 'images') {
        if (old.params.selection) {
          payload.selection = old.params.selection;
          payload.per_section_limit = old.params.per_section_limit;
          payload.organization_limit = old.params.organization_limit;
        } else payload.sections = old.params.sections;
      }
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
  } else if (button.hasAttribute('data-show-telegram-links')) {
    event.stopImmediatePropagation();
    safe(async () => {
      const rows = await call('telegram_phone_links', state.businessId);
      const list = document.querySelector('[data-telegram-link-list]');
      if (!list) return;
      const valid = rows.filter(row => row.ok && row.url);
      list.innerHTML = rows.map((row, index) => row.ok
        ? `<div class="row row-head"><div><strong>${E(row.number)}</strong><div class="small muted">${E(row.url)}</div></div><div class="toolbar"><button class="btn" data-phone-open="${index}">Открыть</button><button class="ghost" data-phone-copy="${index}">Копировать</button></div></div>`
        : `<div class="row"><strong>${E(row.number)}</strong><div class="small muted">Не удалось сформировать ссылку: ${E(row.reason)}</div></div>`).join('') || empty('Телефоны не найдены');
      list.dataset.links = JSON.stringify(rows);
      const copyAll = document.querySelector('[data-copy-all-telegram-links]');
      if (copyAll) copyAll.hidden = valid.length < 1;
      toast(valid.length ? `Ссылок сформировано: ${valid.length}` : 'Подходящие мобильные номера не найдены');
    });
  } else if (button.hasAttribute('data-phone-open')) {
    event.stopImmediatePropagation();
    safe(async () => {
      const rows = JSON.parse(document.querySelector('[data-telegram-link-list]')?.dataset.links || '[]');
      const item = rows[Number(button.dataset.phoneOpen)];
      if (!item?.url) throw Error('Ссылка не найдена');
      await call('open_external', item.url);
    });
  } else if (button.hasAttribute('data-phone-copy') || button.hasAttribute('data-copy-all-telegram-links')) {
    event.stopImmediatePropagation();
    safe(async () => {
      const rows = JSON.parse(document.querySelector('[data-telegram-link-list]')?.dataset.links || '[]');
      const text = button.hasAttribute('data-copy-all-telegram-links')
        ? rows.filter(item => item.ok && item.url).map(item => item.url).join('\n')
        : rows[Number(button.dataset.phoneCopy)]?.url;
      if (!text) throw Error('Ссылка не найдена');
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else {
        const field = document.createElement('textarea'); field.value = text;
        field.style.position = 'fixed'; field.style.opacity = '0'; document.body.append(field);
        field.select(); document.execCommand('copy'); field.remove();
      }
      toast(button.hasAttribute('data-copy-all-telegram-links') ? 'Ссылки скопированы' : 'Ссылка скопирована');
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
  const sourceKey = state.mediaManual ? `url:${String(state.mediaUrl || '').trim()}` : `business:${state.mediaBusiness || ''}`;
  const discovered = state.mediaSectionsFor === sourceKey;
  const availableSections = discovered ? (state.mediaAvailableSections || []) : [];
  const selectedSections = discovered ? (state.mediaSections || ['__all__']) : [];
  const allSectionsSelected = selectedSections.includes('__all__');
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
    ${state.mediaKind === 'images' ? `<section class="panel" aria-label="Разделы галереи"><div class="toolbar">
      <button class="btn" type="button" data-discover-sections>Найти разделы</button>
      <span class="small muted">Список загружается только после нажатия.</span></div>
      ${discovered ? (availableSections.length ? `<p class="small muted">Найдено разделов: ${availableSections.length}. Видео исключено.</p>
        <label><input type="checkbox" data-section-all ${allSectionsSelected ? 'checked' : ''}> Все разделы</label>
        <div class="checkboxes">${availableSections.map(name => `<label><input type="checkbox" data-section="${E(name)}" ${(allSectionsSelected || selectedSections.includes(name)) ? 'checked' : ''}> ${E(name)}</label>`).join('')}</div>` :
        `<div class="inline-note">${E(state.mediaSectionMessage || 'Разделы не найдены. При запуске будет использована общая галерея.')}</div>`) :
        '<div class="inline-note">Сначала найдите разделы, чтобы выбрать все или отдельные категории.</div>'}
      <div class="form-grid" style="margin-top:12px"><label class="field">Фото на раздел<input id="media-count" type="number" min="1" max="50" value="${E(state.mediaCount || 10)}"></label>
      <label class="field">Фото на организацию<input id="media-total-count" type="number" min="1" max="200" value="${E(state.mediaTotalCount || 50)}"></label></div></section>` :
      '<p>Собрать названия товаров, цены и доступные фотографии в отдельную папку.</p>'}
    <div class="task-actions"><button class="btn primary" data-action="start-media" ${state.mediaKind === 'images' && !discovered ? 'disabled' : ''}>Запустить ${state.mediaKind === 'images' ? 'галерею' : 'товары'}</button>
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

// Media actions are intentionally separate from Telegram contact links.
document.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.resumeJob) {
    event.stopImmediatePropagation();
    safe(async () => {
      await call('media_resume', button.dataset.resumeJob);
      activityPoller.refresh();
      toast('Задание продолжено, сохранённые фотографии учтены');
      render();
    });
  } else if (button.dataset.mediaSource) {
    event.stopImmediatePropagation();
    state.mediaManual = button.dataset.mediaSource === 'manual';
    if (state.mediaManual) state.mediaBusiness = null;
    state.mediaSectionsFor = null;
    state.mediaAvailableSections = [];
    render();
  } else if (button.hasAttribute('data-discover-sections')) {
    event.stopImmediatePropagation();
    safe(async () => {
      const payload = state.mediaManual
        ? {url: document.querySelector('#media-url')?.value.trim()}
        : {business_id: document.querySelector('#media-business')?.value};
      const result = await call('media_discover_sections', payload);
      state.mediaAvailableSections = result.sections || [];
      state.mediaSectionsFor = state.mediaManual
        ? `url:${String(state.mediaUrl || '').trim()}` : `business:${payload.business_id || ''}`;
      state.mediaSections = ['__all__'];
      state.mediaSectionMessage = result.fallback
        ? 'Разделы не найдены. При запуске будет использована общая галерея.' : '';
      toast(result.fallback ? 'Разделы не найдены; будет использована общая галерея' : `Найдено разделов: ${result.sections.length}`);
      render();
    });
  } else if (button.dataset.action === 'start-media') {
    event.stopImmediatePropagation();
    safe(async () => {
      const payload = state.mediaManual
        ? {url: document.querySelector('#media-url')?.value.trim()}
        : {business_id: document.querySelector('#media-business')?.value};
      if (state.mediaKind === 'images') {
        const expectedSource = state.mediaManual
          ? `url:${String(state.mediaUrl || '').trim()}` : `business:${payload.business_id || ''}`;
        if (state.mediaSectionsFor !== expectedSource) {
          throw Error('Сначала нажмите «Найти разделы» для выбранной организации');
        }
        const selection = state.mediaSections || ['__all__'];
        payload.selection = {mode: selection.includes('__all__') ? 'all' : 'selected',
          names: selection.filter(name => name !== '__all__')};
        payload.per_section_limit = Number(document.querySelector('#media-count')?.value);
        payload.organization_limit = Number(document.querySelector('#media-total-count')?.value);
      }
      const job = await call('media_start', state.mediaKind, payload);
      toast(job.created ? 'Задание создано' : job.status === 'done' ? 'Результат уже готов. Используйте «Повторить парсинг».' : 'Задание уже выполняется');
      render();
    });
  } else if (button.dataset.openFolder || button.dataset.copyFolder) {
    event.stopImmediatePropagation();
    safe(async () => {
      const path = button.dataset.openFolder
        ? await call('media_open', button.dataset.openFolder, '')
        : await call('media_save', button.dataset.copyFolder, '');
      if (path && button.dataset.copyFolder) toast(`Папка сохранена: ${path}`);
    });
  }
}, true);

function showFirstRunWizard() {
  if (!state.config?.first_run || document.querySelector('#first-run-dialog')) return;
  const dialog = document.createElement('dialog');
  dialog.id = 'first-run-dialog';
  dialog.setAttribute('aria-labelledby', 'setup-title');
  dialog.innerHTML = `<section class="setup-card">
    <span class="section-kicker">ПЕРВЫЙ ЗАПУСК · ${E(state.config.version)}</span>
    <h2 id="setup-title">Добро пожаловать в LeadEqualMoney</h2>
    <p>Приложение работает локально: можно вести базу, искать организации и собирать медиа.</p>
    <div class="inline-note"><strong>Telegram не подключается.</strong> Кнопка «Проверить ТГ» только формирует ссылки из номеров карточки. Она не проверяет наличие аккаунта, не запрашивает контакты, не отправляет сообщения и не требует API ID или API hash.</div>
    <p class="small muted">Папка данных: ${E(state.config.data_dir)}</p>
    <div class="toolbar"><button class="btn primary" type="button" data-setup-continue>Начать работу</button></div>
  </section>`;
  document.body.append(dialog);
  dialog.addEventListener('cancel', event => event.preventDefault());
  dialog.showModal();
  dialog.querySelector('[data-setup-continue]').focus();
}

async function finishFirstRun(button) {
  try {
    await call('complete_setup');
    state.config.first_run = false;
    const dialog = button.closest('dialog');
    dialog?.close(); dialog?.remove();
    toast('Готово. Приложение не подключается к Telegram.');
  } catch (error) {
    toast(error.message || 'Не удалось завершить настройку.', true);
  }
}

window.addEventListener('pywebviewready', () => setTimeout(showFirstRunWizard, 250));
document.addEventListener('click', event => {
  const button = event.target.closest('[data-setup-continue]');
  if (!button) return;
  event.preventDefault(); event.stopImmediatePropagation();
  finishFirstRun(button);
}, true);

// The desktop Base is organized by parse run. Legacy cards without a run are
// surfaced virtually by the backend and are never rewritten during browsing.
state.baseLevel ||= 'niches';
state.baseNiche = state.baseNiche || null;
state.baseCity = state.baseCity || null;
state.baseRunId = state.baseRunId || null;

function baseBreadcrumb(parts) {
  const node = document.querySelector('#breadcrumb');
  if (!node) return;
  node.innerHTML = parts.map((part, index) => index < parts.length - 1
    ? `<button type="button" class="breadcrumb-action" data-base-level="${E(part.level)}">${E(part.label)}</button>`
    : `<span aria-current="page">${E(part.label)}</span>`).join('<span class="breadcrumb-separator" aria-hidden="true">›</span>');
}

function baseDate(value) {
  if (!value) return 'Ожидает запуска';
  return String(value).replace('T', ' ').slice(0, 16);
}

function baseExportButton(scope, title) {
  return `<button type="button" class="btn" data-base-export='${E(JSON.stringify(scope))}'>Экспорт ${E(title)}</button>`;
}

async function loadBaseRunBusinesses(append = false) {
  const list = document.querySelector('#base-business-list');
  if (!list || !state.baseRunId) return;
  if (!append) state.baseOffset = 0;
  const filters = {
    q: state.baseQuery || '', status: state.baseStatus || '', site_type: state.baseSiteType || '',
    limit: 50, offset: state.baseOffset || 0,
  };
  const rows = await safe(() => call('parse_run_businesses', state.baseRunId, filters));
  if (!rows) return;
  if (append && rows.length) list.insertAdjacentHTML('beforeend', rows.map(businessRow).join(''));
  else if (!append) list.innerHTML = rows.length ? rows.map(businessRow).join('') : empty('Организации не найдены');
  state.baseOffset = (state.baseOffset || 0) + rows.length;
  const more = document.querySelector('[data-base-more]');
  if (more) more.hidden = rows.length < 50;
}

businesses = async function (node) {
  state.baseLevel ||= 'niches';
  const level = state.baseLevel;
  if (level === 'niches') {
    state.baseNiche = state.baseCity = state.baseRunId = null;
    setHeader('База', 'Организации сгруппированы по нише, городу и запуску парсинга');
    baseBreadcrumb([{label: 'База', level: 'niches'}]);
    const items = await call('parse_niches');
    node.innerHTML = `<section class="panel base-hierarchy"><div class="panel-head"><div><span class="section-kicker">КАТЕГОРИИ</span><h2>Ниши</h2><p>Каждый новый поиск сохраняется отдельным запуском.</p></div><div class="toolbar"><button class="btn" data-action="import">Импорт XLSX</button><button class="btn" data-action="export">Экспорт всей базы</button><button class="btn primary" data-nav="search">Новый поиск</button></div></div>
      ${items.length ? '<div id="base-folder-grid" class="glass-folder-grid" aria-label="Ниши базы"></div>' : `<div class="base-empty">${empty('База пока пуста', 'Запустите первый парсинг или импортируйте организации из XLSX.')}<button class="btn primary" data-nav="search">Начать поиск</button></div>`}</section>`;
    if (items.length) glassFolderCards.mount(node.querySelector('#base-folder-grid'), items.map(item => ({
      key: item.key, title: item.name,
      summary: `${item.city_count} ${item.city_count === 1 ? 'город' : 'городов'} · ${item.organization_count} организаций`,
      moreCount: item.more_city_count || 0,
      openData: {'data-base-open-niche': item.key, 'data-base-label': item.name},
      exportScope: {level: 'niche', niche_key: item.key},
      files: (item.recent_cities || []).map(city => ({
        title: city.name, meta: `${city.organization_count} организаций${city.latest_at ? ` · ${baseDate(city.latest_at)}` : ''}`,
        data: {'data-base-folder-file-niche': item.key, 'data-base-folder-file-niche-label': item.name,
          'data-base-folder-file-city': city.key, 'data-base-folder-file-city-label': city.name},
      })),
    })));
    return;
  }

  if (level === 'cities') {
    setHeader(state.baseNicheLabel || 'Ниша', 'Города и число найденных организаций');
    baseBreadcrumb([{label: 'База', level: 'niches'}, {label: state.baseNicheLabel || 'Ниша', level: 'cities'}]);
    const [items, niches] = await Promise.all([call('parse_cities', state.baseNiche), call('parse_niches')]);
    const niche = niches.find(item => item.key === state.baseNiche);
    node.innerHTML = `<section class="panel base-hierarchy"><div class="panel-head"><div><span class="section-kicker">НИША</span><h2>${E(state.baseNicheLabel || niche?.name || 'Ниша')}</h2><p>${E(niche?.organization_count || 0)} организаций в этой нише</p></div>${baseExportButton({level: 'niche', niche_key: state.baseNiche}, 'ниши')}</div>
      ${items.length ? '<div id="base-folder-grid" class="glass-folder-grid" aria-label="Города этой ниши"></div>' : empty('Городов пока нет', 'Запустите поиск в этой нише.')}</section>`;
    if (items.length) glassFolderCards.mount(node.querySelector('#base-folder-grid'), items.map(item => ({
      key: `${state.baseNiche}:${item.key}`, title: item.name,
      summary: `${item.organization_count} организаций · ${item.run_count || 0} запусков`,
      moreCount: item.more_run_count || 0,
      openData: {'data-base-open-city': item.key, 'data-base-label': item.name},
      exportScope: {level: 'city', niche_key: state.baseNiche, city_key: item.key},
      files: (item.recent_runs || []).map(run => ({
        title: baseDate(run.display_at || run.started_at), meta: `${run.organization_count} организаций · ${activityLabel(run.status)}`,
        data: {'data-base-open-run': run.id, 'data-base-label': baseDate(run.display_at || run.started_at),
          'data-base-folder-run-city': item.key, 'data-base-folder-run-city-label': item.name},
      })),
    })));
    return;
  }

  if (level === 'runs') {
    setHeader(state.baseCityLabel || 'Город', 'Запуски парсинга, новые сверху');
    baseBreadcrumb([{label: 'База', level: 'niches'}, {label: state.baseNicheLabel || 'Ниша', level: 'cities'}, {label: state.baseCityLabel || 'Город', level: 'runs'}]);
    const items = await call('parse_runs', state.baseNiche, state.baseCity);
    node.innerHTML = `<section class="panel base-hierarchy"><div class="panel-head"><div><span class="section-kicker">ГОРОД</span><h2>${E(state.baseCityLabel || 'Город')}</h2><p>${items.length} запусков парсинга</p></div>${baseExportButton({level: 'city', niche_key: state.baseNiche, city_key: state.baseCity}, 'города')}</div>
      ${items.length ? `<div class="base-folder-grid">${items.map(item => `<article class="base-folder"><div><h3>${E(baseDate(item.display_at || item.started_at || item.created_at))}</h3><p>${E(item.organization_count)} организаций · ${E(activityLabel(item.status))}</p></div><button class="btn" data-base-open-run="${E(item.id)}" data-base-label="${E(baseDate(item.display_at || item.started_at || item.created_at))}">Открыть</button></article>`).join('')}</div>` : empty('Запусков пока нет', 'Начните новый поиск, чтобы сохранить первый запуск.')}</section>`;
    return;
  }

  if (level === 'businesses') {
    const [runs, cities] = await Promise.all([call('parse_runs', state.baseNiche, state.baseCity), call('parse_cities', state.baseNiche)]);
    const run = runs.find(item => item.id === state.baseRunId);
    const city = cities.find(item => item.key === state.baseCity);
    state.baseRunLabel ||= run ? baseDate(run.display_at || run.started_at || run.created_at) : 'Запуск';
    setHeader(state.baseRunLabel, 'Организации этого запуска · карточка, CRM и медиа');
    baseBreadcrumb([{label: 'База', level: 'niches'}, {label: state.baseNicheLabel || 'Ниша', level: 'cities'}, {label: state.baseCityLabel || city?.name || 'Город', level: 'runs'}, {label: state.baseRunLabel, level: 'businesses'}]);
    const scope = {level: 'run', niche_key: state.baseNiche, city_key: state.baseCity, run_id: state.baseRunId};
    node.innerHTML = `<section class="panel"><div class="panel-head"><div><span class="section-kicker">ЗАПУСК · ${E(run?.status ? activityLabel(run.status) : 'Архив')}</span><h2>${E(state.baseRunLabel)}</h2><p>${E(run?.organization_count ?? 0)} организаций</p></div>${baseExportButton(scope, 'запуска')}</div>
      <div class="toolbar base-filters"><input id="base-run-query" aria-label="Имя или адрес" placeholder="Имя или адрес" value="${E(state.baseQuery || '')}">
        <select id="base-run-status" aria-label="Фильтр по статусу"><option value="">Все статусы</option>${Object.entries(state.config?.statuses || {}).map(([key, label]) => `<option value="${E(key)}" ${state.baseStatus === key ? 'selected' : ''}>${E(label)}</option>`).join('')}</select>
        <select id="base-run-site-type" aria-label="Фильтр по типу сайта"><option value="">Все типы сайтов</option><option value="no_site" ${state.baseSiteType === 'no_site' ? 'selected' : ''}>Без сайта</option><option value="weak_site" ${state.baseSiteType === 'weak_site' ? 'selected' : ''}>Конструктор</option><option value="unavailable" ${state.baseSiteType === 'unavailable' ? 'selected' : ''}>Недоступен</option></select>
      </div><div id="base-business-list" class="list">${empty('Загрузка организаций…')}</div><button type="button" class="btn" data-base-more style="margin-top:12px">Показать ещё</button></section>`;
    await loadBaseRunBusinesses(false);
    const query = node.querySelector('#base-run-query');
    let timer;
    query.oninput = () => {
      state.baseQuery = query.value.trim();
      clearTimeout(timer);
      timer = setTimeout(() => loadBaseRunBusinesses(false), 250);
    };
    node.querySelector('#base-run-status').onchange = event => { state.baseStatus = event.target.value; loadBaseRunBusinesses(false); };
    node.querySelector('#base-run-site-type').onchange = event => { state.baseSiteType = event.target.value; loadBaseRunBusinesses(false); };
  }
};

const baseSetViewWithHierarchy = setView;
setView = function (view, extra = {}) {
  if (view === 'businesses') {
    state.baseLevel = 'niches';
    state.baseNiche = state.baseCity = state.baseRunId = null;
    state.baseQuery = state.baseStatus = state.baseSiteType = '';
    state.baseRunLabel = null;
  }
  return baseSetViewWithHierarchy(view, extra);
};

// Multi-city input creates one independently tracked job for each city.
const renderSearchWithCities = search;
search = async function (node) {
  await renderSearchWithCities(node);
  let city = node.querySelector('#search-form [name="city"]');
  if (city) {
    const area = document.createElement('textarea');
    area.name = 'city';
    area.required = true;
    area.rows = 2;
    area.placeholder = 'Казань, Самара';
    area.value = city.value;
    city.replaceWith(area);
    city = area;
    if (city.parentElement?.firstChild?.nodeType === Node.TEXT_NODE) {
      city.parentElement.firstChild.textContent = 'Города (через запятую, точку с запятой или новую строку)';
    }
  }
};

document.addEventListener('submit', event => {
  const form = event.target;
  if (form?.id !== 'search-form') return;
  event.preventDefault();
  event.stopImmediatePropagation();
  safe(async () => {
    const data = new FormData(form);
    const result = await call('search_start', {
      country: data.get('country'), city: data.get('city'), niche: data.get('niche'),
      count: Number(data.get('count')), types: data.getAll('types'),
      review_limit: Number(data.get('review_limit') || 0),
    });
    toast(`Создано запусков: ${result.count}`);
    render();
  });
}, true);

document.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.baseLevel) {
    event.preventDefault(); event.stopImmediatePropagation();
    state.baseLevel = button.dataset.baseLevel;
    if (state.baseLevel === 'niches') state.baseNiche = state.baseCity = state.baseRunId = null;
    else if (state.baseLevel === 'cities') state.baseCity = state.baseRunId = null;
    else if (state.baseLevel === 'runs') state.baseRunId = null;
    render();
  } else if (button.dataset.baseOpenNiche) {
    event.preventDefault(); event.stopImmediatePropagation();
    state.baseNiche = button.dataset.baseOpenNiche;
    state.baseNicheLabel = button.dataset.baseLabel;
    state.baseLevel = 'cities'; render();
  } else if (button.dataset.baseOpenCity) {
    event.preventDefault(); event.stopImmediatePropagation();
    state.baseCity = button.dataset.baseOpenCity;
    state.baseCityLabel = button.dataset.baseLabel;
    state.baseLevel = 'runs'; render();
  } else if (button.dataset.baseFolderFileCity) {
    event.preventDefault(); event.stopImmediatePropagation();
    state.baseNiche = button.dataset.baseFolderFileNiche;
    state.baseNicheLabel = button.dataset.baseFolderFileNicheLabel;
    state.baseCity = button.dataset.baseFolderFileCity;
    state.baseCityLabel = button.dataset.baseFolderFileCityLabel;
    state.baseLevel = 'runs'; render();
  } else if (button.dataset.baseOpenRun) {
    event.preventDefault(); event.stopImmediatePropagation();
    state.baseRunId = button.dataset.baseOpenRun;
    state.baseCity = button.dataset.baseFolderRunCity || state.baseCity;
    state.baseCityLabel = button.dataset.baseFolderRunCityLabel || state.baseCityLabel;
    state.baseRunLabel = button.dataset.baseLabel;
    state.baseLevel = 'businesses';
    state.baseQuery = state.baseStatus = state.baseSiteType = '';
    render();
  } else if (button.hasAttribute('data-base-more')) {
    event.preventDefault(); event.stopImmediatePropagation();
    loadBaseRunBusinesses(true);
  } else if (button.hasAttribute('data-base-export')) {
    event.preventDefault(); event.stopImmediatePropagation();
    safe(async () => {
      const scope = JSON.parse(button.dataset.baseExport);
      const result = await call('export_scope', scope);
      if (result) toast(`Экспортировано ${result.count} карточек`);
    });
  }
}, true);
