"""Walk every «Рассылка» screen in the real desktop window on synthetic data and FakeGateway.

No Telegram traffic: the gateway is FakeGateway. Data: .build-tmp/mailing-tour (recreated).
Output: verification/mailing/ui/*.png and tour.json (checks for A3, A9, A12).
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = (ROOT / ".build-tmp" / "mailing-tour").resolve()
if DATA.exists():
    shutil.rmtree(DATA)
DATA.mkdir(parents=True)
os.environ["DATA_DIR"] = str(DATA)
(DATA / "first_run_complete").write_text("tour", encoding="utf-8")

import webview  # noqa: E402

from app import db  # noqa: E402
from app.desktop import APP_NAME, Bridge, resource_path  # noqa: E402
from app.domain import render_template  # noqa: E402
from app.mailing.fake_gateway import FakeGateway  # noqa: E402
from app.mailing.service import MailingService  # noqa: E402
from app.script_templates import FIRST_BY_SITE_TYPE  # noqa: E402

OUT = ROOT / "verification" / "mailing" / "ui"
OUT.mkdir(parents=True, exist_ok=True)
REPORT: dict = {"steps": [], "checks": {}}
user32 = ctypes.windll.user32
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def seed() -> FakeGateway:
    db.init_desktop_db()
    fake = FakeGateway()
    cities = ["Казань", "Москва", "Новосибирск"]
    types = ["no_site", "weak_site", "unavailable"]
    jobs = db.create_desktop_parse_jobs({"niche": "Кофейни", "country": "Россия", "types": []}, cities[:1])
    for index in range(8):
        username = f"demo_cafe_{index}"
        fake.add_user(username)
        data = {"id": f"tour-{index}", "name": f"Кофейня «Пример {index + 1}»", "category": "Кофейня",
                "phones": [f"+7 900 555 00 {index:02d}"], "socials": [{"url": f"https://t.me/{username}", "label": "Telegram"}],
                "website": "", "site_type": types[index % 3], "rating": 4.5, "reviews": 10,
                "address": f"Казань, Демо-улица, {index + 1}",
                "map_url": f"https://yandex.ru/maps/org/tour/{1000 + index}/"}
        db.upsert_business(data, ("Кофейни", "Россия", "Казань"), jobs[0]["id"], parse_run_id=jobs[0]["run_id"])
    fake.add_user("owner_second_account")
    return fake


def capture(name: str) -> None:
    hwnd = user32.FindWindowW(None, APP_NAME)
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    import clr
    clr.AddReference("System.Drawing")
    from System.Drawing import Bitmap, Graphics
    from System.Drawing.Imaging import ImageFormat
    bitmap = Bitmap(rect.right - rect.left, rect.bottom - rect.top)
    graphics = Graphics.FromImage(bitmap)
    hdc = graphics.GetHdc()
    try:
        user32.PrintWindow(hwnd, ctypes.c_void_p(int(hdc.ToInt64())), 2)
        graphics.ReleaseHdc(hdc)
        hdc = None
        bitmap.Save(str(OUT / name), ImageFormat.Png)
    finally:
        if hdc:
            graphics.ReleaseHdc(hdc)
        graphics.Dispose()
        bitmap.Dispose()


def tour(window, service: MailingService):
    js = window.evaluate_js

    def wait(expr: str, timeout: float = 20) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if js(f"!!({expr})"):
                return True
            time.sleep(0.25)
        raise RuntimeError(f"timeout: {expr}")

    def click(selector: str) -> None:
        js(f"document.querySelector({json.dumps(selector)}).click()")
        time.sleep(0.6)

    def fill(selector: str, value: str) -> None:
        js(f"(()=>{{const n=document.querySelector({json.dumps(selector)});n.value={json.dumps(value)};"
           f"n.dispatchEvent(new Event('input',{{bubbles:true}}));n.dispatchEvent(new Event('change',{{bubbles:true}}))}})()")

    def back_visible() -> bool:
        return bool(js("!document.querySelector('#app-back')?.hidden"))

    def step(name: str, png: str) -> None:
        time.sleep(0.8)
        capture(png)
        REPORT["steps"].append({"step": name, "screenshot": png, "back_button": back_visible(),
                                "errors": js("window.__qaErrors||[]")})

    try:
        wait("window.pywebview && window.pywebview.api && typeof state!=='undefined' && state.config")
        js("window.__qaErrors=[];window.addEventListener('error',e=>__qaErrors.push(e.message))")
        click("nav button[data-view=mailing]")
        wait("document.querySelector('#ml-keys-form')")
        step("Мастер первого запуска: ключи", "01_wizard_keys.png")
        fill("#ml-keys-form [name=api_id]", "31415926")
        fill("#ml-keys-form [name=api_hash]", "0123456789abcdef0123456789abcdef")
        click("#ml-keys-form button.primary")
        wait("document.querySelector('#ml-account-form')")
        fill("#ml-account-form [name=label]", "test")
        fill("#ml-account-form [name=phone]", "+79005550199")
        js("document.querySelector('#ml-account-form [name=is_test]').checked=true")
        click("#ml-account-form button.primary")
        wait("document.querySelector('[data-ml-login]')")
        step("Аккаунты: добавлен, нужен вход", "02_accounts_needs_login.png")
        click("[data-ml-login]")
        wait("document.querySelector('[data-ml-login-code] [name=code]')")
        step("Вход: ввод кода", "03_login_code.png")
        fill("[data-ml-login-code] [name=code]", "12345")
        click("[data-ml-login-code] button.primary")
        wait("[...document.querySelectorAll('.chip')].some(c=>c.textContent==='Активен')")
        step("Аккаунт активен", "04_account_active.png")
        service.set_test_recipients(["@owner_second_account"])
        main_id = service.add_account("main-1", "+79005550198", False)["id"]
        service._save_session(main_id, "fake-session")
        with service.store.tx() as conn:
            conn.execute("UPDATE accounts SET status='active' WHERE id=?", (main_id,))

        click("[data-ml-tab=campaigns]")
        click("[data-ml-tab=new]")
        wait("document.querySelector('#ml-new-form')")
        fill("#ml-new-form [name=title]", "Тур: кофейни Казани")
        fill("#ml-new-form [name=limit]", "5")
        fill("#ml-new-form [name=template]", "Здравствуйте! Посмотрел карточку {название} в {город}: у вас {тип_сайта}. Могу прислать две идеи?")
        # A12: the top «Обновить» re-renders the page; typed values must stay.
        click("#refresh")
        wait("document.querySelector('#ml-new-form')")
        kept = js("document.querySelector('#ml-new-form [name=template]').value.includes('{название}') && "
                  "document.querySelector('#ml-new-form [name=title]').value==='Тур: кофейни Казани'")
        REPORT["checks"]["A12_input_kept_after_refresh"] = bool(kept)
        js("document.querySelectorAll('#ml-new-form [name=accounts]').forEach(n=>n.checked=n.parentElement.textContent.includes('main-1'));"
           "document.querySelector('#ml-new-form [name=accounts]').dispatchEvent(new Event('change',{bubbles:true}))")
        click("[data-ml=preview]")
        wait("document.querySelectorAll('#ml-preview .row').length===3")
        REPORT["checks"]["preview_rows"] = js("document.querySelectorAll('#ml-preview .row').length")
        step("Новая кампания: предпросмотр на 3 записях", "05_new_campaign_preview.png")
        click("#ml-new-form button.primary")
        wait("document.querySelector('[data-ml=dry]')")
        step("Кампания создана (сухой запуск)", "06_campaign_draft.png")
        click("[data-ml=dry]")
        wait("document.querySelector('[data-metric=\"Сухой запуск\"] strong')?.textContent!=='0'")
        campaign_id = js("state.mailingCampaign")
        counts = service.campaign(campaign_id)["counts"]
        shown = js("Object.fromEntries([...document.querySelectorAll('.ml-metrics .metric')].map(m=>[m.dataset.metric,Number(m.querySelector('strong').textContent)]))")
        REPORT["checks"]["A3_dry_run"] = {"dry_sent": counts["dry_sent"], "sent": counts["sent"]}
        REPORT["checks"]["A9_ui_equals_db_after_dry"] = (shown.get("Сухой запуск") == counts["dry_sent"]
                                                         and shown.get("Отправлено") == counts["sent"]
                                                         and shown.get("Всего") == counts["total"])
        step("Сухой запуск: отчёт", "07_dry_run_report.png")

        # Test campaign, live on FakeGateway (2 messages to the owner's own test account).
        click("#app-back")
        time.sleep(2)
        REPORT["checks"]["back_from_campaign_goes_to"] = js("state.mailingCampaign===null ? state.mailingTab : 'campaign'")
        js("setView('mailing',{mailingTab:'campaigns',mailingCampaign:null})")
        wait("document.querySelector('[data-ml-tab=new].btn')")
        time.sleep(1)
        js("state.mailingDraft.kind='test';state.mailingDraft.limit=2;state.mailingDraft.title='Тест на свои аккаунты'")
        click("[data-ml-tab=new].btn")
        wait("document.querySelector('#ml-new-form [name=kind]')?.value==='test'")
        time.sleep(1)
        js("document.querySelectorAll('#ml-new-form [name=accounts]').forEach(n=>n.checked=n.parentElement.textContent.includes('тестовый'));"
           "document.querySelector('#ml-new-form [name=accounts]').dispatchEvent(new Event('change',{bubbles:true}))")
        click("#ml-new-form button.primary")
        wait("document.querySelector('[data-ml=dry]')")
        fill("#ml-live-form [name=confirm]", "ОТПРАВИТЬ")
        click("#ml-live-form button")
        wait("document.querySelector('[data-ml=run]')")
        js("window.confirm=()=>true")
        click("[data-ml=run]")
        test_id = js("state.mailingCampaign")
        deadline = time.time() + 150
        while time.time() < deadline and service.campaign(test_id)["counts"]["sent"] < 2:
            time.sleep(1)
        click("[data-ml=refresh-campaign]")
        time.sleep(1)
        counts = service.campaign(test_id)["counts"]
        shown = js("Object.fromEntries([...document.querySelectorAll('.ml-metrics .metric')].map(m=>[m.dataset.metric,Number(m.querySelector('strong').textContent)]))")
        REPORT["checks"]["fake_live_sent"] = counts["sent"]
        REPORT["checks"]["A9_ui_equals_db_after_live"] = shown.get("Отправлено") == counts["sent"] and shown.get("Всего") == counts["total"]
        step("Тестовая кампания на FakeGateway: отправлено", "08_test_campaign_sent.png")

        click("[data-ml=emergency]")
        wait("document.querySelector('[data-ml=emergency-clear]')")
        step("Аварийный стоп включён", "09_emergency.png")
        js("window.confirm=()=>true")
        click("[data-ml=emergency-clear]")
        wait("document.querySelector('[data-ml=emergency]')")
        js("setView('mailing',{mailingTab:'stoplist',mailingCampaign:null})")
        wait("document.querySelector('#ml-stop-form')")
        fill("#ml-stop-form [name=value]", "@demo_cafe_7")
        click("#ml-stop-form button.primary")
        step("Стоп-лист", "10_stoplist.png")
        click("[data-ml-tab=policy]")
        wait("document.querySelector('#ml-policy-form')")
        step("Правила отправки", "11_policy.png")
        click("[data-ml-tab=journal]")
        step("Журнал", "12_journal.png")
        click("#app-back")
        REPORT["checks"]["back_from_journal_tab"] = js("state.mailingTab")
        html = js("document.querySelector('#content').innerText")
        REPORT["checks"]["no_secrets_on_screen"] = not any(s in html for s in ("31415926", "0123456789abcdef", "9005550199"))
        REPORT["checks"]["all_screens_have_back"] = all(item["back_button"] for item in REPORT["steps"][1:])
        REPORT["checks"]["js_errors"] = js("window.__qaErrors||[]")
    except Exception as exc:  # report and close the window
        REPORT["error"] = f"{type(exc).__name__}: {exc}"
        try:
            for probe in ("business_filter_options", "mailing_scripts"):
                REPORT.setdefault("probes", {})[probe] = str(js(
                    f"(async()=>{{try{{const r=await Promise.race([window.pywebview.api.{probe}(''),new Promise(r=>setTimeout(()=>r('TIMEOUT'),5000))]);return JSON.stringify(r).slice(0,300)}}catch(e){{return 'ERR '+e}}}})()"))
            REPORT["probes"]["errors"] = js("window.__qaErrors||[]")
        except Exception as probe_error:
            REPORT["probe_error"] = str(probe_error)
        try:
            capture("99_error.png")
        except Exception:
            pass
    finally:
        (OUT / "tour.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
        window.destroy()


def main():
    fake = seed()
    service = MailingService(DATA, fake, businesses=lambda q, n: db.list_businesses(q, limit=n),
                             business=db.business, templates=db.templates, render=render_template,
                             first_by_site_type=FIRST_BY_SITE_TYPE)
    service.save_policy({"window_start": 0, "window_end": 24, "weekdays_only": False,
                         "delay_min": 45, "delay_max": 50})
    service.start()
    bridge = Bridge()
    bridge._mailing = service
    import clr  # noqa: F401
    window = webview.create_window(APP_NAME, resource_path("app/desktop_ui/index.html").resolve().as_uri(),
                                   js_api=bridge, width=1366, height=900)
    bridge._window = window
    threading.Thread(target=tour, args=(window, service), daemon=True).start()
    webview.start(gui="edgechromium", private_mode=True)
    service.shutdown(10)
    print(json.dumps(REPORT["checks"], ensure_ascii=False))
    print("error:", REPORT.get("error"))


if __name__ == "__main__":
    main()
