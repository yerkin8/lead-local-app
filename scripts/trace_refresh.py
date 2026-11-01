"""Trace the local desktop UI in Chrome with a deterministic, offline bridge.

No application backend or external URL is contacted. This measures the actual
desktop HTML/CSS/JS and records the boundary at which the Python bridge is called.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "app" / "desktop_ui" / "index.html").as_uri()
VIEWS = ("dashboard", "search", "businesses", "detail", "media", "scripts", "campaigns", "campaign-detail", "settings")

INSTRUMENT = r"""
(() => {
  const trace = {timers: [], calls: [], mutations: [], errors: [], pagehide: 0,
    beforeunload: 0, navigationAtStart: 0, startedAt: performance.now()};
  window.__refreshTrace = trace;
  const stack = () => (new Error()).stack?.split('\n').slice(2, 5).join(' | ') || '';
  for (const name of ['setInterval', 'setTimeout', 'requestAnimationFrame']) {
    const original = window[name].bind(window);
    window[name] = function(callback, delay, ...args) {
      trace.timers.push({at: performance.now(), name, delay: Number(delay) || 0, stack: stack()});
      return original(callback, delay, ...args);
    };
  }
  for (const name of ['fetch', 'EventSource', 'WebSocket']) {
    if (!window[name]) continue;
    const original = window[name];
    window[name] = function(...args) {
      trace.calls.push({at: performance.now(), name, target: String(args[0] || '').slice(0, 160), stack: stack()});
      return Reflect.construct(original, args, new.target || original);
    };
  }
  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    trace.calls.push({at: performance.now(), name: 'XHR', target: String(url).slice(0, 160), stack: stack()});
    return open.call(this, method, url, ...rest);
  };
  for (const name of ['pushState', 'replaceState']) {
    const original = history[name].bind(history);
    history[name] = function(...args) {
      trace.calls.push({at: performance.now(), name: 'history.' + name, stack: stack()});
      return original(...args);
    };
  }
  for (const name of ['reload', 'assign', 'replace']) {
    try {
      const original = location[name].bind(location);
      location[name] = function(...args) {
        trace.calls.push({at: performance.now(), name: 'location.' + name, stack: stack()});
        return original(...args);
      };
    } catch (error) { trace.calls.push({name: 'location.' + name + '.not-writable'}); }
  }
  window.addEventListener('beforeunload', () => trace.beforeunload++);
  window.addEventListener('pagehide', () => trace.pagehide++);
  window.addEventListener('error', e => trace.errors.push(String(e.message)));
  const observer = new PerformanceObserver(list => {
    for (const entry of list.getEntries()) if (entry.entryType === 'navigation')
      trace.calls.push({at: performance.now(), name: 'navigation', target: entry.name});
  });
  observer.observe({type: 'navigation', buffered: true});
  document.addEventListener('DOMContentLoaded', () => {
    trace.navigationAtStart = performance.getEntriesByType('navigation').length;
    const root = document.querySelector('#content');
    new MutationObserver(records => {
      for (const record of records) trace.mutations.push({at: performance.now(),
        target: record.target === root ? 'content-root' : 'descendant',
        type: record.type, added: record.addedNodes.length, removed: record.removedNodes.length});
    }).observe(root, {subtree: true, childList: true, characterData: true});
  });
})();
"""

BRIDGE = r"""
(() => {
  const active = window.name.includes('active');
  const business = {id:'fixture-business', name:'Локальная организация', category:'Услуги',
    address:'Тестовый адрес', phones:['+79991234567'], socials:[], website:null,
    site_type:'no_site', status:'new', note:'', history:[], map_url:'https://example.invalid/card'};
  let tick = 0;
  const searchJob = () => ({id:'search-1',kind:'search',status: active ? 'running' : 'done',
    params:{city:'Тест',niche:'Услуги',count:10},progress:{matched:tick,scanned:tick},result:{},
    created_at:'2026-10-16T00:00:00',updated_at:'2026-10-16T00:00:00'});
  const mediaJob = () => ({id:'media-1',kind:'images',status: active ? 'running' : 'done',
    params:{},progress:{checked:tick},result:{},organization_id:'fixture-business',
    created_at:'2026-10-16T00:00:00',updated_at:'2026-10-16T00:00:00'});
  const campaign = () => ({id:'campaign-1',title:'Локальный черновик',status:active?'running':'draft',
    recipients:1,sent:Math.min(tick,1),created_at:'2026-10-16T00:00:00',updated_at:'2026-10-16T00:00:00',
    recipients_state:active?[{business_id:'fixture-business',status:tick>1?'sent':'sending',error:''}]:[]});
  const data = (method) => {
    if (method === 'bootstrap') return {countries:['Россия'],statuses:{new:'Новый'},
      site_types:['no_site'],data_dir:'offline-fixture',stats:{total:1},telegram:{},
      first_run:false,version:'trace',migration_notice:''};
    if (method === 'activity_snapshot') {
      tick++;
      const topics=args[0]?.topics||['search','media','campaigns'];
      const search=active&&topics.includes('search')?[searchJob()]:[];
      const media=active&&topics.includes('media')?[mediaJob()]:[];
      const campaigns=active&&topics.includes('campaigns')?[campaign()]:[];
      return {search,media,campaigns,active_count:search.length+media.length+campaigns.length};
    }
    if (method === 'stats') return {total:1,contacted:0};
    if (method === 'search_jobs') return [searchJob()];
    if (method === 'businesses') return [business];
    if (method === 'business') return business;
    if (method === 'business_filter_options') return {status:[],category:[],city:[]};
    if (method === 'media_jobs') return [mediaJob()];
    if (method === 'templates') return [{slug:'fixture',title:'Шаблон',body:'Текст'}];
    if (method === 'template_render') return 'Текст';
    if (method === 'campaign_list') return [campaign()];
    if (method === 'campaign_get') return {...campaign(),recipients:[{business_id:'fixture-business',
      peer:'fixture',message:'Черновик сообщения',status:'draft',error:''}]};
    if (method === 'telegram_state') return {configured:false,session_exists:false,
      phone:'',daily_limit:5,delay_seconds:60};
    if (method === 'contact_permissions') return [];
    return [];
  };
  window.__bridgeCalls = [];
  window.pywebview = {api: new Proxy({}, {get(_, method) {
    return async (...args) => {
      window.__bridgeCalls.push({at:performance.now(),method:String(method),args});
      return {ok:true,data:data(String(method),args)};
    };
  }})};
})();
"""


def driver(instrument: bool = True) -> webdriver.Chrome:
    options = Options()
    for arg in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
                "--disable-gpu", "--disable-background-timer-throttling",
                "--window-size=1366,768"):
        options.add_argument(arg)
    options.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})
    configured = os.environ.get("CHROMEDRIVER")
    if configured:
        executable = Path(configured)
    else:
        cache = Path.home() / ".cache" / "selenium" / "chromedriver" / "win64"
        candidates = sorted(cache.glob("*/chromedriver.exe"))
        if not candidates:
            raise RuntimeError("Set CHROMEDRIVER to a local ChromeDriver path")
        executable = candidates[-1]
    chrome = webdriver.Chrome(service=Service(str(executable)), options=options)
    if instrument:
        chrome.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": INSTRUMENT})
        chrome.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": BRIDGE})
    chrome.execute_cdp_cmd("Performance.enable", {})
    return chrome


def run_case(view: str, mode: str, duration: int, out: Path) -> dict:
    chrome = driver()
    try:
        chrome.execute_script("window.name=arguments[0]", mode)
        chrome.get(PAGE)
        chrome.execute_script("window.dispatchEvent(new Event('pywebviewready'))")
        chrome.execute_script("setView(arguments[0],arguments[1])", "campaigns" if view == "campaign-detail" else view,
                              {"businessId":"fixture-business", "campaignId":"campaign-1" if view == "campaign-detail" else None})
        time.sleep(1)
        initial = chrome.execute_script("return {title:document.querySelector('#page-title').textContent,html:document.querySelector('#content').innerHTML.length,errors:window.__refreshTrace.errors}")
        metrics_before = chrome.execute_cdp_cmd("Performance.getMetrics", {})
        baseline = chrome.execute_script("return {mutations:window.__refreshTrace.mutations.length,calls:window.__bridgeCalls.length,navigation:performance.getEntriesByType('navigation').length}")
        if view == "search" and mode == "active":
            chrome.execute_script("""const field=document.querySelector('#content input[name=city]');
              window.setTimeout(()=>{window.__typingProbeAt=performance.now();field.focus();field.value='Тест ввода';
              field.dispatchEvent(new Event('input',{bubbles:true}));},9000);""")
        # A CDP performance trace. ChromeDriver emits tracing events into its performance log.
        chrome.execute_cdp_cmd("Tracing.start", {"categories":"devtools.timeline,blink.user_timing,v8.execute",
                                                "options":"sampling-frequency=10000", "transferMode":"ReportEvents"})
        time.sleep(duration)
        chrome.execute_cdp_cmd("Tracing.end", {})
        time.sleep(1)
        performance_log = chrome.get_log("performance")
        snapshot = chrome.execute_script("return {trace:window.__refreshTrace,bridgeCalls:window.__bridgeCalls,navigation:performance.getEntriesByType('navigation').length,view:state.view,contentHtmlLength:document.querySelector('#content').innerHTML.length}")
        probe = chrome.execute_script("""const field=document.querySelector('#content input[name=city]');
          return {at:window.__typingProbeAt??null,value:field?.value??null,focused:document.activeElement===field,
          callsDuringPause:window.__typingProbeAt===undefined?null:window.__bridgeCalls.filter(x=>
          x.method==='activity_snapshot'&&x.at>=window.__typingProbeAt&&x.at<window.__typingProbeAt+1400).length};""")
        metrics_after = chrome.execute_cdp_cmd("Performance.getMetrics", {})
        browser_log = chrome.get_log("browser")
        result = {"view":view,"mode":mode,"duration_seconds":duration,"initial":initial,"typing_probe":probe,
                  "baseline":baseline,"snapshot":snapshot,"cdp_metrics_before":metrics_before,
                  "cdp_metrics_after":metrics_after,"browser_log":browser_log,"performance_log":performance_log}
        out.mkdir(parents=True, exist_ok=True)
        target = out / f"{view}-{mode}.json"
        target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        return {"view":view,"mode":mode,"file":str(target),
                "bridge_calls":len(snapshot["bridgeCalls"])-baseline["calls"],
                "root_mutations":sum(m["target"]=="content-root" for m in snapshot["trace"]["mutations"][baseline["mutations"]:]),
                "descendant_mutations":sum(m["target"]=="descendant" for m in snapshot["trace"]["mutations"][baseline["mutations"]:]),
                "navigations":snapshot["navigation"]-baseline["navigation"],
                "activity_calls":sum(call["method"]=="activity_snapshot" for call in snapshot["bridgeCalls"][baseline["calls"]:]),
                "typing_probe":probe,"errors":initial["errors"],"title":initial["title"]}
    finally:
        chrome.quit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    parser.add_argument("--duration", type=int, default=90)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--views", nargs="*", choices=VIEWS, default=VIEWS)
    args = parser.parse_args()
    out = ROOT / "verification" / "refresh" / args.phase
    cases = [(view, mode) for view in args.views for mode in ("idle", "active")]
    summaries = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_case, view, mode, args.duration, out) for view,mode in cases]
        for future in concurrent.futures.as_completed(futures):
            item = future.result()
            summaries.append(item)
            print(json.dumps(item,ensure_ascii=False),flush=True)
    (out / "summary.json").write_text(json.dumps(summaries,ensure_ascii=False,indent=2),encoding="utf-8")


if __name__ == "__main__":
    main()
