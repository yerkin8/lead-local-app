"""The lead workflow behind the Mini App's «Лиды» screen.

One feed with segments instead of the niche → city → run hierarchy, a card that carries
everything needed to decide and write, one-tap writing with a ready text, and soft delete
that can be undone. Writing goes through `outreach` so limits, pause and the «СТОП» footer
still apply.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from . import db, outreach
from .config import DB_PATH
from .script_templates import offers

HIDDEN = """CREATE TABLE IF NOT EXISTS hidden_businesses (
    business_id TEXT PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
    hidden_at TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '')"""
SEGMENTS = ("new", "remind", "written", "talking", "all")
# «Позвонить» is active work like a dialog, so it lives in «В диалоге».
SEGMENT_STATUS = {"new": ("not_started",), "written": ("waiting",), "talking": ("call", "replied", "working"),
                  "remind": ("waiting",)}
SITE_RANK = "CASE b.site_type WHEN 'no_site' THEN 0 WHEN 'weak_site' THEN 1 WHEN 'unavailable' THEN 2 ELSE 3 END"
PAGE = 40
PHONE_DIGITS = "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(b.phones,' ',''),'-',''),'(',''),')',''),'+',''),char(160),'')"


def ensure_schema(path: Path = DB_PATH) -> None:
    with db.connection(path) as conn:
        conn.execute(HIDDEN)
        conn.executescript(outreach.SCHEMA)


def _due_before() -> str:
    return outreach._iso(outreach._now() - outreach.FOLLOWUP_AFTER)


def _where(filters: dict) -> tuple[str, list]:
    segment = filters.get("segment") or "new"
    if segment not in SEGMENTS:
        raise ValueError("Неизвестный раздел")
    conditions = ["NOT EXISTS (SELECT 1 FROM hidden_businesses h WHERE h.business_id=b.id)"]
    args: list = []
    if segment in SEGMENT_STATUS:
        statuses = SEGMENT_STATUS[segment]
        conditions.append(f"b.status IN ({','.join('?' * len(statuses))})")
        args.extend(statuses)
    if segment == "new":
        conditions.append("NOT EXISTS (SELECT 1 FROM suppressions s WHERE s.business_id=b.id)")
    if segment == "remind":
        # Due 3 days after the first message, whether it went through «Написать» or «Написал» was set by hand.
        conditions.append("""(EXISTS (SELECT 1 FROM outreach o WHERE o.business_id=b.id AND o.stage='first'
                AND o.status='sent' AND o.resolved_at<=?)
              OR (NOT EXISTS (SELECT 1 FROM outreach o WHERE o.business_id=b.id AND o.stage='first' AND o.status='sent')
                AND (SELECT MAX(e.changed_at) FROM status_events e WHERE e.business_id=b.id
                     AND e.new_status='waiting')<=?))
            AND NOT EXISTS (SELECT 1 FROM outreach f WHERE f.business_id=b.id AND f.stage='followup'
            AND f.status IN ('opened','sent','stop','replied'))""")
        args.extend([_due_before()] * 2)
    for key in ("niche", "city"):
        if filters.get(key):
            conditions.append(f"EXISTS (SELECT 1 FROM business_tags t WHERE t.business_id=b.id AND t.{key}=?)")
            args.append(str(filters[key]))
    if filters.get("run"):
        conditions.append("EXISTS (SELECT 1 FROM parse_run_businesses r WHERE r.business_id=b.id AND r.parse_run_id=?)")
        args.append(str(filters["run"]))
    if filters.get("site_type"):
        conditions.append("b.site_type=?")
        args.append(str(filters["site_type"]))
    if filters.get("q"):
        q = str(filters["q"]).strip()
        digits = re.sub(r"\D", "", q)
        if len(digits) >= 5:
            # A phone in any format: compare digits only, without the 7/8 country prefix.
            digits = digits[-10:] if len(digits) >= 11 else digits
            conditions.append(f"(b.name LIKE ? OR {PHONE_DIGITS} LIKE ?)")
            args.extend([f"%{q}%", f"%{digits}%"])
        else:
            conditions.append("(b.name LIKE ? OR b.address LIKE ? OR b.phones LIKE ?)")
            args.extend([f"%{q}%"] * 3)
    return " AND ".join(conditions), args


def _order(segment: str) -> str:
    if segment == "new":
        return f"{SITE_RANK}, COALESCE(b.rating,0) DESC, COALESCE(b.reviews,0) DESC, b.created_at DESC"
    return "COALESCE((SELECT MAX(e.changed_at) FROM status_events e WHERE e.business_id=b.id), b.updated_at) DESC"


def counts(filters: dict | None = None, path: Path = DB_PATH) -> dict:
    base = {k: v for k, v in (filters or {}).items() if k in {"niche", "city", "run", "site_type", "q"}}
    result = {}
    with db.connection(path) as conn:
        for segment in SEGMENTS:
            where, args = _where({**base, "segment": segment})
            result[segment] = conn.execute(f"SELECT COUNT(*) FROM businesses b WHERE {where}", args).fetchone()[0]
    return result


def _row(conn, item: dict) -> dict:
    socials = item.pop("socials_json") or "[]"
    phones = item.pop("phones_json") or "[]"
    import json
    merged = {"id": item["id"], "socials": json.loads(socials), "phones": json.loads(phones)}
    db._merge_manual_contacts(conn, merged)   # the owner's own numbers and links count too
    phones = json.dumps(merged["phones"])
    contacts = outreach.contacts(merged, outreach._dead(conn, item["id"]))
    item["contact"] = contacts[0]["contact"] if contacts else ""
    item["channel"] = contacts[0]["channel"] if contacts else ""
    item["has_phone"] = bool(json.loads(phones))
    item["has_vk"] = bool(item.get("has_vk"))
    return item


def list_leads(filters: dict | None = None, path: Path = DB_PATH) -> dict:
    filters = dict(filters or {})
    segment = filters.get("segment") or "new"
    where, args = _where(filters)
    limit = min(100, max(1, int(filters.get("limit", PAGE))))
    offset = max(0, int(filters.get("offset", 0)))
    with db.connection(path) as conn:
        rows = [dict(r) for r in conn.execute(f"""SELECT b.id,b.name,b.category,b.site_type,b.status,b.rating,b.reviews,
                b.address,b.socials AS socials_json,b.phones AS phones_json,
                (SELECT t.city FROM business_tags t WHERE t.business_id=b.id LIMIT 1) AS city,
                (SELECT MAX(COALESCE(o.resolved_at,o.opened_at)) FROM outreach o WHERE o.business_id=b.id
                   AND o.status IN ('sent','replied')) AS written_at,
                EXISTS (SELECT 1 FROM links l WHERE l.business_id=b.id AND l.url LIKE '%vk.com%') AS has_vk
            FROM businesses b WHERE {where} ORDER BY {_order(segment)} LIMIT ? OFFSET ?""", [*args, limit, offset])]
        items = [_row(conn, r) for r in rows]
    return {"segment": segment, "items": items, "more": len(items) == limit, "counts": counts(filters, path)}


def filters(path: Path = DB_PATH) -> dict:
    with db.connection(path) as conn:
        niches = [r[0] for r in conn.execute("SELECT DISTINCT niche FROM business_tags WHERE niche<>'' ORDER BY niche")]
        cities = [r[0] for r in conn.execute("SELECT DISTINCT city FROM business_tags WHERE city<>'' ORDER BY city")]
        runs = [dict(r) for r in conn.execute("""SELECT id,niche,city,created_at,status,
                organization_count AS count FROM parse_runs ORDER BY created_at DESC LIMIT 20""")]
    return {"niches": niches, "cities": cities, "runs": runs}


def _text(business: dict, stage: str, path: Path) -> tuple[str, str]:
    from .domain import strip_links
    slug, text = outreach.script_for(business, stage, path)
    return slug, strip_links(text)   # the card shows exactly what will go: without links


def _stage(conn, business_id: str) -> str:
    """«followup» once the first message went out: through «Написать» or by setting «Написал» by hand."""
    first = conn.execute("""SELECT resolved_at FROM outreach WHERE business_id=? AND stage='first' AND status='sent'
        ORDER BY id DESC LIMIT 1""", (business_id,)).fetchone()
    if first:
        return "followup"
    status = conn.execute("SELECT status FROM businesses WHERE id=?", (business_id,)).fetchone()
    return "followup" if status and status[0] == "waiting" else "first"


def card(actor: str, business_id: str, path: Path = DB_PATH) -> dict:
    item = db.business_full(str(business_id), path)
    if not item:
        raise ValueError("Лид не найден")
    with db.connection(path) as conn:
        hidden = conn.execute("SELECT 1 FROM hidden_businesses WHERE business_id=?", (item["id"],)).fetchone()
        contacts = outreach.contacts(item, outreach._dead(conn, item["id"]))
        stage = _stage(conn, item["id"])
        last = conn.execute("""SELECT id,stage,contact,status,opened_at,resolved_at FROM outreach WHERE business_id=?
            ORDER BY id DESC LIMIT 1""", (item["id"],)).fetchone()
        pending = conn.execute("SELECT id,business_id FROM outreach WHERE actor=? AND status='opened' LIMIT 1",
                               (actor,)).fetchone()
        policy = outreach.settings(path)
        budget = outreach._budget(conn, actor, policy)
        acknowledged = bool(outreach._get(conn, f"outreach_ack:{actor}"))
    slug, text = _text(item, stage, path)
    item.update({"offers": offers(item, db.templates(path))})
    item.update({"hidden": bool(hidden), "contacts": contacts, "stage": stage, "template": slug, "text": text,
                 "footer": outreach.OPT_OUT_FOOTER, "last_outreach": dict(last) if last else None,
                 "pending": dict(pending) if pending else None, "budget": budget, "acknowledged": acknowledged,
                 "can_write": bool(contacts) and not item.get("do_not_contact") and stage != "done"})
    return item


def write(actor: str, business_id: str, contact: str = "", text: str = "", path: Path = DB_PATH) -> dict:
    """«Написать»: reserve a slot and return the chat link with the text filled in."""
    lead = card(actor, business_id, path)
    if not lead["contacts"]:
        raise ValueError("У лида нет телефона с Telegram и @username")
    if lead["pending"] and lead["pending"]["business_id"] != lead["id"]:
        # Close a forgotten previous chat as sent: the admin moved on to the next lead.
        outreach.mark(actor, lead["pending"]["id"], "sent", path)
    elif lead["pending"]:
        outreach.mark(actor, lead["pending"]["id"], "not_sent", path)
    if not lead["acknowledged"]:
        outreach.acknowledge(actor, path)
    chosen = contact or lead["contacts"][0]["contact"]
    return outreach.prepare(actor, lead["id"], lead["stage"], chosen, text or lead["text"], path)


def mark(actor: str, outreach_id: int, result: str, path: Path = DB_PATH) -> dict:
    return outreach.mark(actor, int(outreach_id), result, path)


def correct(actor: str, outreach_id: int, result: str, path: Path = DB_PATH) -> dict:
    """Undo an automatic «sent» from the toast: «Не отправил» or «Нет аккаунта»."""
    if result not in {"not_sent", "no_account"}:
        raise ValueError("Можно исправить только на «Не отправил» или «Нет аккаунта»")
    with db.connection(path) as conn:
        row = conn.execute("SELECT * FROM outreach WHERE id=? AND actor=?", (int(outreach_id), actor)).fetchone()
        if not row:
            raise ValueError("Запись не найдена")
        conn.execute("UPDATE outreach SET status=?,resolved_at=? WHERE id=?", (result, db.now(), row["id"]))
        if result == "no_account":
            conn.execute("INSERT OR IGNORE INTO outreach_dead_contacts VALUES (?,?,?,?)",
                         (row["business_id"], row["contact"], "Нет аккаунта Telegram", db.now()))
        previous = conn.execute("""SELECT old_status FROM status_events WHERE business_id=? AND new_status='waiting'
            ORDER BY id DESC LIMIT 1""", (row["business_id"],)).fetchone()
    if row["stage"] == "first" and previous:
        db.set_business(row["business_id"], previous[0], None, path)
    return {"outreach_id": row["id"], "status": result}


def set_status(business_id: str, status: str, path: Path = DB_PATH) -> dict:
    if not db.set_business(str(business_id), status, None, path):
        raise ValueError("Лид не найден")
    return {"id": str(business_id), "status": status}


def hide(actor: str, ids, path: Path = DB_PATH) -> int:
    ids = [str(i) for i in (ids if isinstance(ids, (list, tuple)) else [ids])][:200]
    with db.connection(path) as conn:
        for business_id in ids:
            conn.execute("INSERT OR IGNORE INTO hidden_businesses VALUES (?,?,?)", (business_id, db.now(), actor))
        conn.execute(f"DELETE FROM outreach_pins WHERE business_id IN ({','.join('?' * len(ids))})", ids)
    return len(ids)


def restore(ids, path: Path = DB_PATH) -> int:
    ids = [str(i) for i in (ids if isinstance(ids, (list, tuple)) else [ids])][:200]
    with db.connection(path) as conn:
        conn.execute(f"DELETE FROM hidden_businesses WHERE business_id IN ({','.join('?' * len(ids))})", ids)
    return len(ids)


def bulk_status(ids, status: str, path: Path = DB_PATH) -> int:
    done = 0
    for business_id in [str(i) for i in ids][:200]:
        done += bool(db.set_business(business_id, status, None, path))
    return done


def summary(actor: str, path: Path = DB_PATH) -> dict:
    """«Итоги»: what was done today and what is next."""
    policy = outreach.settings(path)
    start = outreach._iso(outreach._day_start(datetime.now(timezone.utc)))
    with db.connection(path) as conn:
        budget = outreach._budget(conn, actor, policy)
        # Leads written to today by any admin, whether through «Написать» or by setting «Написал» by hand.
        # Undone (back to «Новый») and deleted leads do not count.
        written_today = conn.execute("""SELECT COUNT(*) FROM businesses b WHERE b.status<>'not_started'
            AND NOT EXISTS (SELECT 1 FROM hidden_businesses h WHERE h.business_id=b.id)
            AND (EXISTS (SELECT 1 FROM status_events e WHERE e.business_id=b.id AND e.new_status='waiting'
                         AND e.changed_at>=?)
              OR EXISTS (SELECT 1 FROM outreach o WHERE o.business_id=b.id AND o.status IN ('opened','sent','replied')
                         AND COALESCE(o.resolved_at,o.opened_at)>=?))""", (start, start)).fetchone()[0]
        replies_today = conn.execute("""SELECT COUNT(*) FROM status_events WHERE new_status='replied'
            AND changed_at>=?""", (start,)).fetchone()[0]
    c = counts({}, path)
    return {"budget": budget, "written_today": written_today, "replies_today": replies_today,
            "new": c["new"], "remind": c["remind"], "talking": c["talking"], "written": c["written"]}
