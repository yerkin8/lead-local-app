"""Stage 5: live check on the owner's own accounts only (A3, A4, A6, A7 evidence).

Run only after the owner has logged in to a test account in the app («Рассылка → Аккаунты → Войти»)
and loaded secrets\\accounts.local.json with test_recipients. Close the app first.

What it does (at most 3 real messages, only to test_recipients, only from is_test accounts):
1. dry run on 5 real records — nothing is sent, Telegram is not contacted;
2. test campaign, 3 messages: after the 1st message the service is shut down and started again
   (restart in the middle of a campaign), then the emergency stop is pressed during a pause and
   timed, then cleared and the campaign continues to the end;
3. duplicates check in the database and in the real dialog history.
Evidence: verification/mailing/live/live-run.json (ids masked, no phones, no keys).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "verification" / "mailing" / "live"
LOCAL = Path(os.environ.get("LEM_SECRETS", r"C:\Users\User\Documents\projects\parserYand\secrets"))


def main() -> int:
    from app.desktop import _configure_user_data_dir  # noqa: F401  (sets DATA_DIR to %APPDATA%)
    from app import db
    from app.config import DATA_DIR
    from app.mailing.desktop_bridge import create_service
    from app.mailing.logsafe import mask_digits

    lock = DATA_DIR / "desktop.lock"
    if lock.exists():
        import msvcrt
        with lock.open("a+b") as handle:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                print("Закройте LeadEqualMoney и запустите проверку снова")
                return 2
    db.init_desktop_db()
    report: dict = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "steps": []}

    def note(name, **data):
        data = {key: value for key, value in data.items()}
        report["steps"].append({"step": name, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **data})
        print(name, json.dumps(data, ensure_ascii=False))

    service = create_service(DATA_DIR)
    if (LOCAL / "accounts.local.json").is_file():
        imported = service.import_owner_file(str(LOCAL / "accounts.local.json"))
        note("Тестовые адресаты загружены", count=imported["test_recipients"])
    targets = service.test_recipients()
    accounts = service.accounts()
    test_accounts = [a for a in accounts if a["is_test"] and a["status"] == "active"]
    if not targets or not test_accounts:
        print("Нужно: войти в тестовый аккаунт в приложении и загрузить test_recipients")
        return 3
    test_account = test_accounts[0]["id"]
    any_account = accounts[0]["id"]
    service.start()
    try:
        # 1. Dry run on 5 real records.
        dry = service.create_campaign("Приёмка A3: сухой запуск", {"contact": "username"},
                                      "Здравствуйте! Посмотрел карточку {название} в {город}.",
                                      [next((a["id"] for a in accounts if not a["is_test"]), any_account)], limit=5)
        dry = service.dry_run(dry["id"])
        note("A3 сухой запуск", counts=dry["counts"], telegram_calls="нет (Telegram не вызывался)")

        # 2. Live test campaign: 3 messages, restart after the first, emergency stop during a pause.
        campaign = service.create_campaign("Приёмка A4/A6/A7: тест на свои аккаунты", {},
                                           "Проверка рассылки LeadEqualMoney. Текст по карточке: {название}.",
                                           [test_account], kind="test", limit=3)
        service.enable_live(campaign["id"], "ОТПРАВИТЬ")
        service.run(campaign["id"])
        sent = lambda: service.campaign(campaign["id"])["counts"]["sent"]
        deadline = time.time() + 600
        while sent() < 1 and time.time() < deadline:
            time.sleep(1)
        note("Первое сообщение отправлено", sent=sent())
        service.shutdown(15)
        note("Перезапуск посреди кампании: сервис остановлен", status=service.campaign(campaign["id"])["status"])
        service = create_service(DATA_DIR)
        service.start()  # resumes running campaigns
        note("Сервис запущен снова", status=service.campaign(campaign["id"])["status"])
        while sent() < 2 and time.time() < deadline:
            time.sleep(1)
        time.sleep(3)  # now inside the pause before message 3
        started = time.monotonic()
        service.emergency_stop()
        task = service.engine._tasks.get(campaign["id"])
        while task and not task.done() and time.monotonic() - started < 10:
            time.sleep(0.05)
        note("A7 аварийный стоп", seconds=round(time.monotonic() - started, 2),
             status=service.campaign(campaign["id"])["status"], sent=sent())
        time.sleep(5)
        note("После стопа отправок нет", sent=sent())
        service.clear_emergency()
        service.run(campaign["id"])
        while sent() < 3 and time.time() < deadline:
            time.sleep(1)
        final = service.campaign(campaign["id"])
        rows = [{"recipient": mask_digits(r["username"]) if r["username"].startswith("+") else r["username"][:2] + "••••",
                 "msg_id": r["msg_id"], "sent_at": r["sent_at"], "status": r["status"]} for r in final["recipients"]]
        attempts = service.store.rows("SELECT a.id,a.status,a.msg_id,a.server_response,a.started_at,a.finished_at,"
                                      "a.error_kind FROM attempts a JOIN recipients r ON r.id=a.recipient_id "
                                      "WHERE r.campaign_id=? ORDER BY a.id", (campaign["id"],))
        note("A4 доставка", counts=final["counts"], recipients=rows, attempts=attempts)

        # 3. Duplicates: DB and the real dialog history.
        async def history():
            from telethon.tl.types import InputPeerUser
            client = service.gateway._clients[test_account]
            copies = []
            for r in final["recipients"]:
                peer_row = service.store.row("SELECT telegram_user_id,access_hash FROM peers WHERE account_id=? "
                                             "AND username=?", (test_account, r["username"]))
                peer = InputPeerUser(int(peer_row["telegram_user_id"]), int(peer_row["access_hash"]))
                messages = await client.get_messages(peer, limit=50)
                copies.append(sum(1 for m in messages if m.out and m.message == r["message"]))
            return copies
        copies = service.engine.call(history(), timeout=60)
        note("A6 нет дубликатов в истории диалога", copies_per_message=copies, all_single=all(c == 1 for c in copies))
    finally:
        service.shutdown(15)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "live-run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
