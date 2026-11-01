"""End-to-end check of the built Desktop (docs/unify U8, U9) on a COPY of the owner's base.

1. Startup: 5 launches in a row (window shown and not hung, graceful close), one from another folder, one via a
   shortcut (.lnk in a temp folder).
2. Scenarios in the built exe: list and card with four offers, statistics, one live search on yandex.ru,
   one products job and one gallery job for a real card, one live AI chat request (the owner's key from the
   environment). Yandex may answer with a captcha: that is recorded as the result (KNOWN_ISSUES K2), not hidden.
Output: verification/unify/e2e/ (report.json, screenshots), verification/unify/startup.txt.
Usage: python scripts/unify_e2e.py EXE BASE_COPY
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import scripts.mailing_clean_machine as cdp  # noqa: E402

OUT = ROOT / "verification" / "unify" / "e2e"
cdp.OUT = OUT
RUN = ROOT / ".build-tmp" / "unify-e2e"


def profile(base_copy: Path | None, name: str) -> Path:
    data = RUN / name
    data.mkdir(parents=True)
    if base_copy:
        shutil.copy(base_copy, data / "app.sqlite3")
    (data / "first_run_complete").write_text("e2e", encoding="utf-8")
    return data


def owner_ai_key() -> str:
    """The owner's key from the environment or the Windows user variable (never printed)."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, "OPENROUTER_API_KEY")[0])
    except OSError:
        return ""


