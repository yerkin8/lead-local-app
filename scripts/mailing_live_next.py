"""Live mailing chain on a COPY of the owner's data (docs/next, task 4). Never the real %APPDATA% folder.

    .build-venv\\Scripts\\python.exe scripts\\mailing_live_next.py <copy of %APPDATA%\\LeadEqualMoney> @first @second

Only the owner's test account (is_test, active) sends, only to the owner's test recipients given on the
command line. Chain: batch of 3 real leads → texts → test campaign (dry run, then live) → result per
business → «Ответили» in «Лиды» and «Убрать из пачки» → repeat with the reminder (live) → result.
At most 4 messages. In the copy only: sending window 0–24 every day and a 45–60 s pause, so the check
does not wait for Monday 10:00. Prints a report without phones, keys or full usernames.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def mask_username(value: str) -> str:
    value = str(value or "").lstrip("@")
    return "@" + value[:2] + "***" if value else ""


def main() -> int:
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    copy = Path(sys.argv[1]).resolve()
    real = (Path(os.environ.get("APPDATA", "")) / "LeadEqualMoney").resolve()
    if copy == real or real in copy.parents or not (copy / "mailing" / "mailing.sqlite3").is_file():
        raise SystemExit("Нужна копия папки данных, не сама папка %APPDATA%\\LeadEqualMoney")
    targets = [item.lstrip("@") for item in sys.argv[2:4]]
    os.environ["DATA_DIR"] = str(copy)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app import db
    from app.mailing.desktop_bridge import create_service

    db.init_desktop_db()
    report: list[str] = []

    def note(text: str) -> None:
        report.append(text)
        print(text, flush=True)

    service = create_service(copy)
    service.start()
    try:
        service.set_test_recipients(targets)
        service.save_policy({"weekdays_only": False, "window_start": 0, "window_end": 24,
                             "delay_min": 45, "delay_max": 60})
        accounts = [a for a in service.store.rows("SELECT id,label,is_test,status,phone_mask FROM accounts")
                    if a["is_test"] and a["status"] == "active" and str(a["phone_mask"]).endswith("69")]
        if len(accounts) != 1:
            note("Нет единственного активного тестового аккаунта …69 — проверка остановлена")
            return 2
        account = accounts[0]["id"]
        limit = next(a for a in service.accounts() if a["id"] == account)
        note(f"Тестовый аккаунт: «{accounts[0]['label']}», сегодня можно {limit['limit_today'] - limit['sent_today']}")
        if limit["limit_today"] - limit["sent_today"] < 4:
            note("Дневной лимит тестового аккаунта меньше 4 сообщений — проверка остановлена")
            return 2

        leads = [b for b in db.list_businesses({}, limit=200) if b.get("status") == "not_started"][:3]
        ids = [b["id"] for b in leads]
        statuses_before = {i: db.business(i)["status"] for i in ids}
        note(f"1. Пачка из «Лидов»: {len(ids)} бизнеса — " + "; ".join(b["name"] for b in leads))

        campaign = service.create_campaign("Живая проверка · пачка", {"ids": ids}, "auto", [account], kind="test")
        texts = [r["message"].splitlines()[0][:70] for r in campaign["recipients"]]
        note("2. Тексты подставлены каждому (первая строка): " + " | ".join(texts))
        note("   Строка «СТОП» есть у всех: " + str(all("СТОП" in r["message"] for r in campaign["recipients"])))
        dry = service.dry_run(campaign["id"])
        note(f"3. Сухой запуск: ушло бы {dry['counts']['dry_sent']}, отправлено {dry['counts']['sent']}")
        try:
            service.enable_live(campaign["id"], "да")
            note("   ОШИБКА: реальная отправка включилась без слова подтверждения")
        except ValueError:
            note("   Без слова ОТПРАВИТЬ реальная отправка не включается — верно")
        service.enable_live(campaign["id"], "ОТПРАВИТЬ")
        started = time.monotonic()
        service.run(campaign["id"])
        detail = wait_done(service, campaign["id"])
        note(f"4. Реальная отправка: {detail['status']}, {round(time.monotonic() - started)} с")
        for r in detail["recipients"]:
            note(f"   {r['business_name']}: {result_label(r)} → {mask_username(r['username'])}"
                 f"{' · сообщение № есть' if r['msg_id'] else ''}")
        unchanged = all(db.business(i)["status"] == statuses_before[i] for i in ids)
        note(f"5. Тестовая кампания статусы «Лидов» не меняет: {unchanged}")

        db.set_business(ids[1], "replied", None)                       # owner: «Ответили» in «Лиды»
        service.exclude(campaign["id"], ids[2], "Проверка: убран вручную")
        plan = service.repeat_preview(campaign["id"])
        note(f"6. Повтор: получат {len(plan['include'])}, не попадут {len(plan['skip'])}: "
             + "; ".join(f"{x['name']} — {x['reason']}" for x in plan["skip"]))
        repeat = service.repeat(campaign["id"], "auto:reminder", live=True, confirm="ОТПРАВИТЬ")
        started = time.monotonic()
        detail = wait_done(service, repeat["id"])
        note(f"7. Напоминание: {detail['status']}, этап «{detail['stage']}», {round(time.monotonic() - started)} с")
        for r in detail["recipients"]:
            note(f"   {r['business_name']}: {result_label(r)} → {mask_username(r['username'])} · "
                 f"текст: {r['message'].splitlines()[0][:60]}")
        batch = service.campaign(campaign["id"])["batch"]
        note("8. Пачка: " + "; ".join(f"№{x['id']} {x['stage']} — отправлено {x['counts']['sent']}" for x in batch))
        sent_total = sum(x["counts"]["sent"] for x in batch)
        note(f"Всего реальных сообщений: {sent_total} (только тестовым адресатам)")
        return 0
    finally:
        service.shutdown(20)
        (copy / "live-next-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


def wait_done(service, campaign_id: int, timeout: float = 900) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        detail = service.campaign(campaign_id)
        if detail["status"] in ("done", "stopped", "paused") and not detail["counts"]["queued"] + detail["counts"]["sending"]:
            return detail
        if detail["status"] in ("stopped", "paused"):
            return detail
        time.sleep(5)
    return service.campaign(campaign_id)


def result_label(r: dict) -> str:
    if r.get("replied_at"):
        return "Ответил"
    return {"sent": "Отправлено", "dry_sent": "Сухой запуск", "queued": "В очереди", "sending": "Отправляется",
            "unreachable": "Не дошло", "failed": "Не дошло"}.get(r["status"],
            "В стоп-листе" if r.get("reason_kind") == "stoplist" else f"Пропущено ({r.get('reason')})")


if __name__ == "__main__":
    raise SystemExit(main())
