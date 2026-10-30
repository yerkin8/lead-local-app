// Stage 9: navigation history, scripts library, dense business card and inline CRM status.
const siteTypeLabels = {no_site:'Без сайта', weak_site:'Сайт на конструкторе', unavailable:'Сайт недоступен', other:'Обычный сайт'};
const statusTone = {not_started:'info', waiting:'warning', replied:'info', working:'warning', completed:'success', unsuitable:'error'};
state.navigation ||= [];
state.scriptSlug ||= null;
state.scriptBusiness ||= null;

function snapshotNavigation() {
  return {view:state.view, businessId:state.businessId, baseLevel:state.baseLevel,
    baseNiche:state.baseNiche, baseNicheLabel:state.baseNicheLabel,
    baseCity:state.baseCity, baseCityLabel:state.baseCityLabel,
    baseRunId:state.baseRunId, baseRunLabel:state.baseRunLabel,
    baseQuery:state.baseQuery, baseStatus:state.baseStatus, baseSiteType:state.baseSiteType,
    scroll:document.querySelector('.main')?.scrollTop || 0};
}
function samePlace(a,b){return a.view===b.view&&a.businessId===b.businessId&&a.baseLevel===b.baseLevel&&a.baseRunId===b.baseRunId}
function pushNavigation(){const next=snapshotNavigation();const last=state.navigation.at(-1);if(!last||!samePlace(last,next))state.navigation.push(next)}
async function goBack(){
  const previous=state.navigation.pop(); if(!previous)return;
  Object.assign(state,previous); await render();
  requestAnimationFrame(()=>{const main=document.querySelector('.main');if(main)main.scrollTop=previous.scroll||0});
}
function syncBackButton(){
  let button=document.querySelector('#app-back');
  if(!button){button=document.createElement('button');button.id='app-back';button.type='button';button.className='ghost';button.textContent='← Назад';document.querySelector('.top-actions')?.prepend(button);button.onclick=goBack}
  button.hidden=!state.navigation.length;
}

const stage9SetView=setView;
setView=function(view,extra={}){if(view!==state.view||extra.businessId!==undefined)pushNavigation();return stage9SetView(view,extra)};
const stage9Render=render;
render=async function(){if(state.view==='scripts')await scriptsPage(document.querySelector('#content'));else if(state.view==='telegram')await telegramPage(document.querySelector('#content'));else await stage9Render();syncBackButton()};

function statusSelect(b){return `<label class="status-control status-${E(statusTone[b.status]||'info')}"><span class="sr-only">Статус работы: ${E(b.name)}</span><select data-inline-status="${E(b.id)}" aria-label="Статус работы: ${E(b.name)}">${Object.entries(state.config.statuses).map(([key,label])=>`<option value="${E(key)}" ${key===b.status?'selected':''}>${E(label)}</option>`).join('')}</select></label>`}
businessRow=function(b){return `<article class="row business-row" data-business-card="${E(b.id)}" tabindex="0" role="link" aria-label="Открыть карточку: ${E(b.name)}"><div class="row-head"><div class="business-row-copy"><span class="row-title">${E(b.name)}</span><div class="muted small">${E(b.address||'Адрес не указан')}</div><div class="chips"><span class="chip">${E(siteTypeLabels[b.site_type]||'Тип сайта не определён')}</span>${b.socials?.some(x=>String(x.url||'').includes('t.me/'))?'<span class="chip purple">Telegram</span>':''}</div></div><div class="row-actions">${statusSelect(b)}<button class="btn" data-media="${E(b.id)}">Медиа</button></div></div></article>`};

async function scriptsPage(node){
  setHeader('Скрипты','Редактор и подборщик текстов. Сообщения отсюда не отправляются.');
  const rows=await call('templates');if(!state.scriptSlug)state.scriptSlug=rows[0]?.slug;
  const current=rows.find(x=>x.slug===state.scriptSlug)||rows[0];
  node.innerHTML=`<div class="scripts-layout"><section class="panel script-list"><span class="section-kicker">БИБЛИОТЕКА · ${rows.length}</span><h2>Сценарии</h2><p>Выберите текст для просмотра и редактирования.</p><div class="script-buttons">${rows.map(x=>`<button type="button" class="${x.slug===current?.slug?'active':''}" data-script-select="${E(x.slug)}">${E(x.title)}</button>`).join('')}</div></section><section class="panel"><span class="section-kicker">ТОЛЬКО РЕДАКТОР</span><h2>${E(current?.title||'Скрипт')}</h2><p>Поля в квадратных скобках заполните перед использованием.</p><form id="desktop-script-form"><input type="hidden" name="slug" value="${E(current?.slug||'')}"><textarea name="body" aria-label="Текст скрипта">${E(current?.body||'')}</textarea><div class="toolbar"><button class="btn primary">Сохранить</button><button type="button" class="btn" data-copy-script>Копировать</button></div></form></section></div>`;
}

