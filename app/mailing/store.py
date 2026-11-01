"""SQLite state of the mailing module (separate file, WAL)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .logsafe import scrub

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    is_test INTEGER NOT NULL DEFAULT 0,
    phone_mask TEXT NOT NULL,
    phone_key TEXT NOT NULL UNIQUE,          -- sha256 of digits, for duplicates only
    tg_user_id TEXT,
    status TEXT NOT NULL DEFAULT 'needs_login'
        CHECK(status IN ('needs_login','active','paused','quarantine')),
    status_reason TEXT NOT NULL DEFAULT '',
    paused_until TEXT,
    first_send_day TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'normal' CHECK(kind IN ('normal','test')),
    mode TEXT NOT NULL DEFAULT 'dry' CHECK(mode IN ('dry','live')),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft','running','paused','stopped','done')),
    template TEXT NOT NULL,
    filters_json TEXT NOT NULL DEFAULT '{}',
    stop_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS campaign_accounts (
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    PRIMARY KEY(campaign_id, account_id)
);
CREATE TABLE IF NOT EXISTS recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    business_id TEXT NOT NULL,
    business_name TEXT NOT NULL,
    account_id INTEGER REFERENCES accounts(id),
    username TEXT,                 -- send target (casefold, without @)
    phones TEXT NOT NULL DEFAULT '',  -- normalized digits, comma separated (dedupe, stop-list)
    dedupe_key TEXT NOT NULL,
    message TEXT NOT NULL,
    tz TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK(status IN ('queued','sending','sent','dry_sent','skipped','unreachable','failed')),
    reason_kind TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    telegram_user_id TEXT,
    msg_id INTEGER,
    sent_at TEXT,
    replied_at TEXT,
    position INTEGER NOT NULL,
    UNIQUE(campaign_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_recipients_queue ON recipients(campaign_id, account_id, status, position);
CREATE INDEX IF NOT EXISTS idx_recipients_user ON recipients(telegram_user_id);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE CASCADE,
    account_id INTEGER,
    random_id INTEGER NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('dry','live')),
    status TEXT NOT NULL CHECK(status IN ('sending','sent','failed','dry')),
    error_kind TEXT NOT NULL DEFAULT '',
    error_detail TEXT NOT NULL DEFAULT '',
    wait_seconds INTEGER,
    msg_id INTEGER,
    server_response TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_attempts_account_time ON attempts(account_id, mode, started_at);
CREATE TABLE IF NOT EXISTS peers (
    account_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    telegram_user_id TEXT,
    access_hash TEXT,
    kind TEXT NOT NULL,                    -- user | unreachable
    reason_kind TEXT NOT NULL DEFAULT '',
    resolved_at TEXT NOT NULL,
    PRIMARY KEY(account_id, username)
);
CREATE TABLE IF NOT EXISTS unreachable (
    target TEXT PRIMARY KEY,              -- username:<name> or user:<id>
    reason_kind TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stoplist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('business','username','phone','telegram_user')),
    value TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL CHECK(source IN ('manual','reply')),
    created_at TEXT NOT NULL,
    UNIQUE(kind, value)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL, level TEXT NOT NULL, kind TEXT NOT NULL,
    account_id INTEGER, campaign_id INTEGER, recipient_id INTEGER,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batch_exclusions (   -- «Убрать из пачки»: never in a repeat of this batch
    batch_id INTEGER NOT NULL,
    business_id TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY(batch_id, business_id)
);
"""

TERMINAL = ("sent", "dry_sent", "skipped", "unreachable", "failed")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds") if moment else None


def parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(recipients)")}
            if "edited" not in columns:  # per-recipient text edited by the owner (redesign 02)
                conn.execute("ALTER TABLE recipients ADD COLUMN edited INTEGER NOT NULL DEFAULT 0")
            campaign_columns = {row[1] for row in conn.execute("PRAGMA table_info(campaigns)")}
            # Batches and repeats (next/03): the first campaign of a batch is its root.
            for name, ddl in (("batch_id", "INTEGER"), ("parent_id", "INTEGER"),
                              ("stage", "TEXT NOT NULL DEFAULT 'first'")):
                if name not in campaign_columns:
                    conn.execute(f"ALTER TABLE campaigns ADD COLUMN {name} {ddl}")
            conn.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def tx(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def rows(self, sql: str, args=()) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, args)]

    def row(self, sql: str, args=()) -> dict | None:
        found = self.rows(sql, args)
        return found[0] if found else None

    def value(self, sql: str, args=()):
        with self.connect() as conn:
            item = conn.execute(sql, args).fetchone()
            return item[0] if item else None

    # settings ----------------------------------------------------------
    def setting(self, key: str, default=None):
        raw = self.value("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(raw) if raw is not None else default

    def set_setting(self, key: str, value) -> None:
        with self.tx() as conn:
            conn.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, json.dumps(value)))

    # events ------------------------------------------------------------
    def event(self, kind: str, message: str, *, level: str = "info", account_id=None,
              campaign_id=None, recipient_id=None, conn=None) -> None:
        args = (iso(utcnow()), level, kind, account_id, campaign_id, recipient_id, scrub(message)[:1000])
        sql = ("INSERT INTO events (at,level,kind,account_id,campaign_id,recipient_id,message) "
               "VALUES (?,?,?,?,?,?,?)")
        if conn is not None:
            conn.execute(sql, args)
        else:
            with self.tx() as own:
                own.execute(sql, args)

    # stop-list ---------------------------------------------------------
    def stop_add(self, kind: str, value: str, label: str = "", source: str = "manual") -> bool:
        value = str(value).strip().lstrip("@").casefold()
        if not value:
            raise ValueError("Пустое значение для стоп-листа")
        with self.tx() as conn:
            changed = conn.execute("INSERT OR IGNORE INTO stoplist (kind,value,label,source,created_at) "
                                   "VALUES (?,?,?,?,?)", (kind, value, label[:200], source, iso(utcnow()))).rowcount
            # Anything already queued for this target is withdrawn right away.
            column = {"business": "business_id", "username": "username",
                      "telegram_user": "telegram_user_id"}.get(kind)
            if column:
                conn.execute(f"UPDATE recipients SET status='skipped',reason_kind='stoplist',"
                             f"reason='В стоп-листе' WHERE status='queued' AND lower({column})=?", (value,))
            elif kind == "phone":
                conn.execute("UPDATE recipients SET status='skipped',reason_kind='stoplist',reason='В стоп-листе' "
                             "WHERE status='queued' AND (','||phones||',') LIKE ?", (f"%,{value},%",))
        return bool(changed)

    def stop_remove(self, entry_id: int) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM stoplist WHERE id=?", (int(entry_id),))

    def stop_set(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {"business": set(), "username": set(), "phone": set(), "telegram_user": set()}
        for item in self.rows("SELECT kind,value FROM stoplist"):
            result[item["kind"]].add(item["value"])
        return result
