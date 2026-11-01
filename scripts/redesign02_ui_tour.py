"""Redesign 02: campaign from «Лиды» with per-recipient texts, in the real desktop window.

Synthetic data in a TEMP DATA_DIR, FakeGateway (no Telegram traffic, no login, no real sessions).
Output: verification/redesign/02/*.png and tour.json.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.gettempdir()) / "lem-redesign02-tour"
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
from scripts import mailing_ui_tour as base  # noqa: E402

OUT = ROOT / "verification" / "redesign" / "02"
OUT.mkdir(parents=True, exist_ok=True)
base.OUT = OUT
REPORT: dict = {"steps": [], "checks": {}}


def seed() -> FakeGateway:
    fake = base.seed()  # 8 coffee shops in Kazan with t.me usernames
    return fake


def tour(window, service: MailingService):
    js = window.evaluate_js

    def wait(expr: str, timeout: float = 20) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if js(f"!!({expr})"):
                return
            time.sleep(0.25)
        raise RuntimeError(f"timeout: {expr}")

    def click(selector: str) -> None:
        js(f"document.querySelector({json.dumps(selector)}).click()")
        time.sleep(0.7)

    def step(name: str, png: str) -> None:
        time.sleep(0.8)
        base.capture(png)
        REPORT["steps"].append({"step": name, "screenshot": png, "errors": js("window.__qaErrors||[]")})

    try:
        wait("window.pywebview && window.pywebview.api && typeof state!=='undefined' && state.config")
        js("window.__qaErrors=[];window.addEventListener('error',e=>__qaErrors.push(e.message))")
        click("nav button[data-view=leads]")
        wait("document.querySelectorAll('.lead-row').length>=8")
        step("Лиды: кнопка «Разослать этой пачке»", "01_leads_batch_button.png")
        click("[data-lead-mailing]")
        wait("document.querySelector('#ml-compose-form')")
        REPORT["checks"]["auto_script_default"] = js("document.querySelector('#ml-compose-form [name=script]').value") == "auto"
        REPORT["checks"]["batch_size_shown"] = js("document.querySelector('#ml-compose-form h2').textContent")
        step("Пачка: скрипт авто, аккаунт, слово подтверждения", "02_batch_compose.png")
        click("[data-ml=compose-review]")
        wait("document.querySelector('#ml-recipients .ml-edit')")
        campaign_id = js("state.mailingCampaign")
        detail = service.campaign(campaign_id)
        REPORT["checks"]["draft_recipients"] = len(detail["recipients"])
        REPORT["checks"]["texts_differ_by_site_type"] = len({r["message"].split(chr(10))[0][:25] for r in detail["recipients"]}) > 1
        step("Необязательный просмотр: тексты подставлены каждому", "03_review_texts.png")
        js("document.querySelector('#ml-recipients .ml-edit textarea').value='Добрый день! Это личный текст для первой кофейни.'")
        click("[data-ml-msg-save]")
        wait("document.querySelector('[data-ml-msg-reset]')")
        first = service.campaign(campaign_id)["recipients"][0]
        REPORT["checks"]["edit_saved_in_db"] = first["edited"] == 1 and first["message"].startswith("Добрый день! Это личный")
        REPORT["checks"]["stop_line_kept"] = "СТОП" in first["message"]
        step("Текст одного получателя изменён", "04_edited.png")
        click("[data-ml-msg-reset]")
        time.sleep(1)
        REPORT["checks"]["reset_to_template"] = service.campaign(campaign_id)["recipients"][0]["edited"] == 0
        # one-button launch from «Лиды» (FakeGateway: no Telegram traffic)
        js("setView('leads')")
        wait("document.querySelector('[data-lead-mailing]')")
        click("[data-lead-mailing]")
        wait("document.querySelector('#ml-compose-form')")
        js("window.confirm=()=>true;document.querySelector('#ml-compose-form [name=confirm]').value='ОТПРАВИТЬ'")
        click("#ml-compose-form button.primary")
        wait("state.mailingCampaign && state.mailingCampaign!==%d" % campaign_id)
        launched = service.campaign(js("state.mailingCampaign"))
        REPORT["checks"]["one_button_launch"] = {"mode": launched["mode"], "status": launched["status"],
                                                 "total": launched["counts"]["total"]}
        step("Запуск одной кнопкой: кампания идёт", "05_launched.png")
        js("setView('mailing',{mailingTab:'campaigns',mailingCampaign:null})")
        wait("document.querySelector('[data-ml=to-leads]')")
        step("Рассылка: кампании, история", "06_campaigns.png")
        js("document.querySelector('#content').innerHTML='<div id=\"mlset\"></div>';mailingSettingsBlock(document.querySelector('#mlset'))")
        wait("document.querySelector('.ml-settings-form')")
        js("document.querySelector('.ml-settings-form [name=batch_limit]').value='5';document.querySelector('.ml-settings-form button.primary').click()")
        time.sleep(1.5)
        REPORT["checks"]["settings_block_saves"] = service.policy().batch_limit == 5
        step("Блок настроек: тайминг и количество", "07_settings_block.png")
        REPORT["checks"]["js_errors"] = js("window.__qaErrors||[]")
    except Exception as exc:
        REPORT["error"] = f"{type(exc).__name__}: {exc}"
        try:
            base.capture("99_error.png")
        except Exception:
            pass
    finally:
        (OUT / "tour.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
        window.destroy()


base_fake: FakeGateway


def main():
    global base_fake
    base_fake = seed()
    service = MailingService(DATA, base_fake, businesses=lambda q, n: db.list_businesses(q, limit=n),
                             business=db.business, templates=db.templates, render=render_template,
                             first_by_site_type=FIRST_BY_SITE_TYPE)
    service.save_keys(31415926, "0123456789abcdef0123456789abcdef")
    account_id = service.add_account("main-1", "+79005550198", False)["id"]
    service._save_session(account_id, "fake-session")
    with service.store.tx() as conn:
        conn.execute("UPDATE accounts SET status='active' WHERE id=?", (account_id,))
    service.save_policy({"window_start": 0, "window_end": 24, "weekdays_only": False, "delay_min": 45, "delay_max": 50})
    service.start()
    bridge = Bridge()
    bridge._mailing = service
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
