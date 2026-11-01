const tg = window.Telegram?.WebApp;
tg?.ready(); tg?.expand();
if(tg?.colorScheme)document.documentElement.dataset.theme=tg.colorScheme;
const DEV = location.hostname === '127.0.0.1' || location.hostname === 'localhost';
const state = {config:null,page:'search',businesses:[],moreBusiness:false,jobs:[],detail:null,poll:null};
const $ = s => document.querySelector(s);
const ACCENTS={blue:['Синий','#326bef'],red:['Красный','#df3859'],orange:['Оранжевый','#df690d'],yellow:['Жёлтый','#e4b400'],green:['Зелёный','#13a875'],cyan:['Голубой','#00a4be'],indigo:['Индиго','#5a5ce0'],violet:['Фиолетовый','#a341ca']};
function setAccent(key){if(!ACCENTS[key])return;document.documentElement.dataset.accent=key;try{localStorage.setItem('accent',key)}catch{}document.querySelectorAll('.swatch').forEach(x=>x.classList.toggle('selected',x.dataset.accent===key))}
$('#theme-swatches').innerHTML=Object.entries(ACCENTS).map(([key,[name,color]])=>`<button type="button" class="swatch" data-accent="${key}" style="--swatch:${color}" aria-label="${name}" title="${name}"></button>`).join('');
document.querySelectorAll('.swatch').forEach(x=>x.addEventListener('click',()=>setAccent(x.dataset.accent)));
let savedAccent='blue';try{savedAccent=localStorage.getItem('accent')||'blue'}catch{}setAccent(savedAccent);
$('#theme-button').addEventListener('click',()=>$('#theme-panel').classList.toggle('hidden'));
const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const href = v => {try {const u=new URL(v);return ['http:','https:','tel:'].includes(u.protocol)?u.href:'#'}catch{return '#'}};
const labelType = t => state.config?.site_types?.[t] || ({other:'Обычный сайт'}[t]) || t;
const labelStatus = s => state.config?.statuses?.[s] || s;
function toast(text){const el=$('#toast');el.textContent=text;el.classList.remove('hidden');setTimeout(()=>el.classList.add('hidden'),3500)}
async function api(path,method='GET',body){
  const headers={'X-Telegram-Init-Data':tg?.initData||''};
  if(DEV)headers['X-Dev-Auth']='1';
  if(body!==undefined)headers['Content-Type']='application/json';
  const res=await fetch(path,{method,headers,body:body===undefined?undefined:JSON.stringify(body)});
  let data;try{data=await res.json()}catch{data={detail:'Ошибка сервера'}}
  if(!res.ok)throw new Error(typeof data.detail==='string'?data.detail:'Не удалось выполнить запрос');
  return data;
}
function option(value,text){return `<option value="${esc(value)}">${esc(text)}</option>`}
function empty(text){return `<div class="empty">${esc(text)}</div>`}
async function init(){
  try{state.config=await api('/api/config');renderConfig();await loadFilterOptions();await loadJobs()}catch(e){$('#search-jobs').innerHTML=empty(e.message);toast(e.message)}
}
function renderConfig(){
  $('#country').innerHTML=state.config.countries.map(x=>option(x,x)).join('');
  $('#type-chips').innerHTML=Object.entries(state.config.site_types).map(([key,title],i)=>`<label class="chip"><input type="checkbox" name="types" value="${esc(key)}" ${i<2?'checked':''}>${esc(title)}</label>`).join('');
  $('#filter-type').innerHTML=option('','Все типы')+Object.entries(state.config.site_types).map(([k,v])=>option(k,v)).join('')+option('other','Обычный сайт');
  $('#filter-status').innerHTML=option('','Все статусы')+Object.entries(state.config.statuses).map(([k,v])=>option(k,v)).join('');
}
function showPage(page,load=true){
  state.page=page;
  document.querySelectorAll('.page').forEach(x=>x.classList.toggle('active',x.id==='page-'+page));
  document.querySelectorAll('.bottom-nav button').forEach(x=>x.classList.toggle('selected',x.dataset.page===page));
  $('#page-title').textContent={search:'Поиск',base:'База',stats:'Статистика',scripts:'Скрипты'}[page];
  if(load){if(page==='base')loadFilterOptions().then(()=>loadBusinesses()).catch(e=>toast(e.message));if(page==='stats')loadStats();if(page==='scripts')loadTemplates();if(page==='search')loadJobs()}
  window.scrollTo({top:0,behavior:'instant'});
}
document.querySelectorAll('.bottom-nav button').forEach(x=>x.addEventListener('click',()=>showPage(x.dataset.page)));
$('#search-form').addEventListener('submit',async e=>{
  e.preventDefault();const form=e.currentTarget;const fd=new FormData(form);const types=fd.getAll('types');
  if(!types.length){toast('Выберите хотя бы один тип сайта');return}
  const btn=$('#start-button');btn.disabled=true;
  try{await api('/api/search-jobs','POST',{niche:fd.get('niche'),country:fd.get('country'),city:fd.get('city'),count:Number(fd.get('count')),types});toast('Поиск добавлен в очередь');await loadJobs()}
  catch(err){toast(err.message)}finally{btn.disabled=false}
});
$('#refresh-jobs').addEventListener('click',loadJobs);
async function loadJobs(){
  try{state.jobs=await api('/api/jobs?kind=search');renderJobs();
    clearTimeout(state.poll);if(state.jobs.some(x=>['queued','running'].includes(x.status)))state.poll=setTimeout(loadJobs,5000);
  }catch(e){$('#search-jobs').innerHTML=empty(e.message)}
}
function renderJobs(){
  $('#search-jobs').innerHTML=state.jobs.length?state.jobs.map(j=>{
    const p=j.progress||{}, done=j.result?.matched??p.matched??0,target=j.params.count;
    const status=j.status==='done'&&done===0?'Нет подходящих':({queued:'В очереди',running:'Идёт поиск',done:'Готово',failed:'Остановлено'}[j.status]||j.status);
    const badge=j.status==='done'?'good':j.status==='failed'?'warn':'';
    return `<article class="job-card"><div class="job-head"><strong>${esc(j.params.niche)} · ${esc(j.params.city)}</strong><span class="badge ${badge}">${status}</span></div>
      <p>${done} из ${target} подходящих · просмотрено ${j.result?.scanned??p.scanned??0}</p>
      <div class="progress"><span style="width:${Math.min(100,100*done/target)}%"></span></div>
      ${j.result?.reason?`<p>${esc(j.result.reason)}</p>`:''}${j.error?`<p>${esc(j.error)}</p>`:''}
      <div class="card-actions"><button data-job="${j.id}">Показать найденных</button></div></article>`
  }).join(''):empty('Поисков пока не было. Заполните форму выше.');
  document.querySelectorAll('[data-job]').forEach(b=>b.onclick=()=>openJobResults(b.dataset.job));
}
async function openJobResults(id){try{const rows=await api(`/api/jobs/${id}/businesses`);showPage('base',false);state.businesses=rows;state.moreBusiness=false;renderBusinesses();loadFilterOptions();toast(`Найдено: ${rows.length}`)}catch(e){toast(e.message)}}
const filters=['q','niche','country','city','type','status'];
filters.forEach(k=>$('#filter-'+k).addEventListener(k==='q'?'input':'change',()=>{clearTimeout(state.filterTimer);state.filterTimer=setTimeout(loadBusinesses,250)}));
$('#load-more').addEventListener('click',()=>loadBusinesses(false));
async function loadBusinesses(reset=true){
  const seq=++state.businessSeq;
  try{const params=new URLSearchParams();for(const k of filters){const value=$('#filter-'+k).value;if(value)params.set(k==='type'?'site_type':k,value)}
    params.set('limit','50');params.set('offset',reset?'0':String(state.businesses.length));
    const rows=await api('/api/businesses?'+params);if(seq!==state.businessSeq)return;state.businesses=reset?rows:state.businesses.concat(rows);state.moreBusiness=rows.length===50;renderBusinesses()}
  catch(e){$('#business-list').innerHTML=empty(e.message)}
}
async function loadFilterOptions(){
  const all=await api('/api/filters');
  for(const key of ['niche','country','city']){const node=$('#filter-'+key),current=node.value;
    const title={niche:'Все ниши',country:'Все страны',city:'Все города'}[key];
    node.innerHTML=option('',title)+(all[key]||[]).map(v=>option(v,v)).join('');node.value=current;
  }
}
function renderBusinesses(){
  $('#business-count').textContent=`${state.businesses.length}${state.moreBusiness?'+':''}`;
  $('#load-more').classList.toggle('hidden',!state.moreBusiness);
  $('#business-list').innerHTML=state.businesses.length?state.businesses.map(b=>{
    const first=(b.tags||[])[0]||{};
    const phone=b.phones?.[0];
    const reviewCount=Number(b.reviews);
    const reviewTone=b.reviews==null||!Number.isFinite(reviewCount)?'neutral':reviewCount<100?'low':reviewCount>=200?'high':'medium';
    const statusClass=Object.hasOwn(state.config.statuses,b.status)?b.status:'not_started';
    return `<article class="business-card status-${statusClass}" data-business="${esc(b.id)}" tabindex="0" aria-label="Открыть карточку: ${esc(b.name)}"><div class="business-head"><strong>${esc(b.name)}</strong><span class="badge ${b.site_type==='unavailable'?'warn':''}">${esc(labelType(b.site_type))}</span></div>
      <div class="meta">${esc(first.niche||'')} · ${esc(first.city||'')}</div>
      <div class="business-signals"><span class="review-count review-${reviewTone}">★ ${esc(b.rating??'—')} · ${esc(b.reviews??'—')} отзывов</span><span class="business-address">${esc(b.address||'Адрес не указан')}</span></div>
      <div class="card-actions">
        ${(b.socials||[]).slice(0,3).map(s=>`<a href="${esc(href(s.url))}" target="_blank" rel="noopener">${esc(s.label)}</a>`).join('')}
        <a href="${esc(href(b.map_url))}" target="_blank" rel="noopener">Карта</a>
        ${b.website&&b.site_type==='weak_site'?`<a href="${esc(href(b.website.includes('://')?b.website:'https://'+b.website))}" target="_blank" rel="noopener">Сайт</a>`:''}
      </div><div class="business-quick-actions"><label>Статус<select class="business-status" data-status-business="${esc(b.id)}" aria-label="Статус: ${esc(b.name)}">${Object.entries(state.config.statuses).map(([key,title])=>`<option value="${esc(key)}" ${key===b.status?'selected':''}>${esc(title)}</option>`).join('')}</select></label>${phone?`<a href="tel:${esc(phone.replace(/[^+\d]/g,''))}">Позвонить</a>`:''}</div></article>`
  }).join(''):empty('По этим фильтрам бизнесов пока нет.');
  document.querySelectorAll('.business-card[data-business]').forEach(card=>{
    card.onclick=e=>{if(!e.target.closest('a,button,select,label,input,textarea'))openBusiness(card.dataset.business)};
    card.onkeydown=e=>{if(e.target===card&&['Enter',' '].includes(e.key)){e.preventDefault();openBusiness(card.dataset.business)}};
  });
  document.querySelectorAll('[data-status-business]').forEach(select=>select.onchange=async()=>{
    const previous=state.businesses.find(b=>String(b.id)===select.dataset.statusBusiness)?.status;
    const next=select.value;
    select.disabled=true;
    try{
      await api(`/api/businesses/${encodeURIComponent(select.dataset.statusBusiness)}`,'PATCH',{status:next});
      const business=state.businesses.find(b=>String(b.id)===select.dataset.statusBusiness);
      if(business)business.status=next;
      select.closest('.business-card').className=`business-card status-${next}`;
      toast('Статус обновлён');
      if($('#filter-status').value&&$('#filter-status').value!==next)await loadBusinesses();
    }catch(e){select.value=previous||'not_started';toast(e.message)}
    finally{select.disabled=false}
  });
}
async function openBusiness(id){
  try{const b=await api('/api/businesses/'+id);state.detail=b;const scripts=await api(`/api/businesses/${id}/scripts`);renderDetail(b,scripts)}catch(e){toast(e.message)}
}
function renderDetail(b,scripts){
  $('#detail-content').innerHTML=`<div class="detail-top"><div><h2>${esc(b.name)}</h2><div class="meta">${esc(b.category||'')} · ${esc(b.city||'')}</div></div><button class="mini-button" id="close-detail" aria-label="Закрыть">Закрыть</button></div>
    <div class="meta">${esc(labelType(b.site_type))}${b.site_engine?' · '+esc(b.site_engine):''} · ${b.rating??'—'} ★ · ${b.reviews??'—'} отзывов<br>${esc(b.address||'')}</div>
    <div class="card-actions">${(b.phones||[]).map(p=>`<a href="tel:${esc(p.replace(/[^+\d]/g,''))}">${esc(p)}</a>`).join('')}
      ${(b.socials||[]).map(s=>`<a href="${esc(href(s.url))}" target="_blank" rel="noopener">${esc(s.label)}</a>`).join('')}
      <a href="${esc(href(b.map_url))}" target="_blank" rel="noopener">На карте</a>
      ${b.website?`<a href="${esc(href(b.website.includes('://')?b.website:'https://'+b.website))}" target="_blank" rel="noopener">Сайт</a>`:''}</div>
    <label>Статус<select id="detail-status">${Object.entries(state.config.statuses).map(([k,v])=>`<option value="${k}" ${b.status===k?'selected':''}>${esc(v)}</option>`).join('')}</select></label>
    <label>Заметка<textarea id="detail-note" placeholder="Итоги общения, следующий шаг…">${esc(b.note||'')}</textarea></label>
    <button id="save-business" class="primary" style="margin-top:12px">Сохранить</button>
    <h3>Заготовки для этого бизнеса</h3><p class="meta">Проверьте факты и замените поля в [скобках] перед копированием.</p>${scripts.map(s=>`<div class="script-item"><strong>${esc(s.title)}</strong><textarea aria-label="${esc(s.title)}">${esc(s.text)}</textarea><button class="mini-button copy-script">Копировать</button></div>`).join('')}
    <h3>История статусов</h3>${(b.history||[]).length?(b.history||[]).map(h=>`<div class="simple-row"><span>${esc(labelStatus(h.old_status))} → ${esc(labelStatus(h.new_status))}</span><span class="muted">${esc(new Date(h.changed_at).toLocaleDateString('ru-RU'))}</span></div>`).join(''):empty('Статус ещё не менялся.')}`;
  $('#detail-backdrop').classList.remove('hidden');$('#close-detail').onclick=closeDetail;
  $('#save-business').onclick=async()=>{try{const next=await api(`/api/businesses/${b.id}`,'PATCH',{status:$('#detail-status').value,note:$('#detail-note').value});state.detail=next;toast('Сохранено');loadBusinesses()}catch(e){toast(e.message)}};
  document.querySelectorAll('.copy-script').forEach(x=>x.onclick=async()=>{const content=x.parentElement.querySelector('textarea').value;if(/\[[^\]]+\]/.test(content)){toast('Сначала заполните поля в [скобках]');return}try{await navigator.clipboard.writeText(content);toast('Скопировано')}catch{toast('Не удалось скопировать')}});
}
function closeDetail(){$('#detail-backdrop').classList.add('hidden')}
$('#detail-backdrop').addEventListener('click',e=>{if(e.target.id==='detail-backdrop')closeDetail()});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDetail()});
async function loadStats(){try{const s=await api('/api/stats');
  $('#stat-cards').innerHTML=[['Всего бизнесов',s.total],['Начали контакт',s.contacted],['Получили ответ',s.replied],['Завершены',s.completed]].map(([name,value])=>`<div class="stat-tile"><b>${value}</b><span>${name}</span></div>`).join('');
  $('#status-bars').innerHTML=Object.entries(state.config.statuses).map(([key,name])=>`<div class="bar-row"><div class="bar-title"><span>${esc(name)}</span><b>${s.statuses[key]||0}</b></div><div class="bar-track"><i style="width:${s.total?100*(s.statuses[key]||0)/s.total:0}%"></i></div></div>`).join('');
  $('#type-stats').innerHTML=[...Object.entries(state.config.site_types),['other','Обычный сайт']].map(([key,name])=>`<div class="simple-row"><span>${esc(name)}</span><b>${s.site_types[key]||0}</b></div>`).join('');
  $('#group-stats').innerHTML=s.groups.length?s.groups.map(g=>`<div class="simple-row"><span>${esc(g.niche)} · ${esc(g.city)}, ${esc(g.country)} · ${esc(labelType(g.site_type))}</span><b>${g.count}</b></div>`).join(''):empty('Данных пока нет');
  $('#timeline').innerHTML=s.timeline.length?s.timeline.slice(0,25).map(t=>`<div class="simple-row"><span>${esc(t.day)} · ${esc(labelStatus(t.status))}</span><b>${t.count}</b></div>`).join(''):empty('Изменений пока нет');
}catch(e){toast(e.message)}}
async function loadTemplates(){try{const rows=await api('/api/templates');$('#templates').innerHTML=rows.map(t=>`<details class="template-card"><summary>${esc(t.title)}</summary><textarea aria-label="${esc(t.title)}">${esc(t.body)}</textarea><button class="secondary" data-template="${esc(t.slug)}">Сохранить шаблон</button></details>`).join('');
  document.querySelectorAll('[data-template]').forEach(btn=>btn.onclick=async()=>{try{await api('/api/templates/'+btn.dataset.template,'PUT',{body:btn.parentElement.querySelector('textarea').value});toast('Шаблон сохранён')}catch(e){toast(e.message)}})
}catch(e){$('#templates').innerHTML=empty(e.message)}}
init();
