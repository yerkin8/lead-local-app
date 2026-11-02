"""Desktop window tour after the Mini App merge (docs/unify), on synthetic .build-tmp/ui-tour-data only.

Checks the owner's UI requirements in the real pywebview window and saves screenshots + a JSON report:
the app starts on «Лиды»; every section opens without JS errors; the card opens from «Лиды» and «Сегодня»,
«Назад» returns and the sidebar keeps «Лиды» on the card; the card shows four offers and
«Позвонить», no notes; a repeated ready event and the poller do not wipe typed text.
Run: python scripts/seed_demo_data.py (with DATA_DIR=.build-tmp/ui-tour-data), then this script.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = Path(os.environ.get("UI_TOUR_DATA") or ROOT / ".build-tmp/ui-tour-data").resolve()
os.environ["DATA_DIR"] = str(DATA)
if not (DATA / "app.sqlite3").exists():
    raise SystemExit("Run scripts/seed_demo_data.py first")
(DATA / "first_run_complete").write_text("1.0.0", encoding="utf-8")

import webview  # noqa: E402
from app.desktop import Bridge, LocalWorker, resource_path  # noqa: E402

OUT = Path(os.environ.get("UI_TOUR_OUT") or ROOT / "verification/unify/ui")
OUT.mkdir(parents=True, exist_ok=True)


import ctypes  # noqa: E402
from ctypes import wintypes  # noqa: E402

_user32 = ctypes.WinDLL("user32")  # own instance: other modules may set argtypes on ctypes.windll.user32
_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowW.restype = wintypes.HWND
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.PrintWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.UINT]


def capture(name: str, out: Path | None = None) -> None:
    hwnd = _user32.FindWindowW(None, "LeadEqualMoney")
    if not hwnd:
        raise RuntimeError("Window handle unavailable")
    rect = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    import clr
    clr.AddReference("System.Drawing")
    from System.Drawing import Bitmap, Graphics
    from System.Drawing.Imaging import ImageFormat
    bitmap = Bitmap(rect.right - rect.left, rect.bottom - rect.top)
    graphics = Graphics.FromImage(bitmap)
    hdc = graphics.GetHdc()
    try:
        _user32.PrintWindow(hwnd, ctypes.c_void_p(hdc.ToInt64()), 2)  # .NET IntPtr -> plain address
    finally:
        graphics.ReleaseHdc(hdc)
    bitmap.Save(str((out or OUT) / name), ImageFormat.Png)
    graphics.Dispose()
    bitmap.Dispose()
REPORT: dict = {"checks": {}, "log": []}


def js(window, code):
    return window.evaluate_js(code)


def wait(window, condition: str, seconds: float = 8.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if js(window, f"Boolean({condition})"):
            return True
        time.sleep(0.2)
    return False


def check(name: str, ok, detail=""):
    REPORT["checks"][name] = {"ok": bool(ok), "detail": detail}


BRIDGE = None   # set in main(): the tour prepares mailing data through the same service


def next_checks(window):
    """docs/next: «Остановить» on a search job; a batch with results, «Убрать из пачки», «Повторить пачку».
    No network: the search job is written as already running (the worker only takes queued ones),
    the mailing runs as a dry run only."""
    from app import db
    params = {"country": "Россия", "city": "Казань", "niche": "Кофейни", "count": 30, "types": ["no_site"],
              "review_limit": 10, "_desktop_details": True}
    running = db.create_job("search", params)
    db.update_job(running, status="running", progress={"matched": 7, "scanned": 12, "target": 30,
                                                       "phase": "details", "details_done": 3, "details_total": 7})
    js(window, 'document.querySelector(".sidebar [data-view=search]").click()'); time.sleep(1.3)
    row = f'document.querySelector("[data-activity-id=\'{running}\']")'
    check("search_stop_button_on_running_job", js(window, f'Boolean({row}?.querySelector("[data-stop-job]"))'))
    check("search_shows_review_pass", "догружаю отзывы" in (js(window, f'{row}?.innerText || ""') or "").lower(),
          js(window, f'{row}?.innerText'))
    capture("search-running.png")
    js(window, f'{row}.querySelector("[data-stop-job]").click()'); time.sleep(1.5)
    check("search_stop_click_marks_stopping", bool(db.job(running)["progress"].get("stopping")))
    db.update_job(running, status="stopped", error="Остановлено пользователем",
                  result={"partial": True, "matched": 7, "reason": "Остановлено пользователем"})
    js(window, 'render()'); time.sleep(1.3)
    text = js(window, f'{row}?.innerText || ""') or ""
    check("search_stopped_label", "Остановлен" in text and "в «Лидах»" in text, text[-200:])
    capture("search-stopped.png")

    service = BRIDGE._mailing
    service.save_keys(31415926, "0123456789abcdef0123456789abcdef")   # demo keys, tour folder only
    account = service.add_account("demo-1", "+79990000001", False)["id"]
    with service.store.tx() as conn:
        conn.execute("UPDATE accounts SET status='active' WHERE id=?", (account,))
    with db.connection() as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM businesses ORDER BY id LIMIT 6")]
        for index, identity in enumerate(ids):
            conn.execute("UPDATE businesses SET socials=? WHERE id=?",
                         (json.dumps([{"url": f"https://t.me/demo_cafe_{index}", "label": "t.me"}]), identity))
    campaign = service.create_campaign("Кофейни · Казань", {"ids": ids}, "auto", [account])
    service.dry_run(campaign["id"])
    db.set_business(ids[1], "replied", None)
    js(window, f'setView("mailing", {{mailingCampaign: {campaign["id"]}}})'); time.sleep(2)
    wait(window, 'document.querySelector("#ml-repeat-form")', 8)
    page = js(window, 'document.querySelector("#content").innerText') or ""
    check("mailing_batch_results_per_business", "Бизнесы пачки и результат" in page and "Сухой запуск" in page and "уйдёт" in page.lower(),
          page[:300])
    check("mailing_lead_status_shown", "В «Лидах»: Ответили" in page)
    check("mailing_every_card_has_timer",
          js(window, 'document.querySelectorAll(".ml-recipient").length') == js(window, 'document.querySelectorAll(".ml-recipient .ml-due").length') > 0,
          [js(window, 'document.querySelectorAll(".ml-recipient").length'), js(window, 'document.querySelectorAll(".ml-recipient .ml-due").length')])
    check("mailing_exclude_buttons", js(window, 'document.querySelectorAll("[data-ml-exclude]").length') == len(ids))
    check("mailing_repeat_reminder_default",
          js(window, 'document.querySelector("#ml-repeat-form [name=template]").value') == "auto:reminder")
    check("mailing_repeat_skips_replied", "Ответили — 1" in page, page[page.find("Получат"):][:200])
    check("mailing_repeat_dry_only_for_dry_batch", not js(window, 'Boolean(document.querySelector("[data-ml=repeat-live]"))'))
    js(window, 'document.querySelector("#ml-repeat").scrollIntoView()'); time.sleep(0.5)
    capture("mailing-batch.png")
    js(window, 'document.querySelector("#ml-recipients").scrollIntoView()'); time.sleep(0.5)
    capture("mailing-results.png")
    js(window, f'document.querySelector("[data-ml-exclude=\'{ids[2]}\']").click()'); time.sleep(1.5)
    check("mailing_exclude_then_include_button",
          js(window, f'Boolean(document.querySelector("[data-ml-include=\'{ids[2]}\']"))'))
    js(window, 'document.querySelector("[data-ml=repeat-dry]").click()'); time.sleep(2.5)
    detail = service.campaign(js(window, "state.mailingCampaign"))
    check("mailing_repeat_dry_created", detail["stage"] == "reminder" and detail["counts"]["dry_sent"] == len(ids) - 2,
          {"stage": detail["stage"], "counts": detail["counts"]})
    page = js(window, 'document.querySelector("#content").innerText') or ""
    check("mailing_batch_lists_both", page.count("сухой запуск") >= 2 and "напоминание" in page)
    js(window, 'document.querySelector("#ml-repeat").previousElementSibling.scrollIntoView()'); time.sleep(0.5)
    capture("mailing-repeat.png")
    js(window, f'document.querySelector("#ml-recipients [data-open]")?.click()'); time.sleep(1.5)
    check("mailing_opens_lead_card", js(window, "state.view") == "lead", js(window, "state.view"))
    check("back_named_to_mailing", js(window, 'document.querySelector("#app-back")?.textContent') == "← К рассылке")
    js(window, 'setView("mailing", {mailingCampaign: null, mailingTab: "tests"})'); time.sleep(1.5)
    check("mailing_tests_tab", js(window, 'Boolean(document.querySelector("#ml-test-add-form") && document.querySelector("#ml-test-run-form [name=no_wait]"))'))
    check("one_scrollbar_mailing", js(window, 'document.scrollingElement.scrollHeight <= innerHeight'))


def tour(window):
    try:
        wait(window, "window.state && state.config", 20)
        js(window, 'window.__qaErrors=[];window.addEventListener("error",e=>window.__qaErrors.push(e.message));'
                   'const old=console.error;console.error=(...x)=>{window.__qaErrors.push(x.join(" "));old(...x)}')
        check("start_page_is_leads", js(window, "state.view") == "leads", js(window, "state.view"))
        capture("start.png")
        check("no_base_or_panel_in_sidebar", js(window, '!document.querySelector(".sidebar [data-view=businesses],.sidebar [data-view=dashboard]")'))
        back_on_roots = []
        for view in ("leads", "results", "today", "search", "scripts", "ai", "mailing", "media", "settings"):
            js(window, f'document.querySelector(".sidebar [data-view={view}]").click()')
            time.sleep(1.2)
            if not js(window, 'document.querySelector("#app-back")?.hidden !== false'):
                back_on_roots.append(view)
            active = js(window, 'document.querySelector(".sidebar nav button.active")?.dataset.view')
            check(f"section_{view}_opens", js(window, "state.view") == view and active == view,
                  f"view={js(window, 'state.view')} active={active}")
            capture(f"{view}.png")
            if view == "mailing":
                check("mailing_section_loads", not js(window, 'document.querySelector("#content").innerText.includes("Не удалось открыть")')
                      and js(window, 'document.querySelector("#page-title")?.textContent || ""') != "ИИ-чат")
        check("back_hidden_on_every_root", not back_on_roots, back_on_roots)
        check("sidebar_data_path_short", js(window, 'document.querySelector("#data-dir").textContent') == "Данные: локально"
              and bool(js(window, 'document.querySelector("#data-dir").title')))
        check("settings_save_not_over_labels", js(window, """(() => { const b=document.querySelector('#settings-search-form .btn');
            const l=document.querySelector('#settings-search-form label.field'); if(!b||!l) return false;
            const r=b.getBoundingClientRect(), t=l.getBoundingClientRect(); return r.left>=t.right-1 || r.top>=t.bottom-1; })()"""))
        check("settings_without_old_data_tools", js(window, '!document.querySelector("[data-replace-database],[data-open-logs],#debug-html")'))
        check("settings_has_no_server_admins", js(window, '!document.querySelector("#admins-panel,#agent-form")'))
        js(window, 'document.querySelector("#ai-key-panel")?.scrollIntoView()'); time.sleep(0.5); capture("settings-ai-key.png")
        check("no_horizontal_scroll_in_settings",
              js(window, 'document.querySelector(".main").scrollWidth - document.querySelector(".main").clientWidth') <= 1)
        check("settings_has_ai_key_panel", js(window, 'Boolean(document.querySelector("#ai-key-panel [data-ai-key-form]"))'))

        # The key is entered in «Настройки» (redesign 05): the field is emptied, the key never shows, the chat opens.
        fake = "sk-or-v1-" + "0123456789abcdef" * 4
        had_form = js(window, 'Boolean(document.querySelector("#ai-key-panel [data-ai-key-form]"))')
        js(window, f'const f=document.querySelector("#ai-key-panel [data-ai-key-form]");if(f){{'
                   f'f.querySelector("[name=key]").value={json.dumps(fake)};f.requestSubmit()}}')
        time.sleep(2.5)
        saved_hidden = fake not in js(window, "document.documentElement.outerHTML")
        js(window, 'document.querySelector(".sidebar [data-view=ai]").click()'); time.sleep(1.5)
        page = js(window, "document.documentElement.outerHTML")
        check("ai_key_form_saves_without_echo",
              had_form and saved_hidden and fake not in page and js(window, 'Boolean(document.querySelector(".ai-chat"))'),
              {"had_form": had_form, "chat_open": js(window, 'Boolean(document.querySelector(".ai-chat"))')})
        capture("ai-after-key.png")

        js(window, 'document.querySelector(".sidebar [data-view=scripts]").click()'); time.sleep(1.2)
        groups = js(window, 'document.querySelectorAll("#content details").length')
        check("scripts_grouped_by_category", groups >= 7, groups)

        # Card from «Лиды»: one lead card with everything; «Назад» returns, the sidebar stays on «Лиды».
        js(window, 'document.querySelector(".sidebar [data-view=leads]").click()'); time.sleep(1.5)
        js(window, 'state.navigation=[]')
        check("leads_has_add_lead", js(window, 'Boolean(document.querySelector("[data-lead-add]") && document.querySelector("[data-phones-refill]"))'))
        js(window, 'document.querySelector("[data-lead-add]")?.click()'); time.sleep(0.6)
        check("add_lead_sheet_opens", js(window, 'Boolean(document.querySelector("#lead-add-form [name=url]"))'))
        js(window, 'document.querySelector(".lead-sheet")?.remove()')
        wait(window, 'document.querySelector("[data-lead-open]")', 5)
        js(window, 'document.querySelector("[data-lead-open]")?.click()'); time.sleep(1.5)
        check("leads_opens_card", js(window, "state.view") == "lead", js(window, "state.view"))
        check("card_has_moved_actions", js(window, 'Boolean(document.querySelector("[data-lead-refresh]") && document.querySelector("[data-today-add]") && document.querySelector("#content [data-media]") && document.querySelector("[data-contact-add=phone]") && document.querySelector("[data-contact-add=link]"))'))
        check("no_old_card_link", js(window, '!document.querySelector("[data-menu=old]")'))
        check("one_scrollbar", js(window, 'document.scrollingElement.scrollHeight <= innerHeight'),
              js(window, '[document.scrollingElement.scrollHeight, innerHeight]'))
        js(window, 'document.querySelector("[data-today-add]")?.click()'); time.sleep(1.2)  # queue it for «Сегодня»
        capture("card.png")
        offers = js(window, '[...document.querySelectorAll("[data-offer]")].map(x=>x.dataset.offer).join(",")')
        check("card_four_offers", offers.split(",") == ["site", "landing", "bot", "promotion"], offers)
        check("card_copy_buttons", js(window, 'document.querySelectorAll("[data-copy-offer]").length') == 4)
        check("card_status_call", js(window, 'Boolean(document.querySelector("[data-lead-set-status=call]"))'))
        check("card_no_notes", js(window, '!document.querySelector("#business-note,[name=note],#lead-note")'))
        check("sidebar_on_card_is_leads", js(window, 'document.querySelector(".sidebar nav button.active")?.dataset.view') == "leads")

        # Typed text survives a repeated ready event and a poller tick.
        js(window, 'const t=document.querySelector("[data-offer-text]");if(t){t.value="ТЕКСТ-ПРОВЕРКА";t.dispatchEvent(new Event("input",{bubbles:true}))}')
        js(window, 'window.dispatchEvent(new Event("pywebviewready"));document.dispatchEvent(new Event("pywebviewready"))')
        time.sleep(2)
        js(window, 'typeof activityPoller!=="undefined"&&activityPoller.refresh&&activityPoller.refresh()'); time.sleep(2)
        check("input_kept_after_ready_and_poll",
              js(window, 'document.querySelector("[data-offer-text]")?.value') == "ТЕКСТ-ПРОВЕРКА",
              js(window, 'document.querySelector("[data-offer-text]")?.value'))

        depth = js(window, "state.navigation.length")
        trail = []
        for _ in range(depth):
            js(window, 'document.querySelector("#app-back")?.click()'); time.sleep(1)
            active = js(window, 'document.querySelector(".sidebar nav button.active")?.dataset.view')
            trail.append(f'{js(window, "state.view")}:{active}')
        check("back_returns_to_leads", depth >= 1 and trail[-1] == "leads:leads", {"depth": depth, "trail": trail})
        capture("back-from-card.png")
        check("back_hidden_at_root", js(window, 'document.querySelector("#app-back")?.hidden') is True)

        # Card from «Сегодня» («Карточка»), then «Назад».
        js(window, 'document.querySelector(".sidebar [data-view=today]").click()'); time.sleep(1.5)
        js(window, 'state.navigation=[]')
        js(window, 'document.querySelector("[data-today-ack]")?.click()'); time.sleep(1.2)
        check("today_warning_one_line", js(window, 'Boolean(document.querySelector(".today-warning-line")) && !document.querySelector(".today-warning")'))
        capture("today-ack.png")
        has_today = wait(window, 'document.querySelector("#content button[data-open]")', 5)
        js(window, 'document.querySelector("#content button[data-open]")?.click()'); time.sleep(1.5)
        check("today_opens_card", has_today and js(window, "state.view") == "lead", js(window, "state.view"))
        check("back_named_to_today", js(window, 'document.querySelector("#app-back")?.textContent') == "← К «Сегодня»")
        capture("card-from-today.png")
        js(window, 'document.querySelector("#app-back")?.click()'); time.sleep(1.2)
        check("back_from_card_to_today", js(window, "state.view") == "today", js(window, "state.view"))

        # «Лиды»: a row opens the lead card.
        js(window, 'document.querySelector(".sidebar [data-view=leads]").click()'); time.sleep(1.5)
        wait(window, 'document.querySelector("[data-lead-open]")', 5)
        js(window, 'document.querySelector("[data-lead-open]")?.click()'); time.sleep(1.5)
        check("lead_row_opens_lead_card", js(window, "state.view") == "lead", js(window, "state.view"))
        # Sticky bar: prev/next through the list order, «Назад» named after the list.
        first = js(window, "state.businessId")
        check("lead_bar_present", js(window, 'Boolean(document.querySelector(".lead-bar [data-lead-next]") && document.querySelector(".lead-bar [data-lead-set-status=call]"))'))
        check("lead_bar_prev_disabled_on_first", js(window, 'document.querySelector("[data-lead-prev]")?.disabled') is True)
        check("back_named_to_leads", js(window, 'document.querySelector("#app-back")?.textContent') == "← К лидам"
              and js(window, 'document.querySelector("#app-back")?.hidden') is False)
        js(window, 'document.querySelector("[data-lead-next]")?.click()'); time.sleep(1.5)
        second = js(window, "state.businessId")
        js(window, 'document.querySelector("[data-lead-prev]")?.click()'); time.sleep(1.5)
        check("lead_next_then_prev", second and second != first and js(window, "state.businessId") == first and js(window, "state.view") == "lead",
              {"first": first, "second": second, "back": js(window, "state.businessId")})
        check("sidebar_on_lead_is_leads", js(window, 'document.querySelector(".sidebar nav button.active")?.dataset.view') == "leads")
        capture("lead.png")

        next_checks(window)

        # Dark theme screenshots of every section (UI_TOUR_DARK_OUT).
        dark = os.environ.get("UI_TOUR_DARK_OUT")
        if dark:
            dark_dir = Path(dark); dark_dir.mkdir(parents=True, exist_ok=True)
            js(window, 'document.documentElement.dataset.theme="dark"')
            for view in ("leads", "results", "today", "search", "scripts", "ai", "mailing", "media", "settings"):
                js(window, f'document.querySelector(".sidebar [data-view={view}]").click()'); time.sleep(1.3)
                capture(f"{view}.png", dark_dir)
            js(window, 'document.querySelector(".sidebar [data-view=leads]").click()'); time.sleep(1.3)
            js(window, 'document.querySelector("[data-lead-open]")?.click()'); time.sleep(1.5)
            capture("lead.png", dark_dir)
            js(window, 'document.documentElement.dataset.theme="light"')
        errors = json.loads(js(window, "JSON.stringify(window.__qaErrors)") or "[]")
        check("no_js_errors", not errors, errors[:5])
    except Exception as error:  # report, never hang the window
        REPORT["log"].append(f"tour error: {error!r}")
    finally:
        REPORT["passed"] = all(c["ok"] for c in REPORT["checks"].values()) and not REPORT["log"]
        (OUT / "report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=1), encoding="utf-8")
        window.destroy()


def main():
    worker = LocalWorker()
    bridge = Bridge()
    from app.mailing.desktop_bridge import create_service  # as desktop.main(): the mailing module runs (no keys, no network)
    bridge._mailing = create_service(DATA)
    global BRIDGE
    BRIDGE = bridge
    bridge._mailing.start()
    window = webview.create_window("LeadEqualMoney", resource_path("app/desktop_ui/index.html").resolve().as_uri(), js_api=bridge,
                                   width=1360, height=900)
    threading.Thread(target=lambda: (time.sleep(3), tour(window)), daemon=True).start()
    webview.start(worker.start, gui="edgechromium", private_mode=True)
    worker.shutdown(timeout=10)
    try:
        bridge._mailing.shutdown(15)
    except Exception:
        pass
    print(json.dumps({k: v["ok"] for k, v in REPORT["checks"].items()}, ensure_ascii=False))
    print("PASSED" if REPORT.get("passed") else "FAILED", REPORT["log"])


if __name__ == "__main__":
    main()