detail=async function(node){
  const [b,scripts]=await Promise.all([call('business',state.businessId),call('business_scripts',state.businessId)]);if(!b){goBack();return}
  const items=scripts.items||[];const selected=items.find(x=>x.slug===(state.scriptBusiness||scripts.preferred))||items[0];state.scriptBusiness=selected?.slug;
  setHeader(b.name,'Карточка организации · данные, предложение сообщения и статус');
  const d=b.details||{};const links=[...(b.links||[]),...(b.socials||[])];
  node.innerHTML=`<div class="business-detail-grid"><section class="panel"><div class="panel-head"><div><span class="section-kicker">ОРГАНИЗАЦИЯ</span><h2>${E(b.name)}</h2><p>${E(b.address||'Адрес не указан')}</p></div><button class="btn" data-media="${E(b.id)}">Медиа</button></div><dl class="facts"><div><dt>Тип сайта</dt><dd>${E(siteTypeLabels[b.site_type]||'Обычный сайт')}${b.site_engine?' · '+E(b.site_engine):''}</dd></div><div><dt>Сайт</dt><dd>${b.website?`<a href="${E(b.website)}">${E(b.website)}</a>`:'Не указан'}</dd></div><div><dt>Рейтинг</dt><dd>${E(b.rating??'—')} · ${E(b.reviews??0)} отзывов</dd></div><div><dt>Рубрики</dt><dd>${E((d.categories||[]).join(', ')||b.category||'Не указаны')}</dd></div></dl><h3>Телефоны</h3><div class="toolbar">${(b.phones||[]).map(p=>`<span class="chip">${E(p)}</span>`).join('')||'<span class="muted">Не указаны</span>'}<button class="btn" data-open-telegram="${E(b.id)}">Проверить ТГ</button></div><h3>Ссылки и соцсети</h3><div class="detail-links">${links.length?links.map(x=>`<a href="${E(x.url)}">${E(x.label||x.type||x.url)}</a>`).join(''):'<span class="muted">Не найдены</span>'}</div><h3>Режим работы</h3><div class="org-details-list">${(b.hours||[]).map(x=>`<div><strong>${E(x.weekday)}</strong><span>${x.is_closed?'Закрыто':E(x.raw||`${x.opens||'—'}–${x.closes||'—'}`)}</span></div>`).join('')||'<span class="muted">Не указан</span>'}</div><h3>Особенности</h3><div class="chips">${(b.features||[]).map(x=>`<span class="chip">${E(x.name)}${x.value?' · '+E(x.value):''}</span>`).join('')||'<span class="muted">Не сохранены</span>'}</div></section><section class="panel proposal-panel"><span class="section-kicker">ПРЕДЛОЖЕНИЕ СООБЩЕНИЯ</span><h2>${E(selected?.title||'Скрипт')}</h2><label class="field">Вариант<select id="business-script-select">${items.map(x=>`<option value="${E(x.slug)}" ${x.slug===selected?.slug?'selected':''}>${E(x.title)}</option>`).join('')}</select></label><label class="field">Текст<textarea id="business-script-text">${E(selected?.text||'')}</textarea></label><button class="btn" data-copy-business-script>Копировать</button><hr class="divider"><h2>Статус работы</h2>${statusSelect(b)}<label class="field">Заметка<textarea id="business-note">${E(b.note||'')}</textarea></label><button class="btn primary" data-save-business-note>Сохранить заметку</button></section></div><section class="panel"><h2>Отзывы</h2><div class="org-review-list">${(b.review_items||[]).map(x=>`<article><div class="small muted">${E(x.review_date||'Дата не указана')} · ${E(x.rating??'без оценки')}</div><p>${E(x.text)}</p></article>`).join('')||'<p>Отзывы пока не сохранены.</p>'}</div></section>`;
}

