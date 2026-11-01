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
  const retry = ['done', 'failed', 'blocked'].includes(job.status)
    ? `<button class="ghost" data-repeat-job="${E(job.id)}">Повторить парсинг</button>` : '';
  const resume = job.kind === 'images' && ['failed', 'blocked'].includes(job.status)
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

// Search jobs: «Остановить» for queued and running ones, the review pass and the speed are shown.
const JOB_LABELS = { queued: 'В очереди', running: 'В работе', done: 'Готово', failed: 'Ошибка',
  blocked: 'Капча — Яндекс ограничил, повторите позже', deleting: 'Удаление', stopped: 'Остановлен',
  interrupted: 'Прервано' };
jobBadge = function (job) {
  const stopping = job.status === 'running' && job.progress?.stopping;
  const label = stopping ? 'Останавливается…' : (JOB_LABELS[job.status] || job.status);
  const tone = job.status === 'done' ? 'green' : ['failed', 'blocked'].includes(job.status) ? 'warn' : '';
  return `<span class="chip ${tone}">${E(label)}</span>`;
};
function searchStage(job) {
  const x = job.progress || {};
  if (job.status !== 'running') return '';
  if (x.phase === 'details' && x.details_total) {
    return `Карточки найдены, догружаю отзывы и фото: ${E(x.details_done ?? 0)} из ${E(x.details_total)}`;
  }
  return x.per_minute ? `Ищу карточки · примерно ${E(x.per_minute)} в минуту` : 'Ищу карточки';
}
searchJob = function (j) {
  const p = j.params || {}, r = j.result || {}, x = j.progress || {};
  const active = ['queued', 'running'].includes(j.status);
  const stop = active && !x.stopping ? `<button class="ghost" data-stop-job="${E(j.id)}">Остановить</button>` : '';
  const stage = searchStage(j);
  const note = j.status === 'stopped'
    ? `Остановлен. Найденные до остановки (${E(r.matched ?? x.matched ?? 0)}) — в «Лидах».`
    : j.error && j.status !== 'stopped' ? '' : r.reason || '';
  return `<div class="row" data-activity-kind="search" data-activity-id="${E(j.id)}"><div class="row-head"><div>
    <span class="row-title">${E(p.niche)} · ${E(p.city)}</span>
    <div class="muted small">Цель ${E(p.count)} новых · отзывов ${E(p.review_limit ?? 20)} · ${E(LeadNav.localTime(j.created_at))}</div></div>
    <div class="row-actions">${jobBadge(j)}${stop}<button class="btn" data-search-results="${E(j.id)}">Результаты</button></div></div>
    <div class="chips"><span class="chip green">Новых ${E(r.matched ?? x.matched ?? 0)}</span><span class="chip">Уже в базе ${E(r.skipped_existing ?? x.skipped_existing ?? 0)}</span><span class="chip">Проверены ранее ${E(r.skipped_checked ?? x.skipped_checked ?? 0)}</span><span class="chip">Просмотрены ${E(r.scanned ?? x.scanned ?? 0)}</span><span class="chip">Ошибки ${E(r.errors ?? x.errors ?? 0)}</span></div>
    ${stage ? `<div class="inline-note">${stage}</div>` : ''}
    ${j.error && j.status !== 'stopped' ? `<div class="inline-note error-note">${E(j.error)}</div>` : note ? `<div class="inline-note">${E(note)}</div>` : ''}</div>`;
};
document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-stop-job]');
  if (!button) return;
  event.stopPropagation();
  button.disabled = true;
  safe(async () => {
    const answer = await call('search_stop', button.dataset.stopJob);
    toast(answer?.status === 'stopped' ? 'Поиск остановлен' : 'Останавливаю поиск — найденное сохранится');
    if (state.view === 'search') await render();
  });
}, true);

// «Настройки» are built in settings.js.

const baseSearch = search;
search = async function (node) {
  await baseSearch(node);
  node.querySelector('#search-form .form-grid')?.insertAdjacentHTML('beforeend',
    `<label class="field">Отзывы на организацию<input name="review_limit" type="number" min="0" max="100" value="${E(state.prefs?.review_limit ?? 20)}"><small class="muted">0 отключает сбор; максимум 100 · по умолчанию из Настроек</small></label>`);
};

const baseCall = call;
call = async function (method, ...args) {
  if (method === 'search_start' && args[0] && args[0].review_limit === undefined) {
    args[0].review_limit = Number(document.querySelector('#search-form [name="review_limit"]')?.value ?? state.prefs?.review_limit ?? 20);
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
  } else if (button.dataset.deleteMedia) {
    event.stopImmediatePropagation();
    safe(async () => {
      if (!await LEM.platform.confirm('Удалить этот архив и запись задания? Организация останется в базе.')) return;
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
  } else if (button.hasAttribute('data-replace-database')) {
    event.stopImmediatePropagation();
    safe(async () => {
      if (!await LEM.platform.confirm('Заменить рабочую базу другим файлом? Текущая база сначала сохранится в резервную копию, затем выбранный файл станет рабочей базой. Объединения не будет.')) return;
      const result = await call('replace_database');
      if (result) toast(`База заменена: ${result.businesses} организаций. Резервная копия сохранена`);
      state.config = await call('bootstrap');
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
    <div class="inline-note"><strong>Telegram подключается только в разделе «Рассылка».</strong> Там вы вводите свои api_id и api_hash и входите в свой аккаунт; ключи и сессии шифруются и не покидают этот компьютер. Кнопка «Проверить ТГ» только формирует ссылки из номеров карточки и ключей не требует.</div>
    <label class="field">Ключ OpenRouter для ИИ-чата <span class="muted small">· необязательно, можно позже в «Настройках»</span>
      <input id="setup-ai-key" type="password" autocomplete="off" spellcheck="false" placeholder="sk-or-v1-…"></label>
    <p class="small muted">Ключ свой у каждого пользователя: создайте его на openrouter.ai/settings/keys. Он шифруется для этой учётной записи Windows.</p>
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
    const keyInput = document.querySelector('#setup-ai-key');
    const key = keyInput?.value.trim();
    if (keyInput) keyInput.value = '';
    if (key) await call('ai_key_save', key);  // a wrong key stops here with a readable error; setup stays open
    await call('complete_setup');
    state.config.first_run = false;
    const dialog = button.closest('dialog');
    dialog?.close(); dialog?.remove();
    toast('Готово. Рассылка в Telegram — в разделе «Рассылка».');
  } catch (error) {
    toast(error.message || 'Не удалось завершить настройку.', true);
  }
}

if (LEM.kind === 'desktop') LEM.onReady(() => setTimeout(showFirstRunWizard, 250));
document.addEventListener('click', event => {
  const button = event.target.closest('[data-setup-continue]');
  if (!button) return;
  event.preventDefault(); event.stopImmediatePropagation();
  finishFirstRun(button);
}, true);

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
      city.parentElement.firstChild.textContent = 'Города';
      city.insertAdjacentHTML('afterend', '<small class="muted">Через запятую, точку с запятой или с новой строки</small>');
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