def launch(command, data: Path, cwd: Path, debug: bool = False):
    env = {**os.environ, "DATA_DIR": str(data)}
    if debug and owner_ai_key():
        env["OPENROUTER_API_KEY"] = owner_ai_key()
    if debug:
        env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"--remote-debugging-port={cdp.PORT} --remote-allow-origins=*"
    return subprocess.Popen(command, cwd=str(cwd), env=env,
                            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def wait_window(seconds: int = 90) -> bool:
    import ctypes
    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = ctypes.c_void_p
    end = time.time() + seconds
    while time.time() < end:
        if user32.FindWindowW(None, "LeadEqualMoney"):
            return True
        time.sleep(0.5)
    return False


def close_app() -> bool:
    import ctypes
    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = ctypes.c_void_p
    hwnd = user32.FindWindowW(None, "LeadEqualMoney")
    if hwnd:
        user32.PostMessageW(ctypes.c_void_p(hwnd), 0x0010, 0, 0)  # WM_CLOSE: the normal close path
    end = time.time() + 60
    while time.time() < end and user32.FindWindowW(None, "LeadEqualMoney"):
        time.sleep(0.5)
    return not user32.FindWindowW(None, "LeadEqualMoney")


def startup(exe: Path) -> list[str]:
    lines = []
    for i in range(1, 6):
        data = profile(None, f"start-{i}")
        process = launch([str(exe)], data, RUN)
        shown = wait_window()
        responding = cdp.window_responding() if shown else False
        closed = close_app()
        process.wait(30)
        lines.append(f"запуск {i}: окно={shown} отвечает={responding} закрыто штатно={closed} код={process.returncode}")
    other = RUN / "другая папка"
    other.mkdir()
    data = profile(None, "start-other-folder")
    process = launch([str(exe)], data, other)
    shown = wait_window()
    lines.append(f"из другой папки ({other.name}): окно={shown} отвечает={cdp.window_responding() if shown else False} "
                 f"закрыто={close_app()}")
    process.wait(30)
    link = RUN / "LeadEqualMoney.lnk"
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');$s.TargetPath='{exe}';"
          f"$s.WorkingDirectory='';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)
    data = profile(None, "start-shortcut")
    try:  # the .lnk is opened by the shell («start»), as a double click does; DATA_DIR is inherited
        subprocess.Popen(["cmd", "/c", "start", "", str(link)], cwd=str(RUN), env={**os.environ, "DATA_DIR": str(data)},
                         creationflags=subprocess.CREATE_NO_WINDOW)
        shown = wait_window()
        lines.append(f"по ярлыку (пустая рабочая папка): окно={shown} "
                     f"отвечает={cdp.window_responding() if shown else False} закрыто={close_app()} "
                     f"база создана в DATA_DIR={(data / 'app.sqlite3').is_file()}")
    except OSError as error:
        lines.append(f"по ярлыку: не запустился ({error})")
    return lines


def poll(page, expression: str, done: str, seconds: int):
    end = time.time() + seconds
    value = None
    while time.time() < end:
        value = page.js(expression)
        if page.js(f"(v=>{done})({json.dumps(value)})"):
            return value, True
        time.sleep(5)
    return value, False


def scenarios(exe: Path, base_copy: Path) -> dict:
    data = profile(base_copy, "scenarios")
    process = launch([str(exe)], data, RUN, debug=True)
    result = {}
    try:
        page = cdp.Page()
        page.wait("typeof state!=='undefined' && state.config", 60)
        result["businesses"] = page.js("call('businesses',{}).then(x=>x.length)")
        org = page.js("call('businesses',{}).then(x=>{const b=x.find(b=>b.map_url);return {id:b.id,name:b.name}})")
        scripts = page.js(f"call('business_scripts',{json.dumps(org['id'])}).then(s=>s.offers.map(o=>[o.category,o.slug,Boolean(o.text)]))")
        result["card_offers"] = scripts
        page.js(f"(()=>{{state.businessId={json.dumps(org['id'])};setView('detail',{{businessId:{json.dumps(org['id'])}}})}})()")
        page.js("new Promise(r=>setTimeout(r,2000))")
        result["card_shows_four_offers"] = page.js("document.querySelectorAll('[data-offer]').length") == 4
        page.shot("01_card_real_copy.png")
        result["stats"] = page.js("call('stats').then(s=>({total:s.total,contacted:s.contacted,statuses:s.statuses}))")
        page.js("document.querySelector('.sidebar [data-view=results]').click()")
        page.js("new Promise(r=>setTimeout(r,2500))"); page.shot("02_results.png")
        # Live search on yandex.ru (one job, two new organisations).
        job = page.js("call('search_start',{country:'Россия',city:'Тула',niche:'кофейня',count:2,"
                      "types:['no_site','weak_site','unavailable']}).then(r=>r.jobs[0].id)")
        value, finished = poll(page, f"call('search_jobs').then(x=>x.find(j=>j.id==={json.dumps(job)}))",
                               "v && !['queued','running'].includes(v.status)", 420)
        result["live_search"] = {"finished": finished, "status": (value or {}).get("status"),
                                 "result": (value or {}).get("result"), "error": (value or {}).get("error")}
        # Products and gallery for a real card.
        # «Найти разделы фото» first, as in the UI (KNOWN_ISSUES K1: chips may not be found), then the whole gallery.
        try:
            found = page.js(f"call('media_discover_sections',{json.dumps({'business_id': org['id']})})")
            result["discover_sections"] = {"ok": True, "sections": (found or {}).get("sections")}
        except Exception as error:  # a captcha or no chips is a result, not a crash of the check
            result["discover_sections"] = {"ok": False, "error": str(error)[:300]}
        for kind, extra in (("products", {}),
                            ("images", {"selection": {"mode": "all"}, "per_section_limit": 2, "organization_limit": 4})):
            # repeat=True: a real new run, like «Повторить парсинг» (otherwise the card's earlier job is returned)
            item = page.js(f"call('media_start',{json.dumps(kind)},{json.dumps({'business_id': org['id'], 'repeat': True, **extra})}).then(j=>j.id)")
            value, finished = poll(page, f"call('media_jobs').then(x=>x.find(j=>j.id==={json.dumps(item)}))",
                                   "v && !['queued','running'].includes(v.status)", 420)
            result[f"live_{kind}"] = {"finished": finished, "status": (value or {}).get("status"),
                                      "error": (value or {}).get("error"),
                                      "result": json.dumps((value or {}).get("result"), ensure_ascii=False)[:300]}
        # One live AI chat request with the owner's key (environment).
        task = page.js(f"call('ai_chat_start',[{{role:'user',content:'Короткое первое сообщение кафе без сайта'}}],'site',"
                       f"{json.dumps(org['id'])}).then(t=>t.id)")
        value, finished = poll(page, f"call('ai_chat_result',{json.dumps(task)})", "v && v.status!=='running'", 240)
        result["live_ai_chat"] = {"finished": finished, "status": (value or {}).get("status"),
                                  "model": (value or {}).get("model"), "error": (value or {}).get("error"),
                                  "reply_chars": len((value or {}).get("text") or "")}
        page.js("document.querySelector('.sidebar [data-view=leads]').click()")
        page.js("new Promise(r=>setTimeout(r,2500))"); page.shot("03_leads_after_search.png")
        result["businesses_after"] = page.js("call('businesses',{}).then(x=>x.length)")
        result["js_errors_toast"] = page.js("document.querySelector('#toast')?.textContent || ''")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        close_app()
        try:
            process.wait(30)
        except subprocess.TimeoutExpired:
            process.kill()
    log = data / "logs" / "app.log"
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    key = owner_ai_key()
    result["log_has_no_ai_key"] = not key or key not in text
    result["log_tracebacks"] = text.count("Traceback")
    return result


def main(exe: Path, base_copy: Path) -> int:
    exe, base_copy = exe.resolve(), base_copy.resolve()  # the shortcut needs an absolute target
    if RUN.exists():
        shutil.rmtree(RUN)
    RUN.mkdir(parents=True)
    OUT.mkdir(parents=True, exist_ok=True)
    lines = startup(exe)
    (ROOT / "verification" / "unify" / "startup.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    report = scenarios(exe, base_copy)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