async function telegramPage(node){
  const b=await call('business',state.businessId);setHeader('Проверить ТГ',b?.name||'Организация');
  const rows=await call('telegram_phone_links',state.businessId);
  node.innerHTML=`<section class="panel"><span class="section-kicker">РУЧНЫЕ ССЫЛКИ</span><h2>${E(b?.name||'Организация')}</h2><p>Приложение формирует ссылки из номера, не проверяет наличие аккаунта и ничего не отправляет.</p><div class="list telegram-links">${rows.map((x,i)=>`<div class="row row-head"><div><strong>${E(x.number)}</strong><div class="small muted">${E(x.ok?x.url:x.reason)}</div></div>${x.ok?`<div class="toolbar"><button class="btn" data-tg-open="${i}">Открыть</button><button class="ghost" data-tg-copy="${i}">Копировать</button></div>`:''}</div>`).join('')||empty('Телефоны не найдены')}</div></section>`;node.dataset.telegramRows=JSON.stringify(rows);
}

document.addEventListener('click',event=>{
  const card=event.target.closest('[data-business-card]');if(card&&!event.target.closest('button,select,a,input,textarea,label')){pushNavigation();state.businessId=card.dataset.businessCard;state.view='detail';render();return}
  const button=event.target.closest('button');if(!button)return;
  if(button.dataset.scriptSelect){state.scriptSlug=button.dataset.scriptSelect;render()}
  else if(button.dataset.openTelegram){pushNavigation();state.businessId=button.dataset.openTelegram;state.view='telegram';render()}
  else if(button.hasAttribute('data-copy-script'))copyText(document.querySelector('#desktop-script-form textarea')?.value)
  else if(button.hasAttribute('data-copy-business-script'))copyText(document.querySelector('#business-script-text')?.value)
  else if(button.hasAttribute('data-save-business-note'))safe(async()=>{await call('business_save',state.businessId,null,document.querySelector('#business-note').value);toast('Заметка сохранена')})
  else if(button.dataset.tgOpen||button.dataset.tgCopy){const rows=JSON.parse(document.querySelector('#content')?.dataset.telegramRows||'[]');const i=Number(button.dataset.tgOpen??button.dataset.tgCopy);const url=rows[i]?.url;if(button.dataset.tgOpen!==undefined)safe(()=>call('open_external',url));else copyText(url)}
},true);
document.addEventListener('keydown',event=>{const card=event.target.closest?.('[data-business-card]');if(card&&event.key==='Enter'){event.preventDefault();card.click()}if(event.altKey&&event.key==='ArrowLeft'){event.preventDefault();goBack()}});
document.addEventListener('change',event=>{
  if(event.target.id==='business-script-select'){state.scriptBusiness=event.target.value;render();return}
  if(!event.target.dataset.inlineStatus)return;event.stopImmediatePropagation();const select=event.target;const old=select.dataset.previous||'';select.disabled=true;safe(async()=>{await call('business_save',select.dataset.inlineStatus,select.value,null);toast('Статус сохранён');const wrap=select.closest('.status-control');wrap.className=`status-control status-${statusTone[select.value]||'info'}`}).finally(()=>select.disabled=false);
},true);
document.addEventListener('focusin',event=>{if(event.target.dataset.inlineStatus)event.target.dataset.previous=event.target.value});
document.addEventListener('submit',event=>{if(event.target.id!=='desktop-script-form')return;event.preventDefault();event.stopImmediatePropagation();safe(async()=>{const data=new FormData(event.target);await call('template_save',data.get('slug'),data.get('body'));toast('Скрипт сохранён')})},true);

async function copyText(value){if(!value)return toast('Нет текста для копирования',true);try{await navigator.clipboard.writeText(value);toast('Скопировано')}catch(error){toast('Не удалось скопировать',true)}}

for(const type of ['click'])document.addEventListener(type,event=>{const b=event.target.closest('button');if(b&&(b.dataset.baseOpenNiche||b.dataset.baseOpenCity||b.dataset.baseFolderFileCity||b.dataset.baseOpenRun||b.dataset.baseLevel))pushNavigation()},true);
window.addEventListener('error',event=>{bridge()?.frontend_error?.(event.message,event.filename,event.lineno,event.colno)});
const originalConsoleError=console.error.bind(console);console.error=(...args)=>{originalConsoleError(...args);bridge()?.frontend_error?.(args.map(String).join(' '),'console',0,0)};
