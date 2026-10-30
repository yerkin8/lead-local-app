"""Campaign runner. All sends go through here; every send is recorded before the network call."""

from __future__ import annotations

import asyncio
import random
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable

from . import gateway as gw
from .gateway import GatewayError, Peer, TelegramGateway
from .logsafe import get_logger, scrub
from .policy import DEFAULT_TZ, Policy, zone
from .store import Store, iso, parse, utcnow

log = get_logger()
STOP_WORDS = re.compile(r"\b(стоп|stop|не\s*пиш\w*|отпиш\w*|не\s*интересн\w*|удалите\s+меня)\b", re.I)


def local_day(moment: datetime) -> date:
    """Account days are counted in Moscow time (owner's day)."""
    return moment.astimezone(zone(DEFAULT_TZ)).date()


@dataclass
class Credentials:
    api_id: int
    api_hash: str
    session: str


CredentialsProvider = Callable[[int], Credentials]
SessionSaver = Callable[[int, str], None]


class Engine:
    def __init__(self, store: Store, gateway: TelegramGateway, *,
                 credentials: CredentialsProvider, save_session: SessionSaver | None = None,
                 policy: Callable[[], Policy] | None = None,
                 test_recipients: Callable[[], set[str]] | None = None,
                 external_blocked: Callable[[], set[str]] | None = None,
                 clock: Callable[[], datetime] = utcnow,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 tick: float = 0.25, rng: random.Random | None = None):
        self.store = store
        self.gateway = gateway
        self.credentials = credentials
        self.save_session = save_session or (lambda _account, _session: None)
        self.policy = policy or Policy
        self.test_recipients = test_recipients or (lambda: set())
        self.external_blocked = external_blocked or (lambda: set())
        self.clock = clock
        self.sleep = sleep
        self.tick = tick
        self.rng = rng or random.SystemRandom()
        self._connected: set[int] = set()
        self._account_locks: dict[int, asyncio.Lock] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        gateway.set_incoming_handler(self.on_incoming)

    # ------------------------------------------------------------------ thread
    def start_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        ready = threading.Event()

        def main():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            ready.set()
            self._loop.run_forever()
            self._loop.close()

        self._thread = threading.Thread(target=main, name="Mailing engine", daemon=True)
        self._thread.start()
        ready.wait(10)
        self.call(self.resume_running(), timeout=30)

    def call(self, coroutine, timeout: float = 90):
        """Run a coroutine on the engine loop from another thread and wait for the result."""
        if not self._loop:
            raise RuntimeError("Модуль рассылки не запущен")
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result(timeout)

    def shutdown(self, timeout: float = 15) -> bool:
        if not self._loop:
            return True

        async def stop_all():
            await self.cancel_all()
            for account_id in list(self._connected):
                try:
                    await self.gateway.disconnect(account_id)
                except Exception:
                    pass
            self._connected.clear()

        try:
            self.call(stop_all(), timeout=timeout)
        except Exception:
            log.exception("Остановка модуля рассылки прошла с ошибкой")
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout)
        alive = bool(self._thread and self._thread.is_alive())
        self._loop = None
        return not alive

    # --------------------------------------------------------------- control
    async def resume_running(self) -> list[int]:
        """After a restart, continue campaigns that were running when the app closed."""
        if self.store.setting("emergency_stop", False):
            return []
        ids = [row["id"] for row in self.store.rows(
            "SELECT id FROM campaigns WHERE status='running' AND mode='live'")]
        for campaign_id in ids:
            self.store.event("resume", "Кампания продолжена после перезапуска", campaign_id=campaign_id)
            self.launch(campaign_id)
        return ids

    def launch(self, campaign_id: int) -> None:
        task = self._tasks.get(campaign_id)
        if task and not task.done():
            return
        self._tasks[campaign_id] = asyncio.ensure_future(self.run_campaign(campaign_id))

    async def cancel_all(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cancel_campaign(self, campaign_id: int) -> None:
        task = self._tasks.get(campaign_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _campaign_active(self, campaign_id: int) -> bool:
        if self.store.setting("emergency_stop", False):
            return False
        return self.store.value("SELECT status FROM campaigns WHERE id=?", (campaign_id,)) == "running"

    # -------------------------------------------------------------- campaign
    async def run_campaign(self, campaign_id: int) -> None:
        accounts = [row["account_id"] for row in self.store.rows(
            "SELECT DISTINCT account_id FROM recipients WHERE campaign_id=? AND account_id IS NOT NULL",
            (campaign_id,))]
        try:
            await asyncio.gather(*(self._account_worker(campaign_id, account) for account in accounts))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # unexpected: stop the campaign, never loop on errors
            log.exception("Кампания %s остановлена из-за ошибки", campaign_id)
            self._set_campaign(campaign_id, "paused", f"Ошибка модуля: {type(exc).__name__}")
            return
        remaining = self.store.value(
            "SELECT COUNT(*) FROM recipients WHERE campaign_id=? AND status IN ('queued','sending')",
            (campaign_id,))
        if not remaining and self._campaign_active(campaign_id):
            self._set_campaign(campaign_id, "done", "")
            self.store.event("done", "Кампания завершена", campaign_id=campaign_id)

    def _set_campaign(self, campaign_id: int, status: str, reason: str, conn=None) -> None:
        sql = ("UPDATE campaigns SET status=?,stop_reason=?,updated_at=?,"
               "finished_at=CASE WHEN ?='done' THEN ? ELSE finished_at END WHERE id=?")
        now = iso(self.clock())
        args = (status, reason, now, status, now, campaign_id)
        if conn is not None:
            conn.execute(sql, args)
        else:
            with self.store.tx() as own:
                own.execute(sql, args)

    async def _account_worker(self, campaign_id: int, account_id: int) -> None:
        network_failures = 0
        while self._campaign_active(campaign_id):
            account = self.store.row("SELECT * FROM accounts WHERE id=?", (account_id,))
            if account and account["status"] == "paused" and parse(account["paused_until"])                     and parse(account["paused_until"]) <= self.clock():
                with self.store.tx() as conn:
                    conn.execute("UPDATE accounts SET status='active',status_reason='',paused_until=NULL "
                                 "WHERE id=? AND status='paused'", (account_id,))
                self.store.event("resumed", "Пауза аккаунта закончилась", account_id=account_id)
                continue
            if not account or account["status"] in ("quarantine", "needs_login"):
                self._stop_for_account(account_id, account["status_reason"] if account else "Аккаунт удалён")
                return
            recipient = self.store.row(
                "SELECT * FROM recipients WHERE campaign_id=? AND account_id=? AND status IN ('sending','queued') "
                "ORDER BY CASE status WHEN 'sending' THEN 0 ELSE 1 END, position LIMIT 1",
                (campaign_id, account_id))
            if not recipient:
                return
            campaign = self.store.row("SELECT * FROM campaigns WHERE id=?", (campaign_id,))
            if recipient["status"] == "queued":
                verdict = self.precheck(campaign, account, recipient)
                if verdict:
                    self._close_recipient(recipient["id"], *verdict)
                    continue
            due = self.earliest_send(account, recipient)
            if due > self.clock():
                if not await self._wait_until(due, campaign_id):
                    return
                continue
            lock = self._account_locks.setdefault(account_id, asyncio.Lock())
            try:
                async with lock:
                    if not self._campaign_active(campaign_id):
                        return
                    await self._ensure_connected(account_id)
                    await self.send_one(campaign, account, recipient)
                network_failures = 0
            except _NetworkTrouble:
                network_failures += 1
                if network_failures >= 3:
                    self._set_campaign(campaign_id, "paused", "Нет связи с Telegram. Проверьте интернет и продолжите")
                    self.store.event("network", "Три попытки без ответа Telegram, кампания на паузе",
                                     level="warning", account_id=account_id, campaign_id=campaign_id)
                    return
                if not await self._wait_until(self.clock() + timedelta(seconds=30 * network_failures), campaign_id):
                    return
            except _AccountStopped:
                return

    async def _wait_until(self, due: datetime, campaign_id: int) -> bool:
        while self.clock() < due:
            if not self._campaign_active(campaign_id):
                return False
            await self.sleep(min(self.tick, max(0.0, (due - self.clock()).total_seconds())))
        return self._campaign_active(campaign_id)

    # ---------------------------------------------------------------- checks
    def precheck(self, campaign: dict, account: dict, recipient: dict) -> tuple[str, str, str] | None:
        """Return (status, reason_kind, reason) when the recipient must not get a message."""
        stop = self.store.stop_set()
        username = (recipient["username"] or "").casefold()
        phones = {item for item in (recipient["phones"] or "").split(",") if item}
        if recipient["business_id"] in stop["business"] or recipient["business_id"] in self.external_blocked():
            return "skipped", "stoplist", "Организация в стоп-листе"
        if campaign["kind"] == "normal":
            if username and username in stop["username"]:
                return "skipped", "stoplist", "Username в стоп-листе"
            if phones & stop["phone"]:
                return "skipped", "stoplist", "Номер в стоп-листе"
            if recipient["telegram_user_id"] and recipient["telegram_user_id"] in stop["telegram_user"]:
                return "skipped", "stoplist", "Аккаунт в стоп-листе"
            if self.store.value("SELECT 1 FROM unreachable WHERE target=?", (f"username:{username}",)):
                return "unreachable", "known_unreachable", "Ранее недоступен, повторов нет"
            already = self.store.value(
                "SELECT 1 FROM recipients r JOIN campaigns c ON c.id=r.campaign_id "
                "WHERE c.kind='normal' AND r.status='sent' AND r.id<>? AND "
                "(r.business_id=? OR (r.username IS NOT NULL AND r.username=?)) LIMIT 1",
                (recipient["id"], recipient["business_id"], username))
            if already:
                return "skipped", "already_sent", "Этой организации уже писали"
        if account["is_test"] and username not in self.test_recipients():
            if not self.store.value("SELECT 1 FROM events WHERE kind='test_guard' AND campaign_id=?", (campaign["id"],)):
                self.store.event("test_guard", "Тестовый аккаунт может писать только тестовым адресатам",
                                 level="warning", account_id=account["id"], campaign_id=campaign["id"])
            return "skipped", "test_guard", "Тестовый аккаунт пишет только тестовым адресатам"
        if not username:
            return "skipped", "no_contact", "Нет username Telegram"
        return None

    def earliest_send(self, account: dict, recipient: dict, *, now: datetime | None = None,
                      sent_today: int | None = None, last_hour: list[datetime] | None = None,
                      last_send: datetime | None = None) -> datetime:
        policy = self.policy()
        now = now or self.clock()
        candidates = [now]
        paused = parse(account["paused_until"])
        if paused and paused > now:
            candidates.append(paused)
        day_start = policy.end_of_day(now) - timedelta(days=1)
        if sent_today is None:
            sent_today = self.store.value(
                "SELECT COUNT(*) FROM attempts WHERE account_id=? AND mode='live' AND status IN ('sent','sending') "
                "AND started_at>=?", (account["id"], iso(day_start)))
        first_day = date.fromisoformat(account["first_send_day"]) if account["first_send_day"] else None
        limit = policy.daily_limit(first_day, local_day(now))
        if sent_today >= limit:
            candidates.append(policy.end_of_day(now))
        if last_hour is None:
            last_hour = [parse(row["started_at"]) for row in self.store.rows(
                "SELECT started_at FROM attempts WHERE account_id=? AND mode='live' AND status IN ('sent','sending') "
                "AND started_at>=? ORDER BY started_at", (account["id"], iso(now - timedelta(hours=1))))]
        if len(last_hour) >= policy.hourly_cap:
            candidates.append(last_hour[-policy.hourly_cap] + timedelta(hours=1))
        if recipient.get("status") != "sending":
            pause_until = self.store.setting(f"next_send:{account['id']}")
            if last_send is not None:
                candidates.append(last_send)
            elif pause_until:
                candidates.append(parse(pause_until))
        due = max(candidates)
        # Test campaigns go to the owner's own accounts: no recipient window (tz is empty).
        return policy.next_window_start(due, recipient["tz"]) if recipient["tz"] else due

    # ------------------------------------------------------------------ send
    async def _ensure_connected(self, account_id: int) -> None:
        if account_id in self._connected:
            return
        credentials = self.credentials(account_id)
        try:
            user_id = await self.gateway.connect(account_id, credentials.api_id, credentials.api_hash,
                                                 credentials.session)
        except GatewayError as error:
            await self._handle_account_error(account_id, error)
            raise _AccountStopped() from error
        self._connected.add(account_id)
        with self.store.tx() as conn:
            conn.execute("UPDATE accounts SET tg_user_id=?,updated_at=? WHERE id=?",
                         (str(user_id), iso(self.clock()), account_id))
        session = self.gateway.export_session(account_id)
        if session:
            self.save_session(account_id, session)

    async def _peer(self, campaign: dict, account: dict, recipient: dict) -> Peer:
        username = recipient["username"]
        cached = self.store.row("SELECT * FROM peers WHERE account_id=? AND username=?",
                                (account["id"], username))
        if cached and cached["kind"] == "user":
            return Peer(int(cached["telegram_user_id"]), int(cached["access_hash"]))
        if username.startswith("+") and campaign["kind"] == "test":
            peer = await self.gateway.resolve_contact(account["id"], username)
        else:
            peer = await self.gateway.resolve_username(account["id"], username)
        with self.store.tx() as conn:
            conn.execute("INSERT OR REPLACE INTO peers VALUES (?,?,?,?,?,?,?)",
                         (account["id"], username, str(peer.user_id), str(peer.access_hash), "user", "",
                          iso(self.clock())))
            conn.execute("UPDATE recipients SET telegram_user_id=? WHERE id=?", (str(peer.user_id), recipient["id"]))
        return peer

    async def send_one(self, campaign: dict, account: dict, recipient: dict) -> None:
        account_id = account["id"]
        try:
            peer = await self._peer(campaign, account, recipient)
        except GatewayError as error:
            await self._handle_recipient_error(campaign, account_id, recipient, None, error)
            return
        if campaign["kind"] == "normal" and str(peer.user_id) in self.store.stop_set()["telegram_user"]:
            self._close_recipient(recipient["id"], "skipped", "stoplist", "Аккаунт в стоп-листе")
            return
        attempt = self.store.row("SELECT * FROM attempts WHERE recipient_id=? AND status='sending' "
                                 "ORDER BY id DESC LIMIT 1", (recipient["id"],))
        if attempt:
            # Outcome of an earlier send is unknown (crash, cancel, lost answer). Check first.
            since = parse(attempt["started_at"]) - timedelta(seconds=30)
            try:
                found = await self.gateway.find_outgoing(account_id, peer, recipient["message"], since)
            except GatewayError as error:
                await self._handle_recipient_error(campaign, account_id, recipient, attempt["id"], error)
                return
            if found:
                self._finish_sent(recipient, attempt["id"], found, "Найдено в истории диалога после перезапуска")
                self.store.event("reconciled", "Сообщение уже было доставлено, повтор не отправлен",
                                 account_id=account_id, campaign_id=campaign["id"], recipient_id=recipient["id"])
                return
            random_id = attempt["random_id"]
            attempt_id = attempt["id"]
        else:
            random_id = self.rng.getrandbits(62) + 1
            with self.store.tx() as conn:
                state = conn.execute("SELECT status FROM recipients WHERE id=?", (recipient["id"],)).fetchone()
                if not state or state["status"] != "queued":
                    return
                attempt_id = conn.execute(
                    "INSERT INTO attempts (recipient_id,account_id,random_id,mode,status,started_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (recipient["id"], account_id, random_id, "live", "sending", iso(self.clock()))).lastrowid
                conn.execute("UPDATE recipients SET status='sending' WHERE id=?", (recipient["id"],))
                conn.execute("UPDATE accounts SET first_send_day=COALESCE(first_send_day,?) WHERE id=?",
                             (local_day(self.clock()).isoformat(), account_id))
        try:
            sent = await self.gateway.send_message(account_id, peer, recipient["message"], random_id)
        except GatewayError as error:
            if error.kind == gw.DUPLICATE:
                self._finish_sent(recipient, attempt_id, None, "RANDOM_ID_DUPLICATE: уже доставлено")
                return
            await self._handle_recipient_error(campaign, account_id, recipient, attempt_id, error)
            return
        self._finish_sent(recipient, attempt_id, sent.msg_id, sent.response or "ok")
        pause = self.policy().pause_seconds(self.rng)
        self.store.set_setting(f"next_send:{account_id}", iso(self.clock() + timedelta(seconds=pause)))

    def _finish_sent(self, recipient: dict, attempt_id: int, msg_id: int | None, response: str) -> None:
        now = iso(self.clock())
        with self.store.tx() as conn:
            conn.execute("UPDATE attempts SET status='sent',msg_id=?,server_response=?,finished_at=? WHERE id=?",
                         (msg_id, scrub(response)[:300], now, attempt_id))
            conn.execute("UPDATE recipients SET status='sent',msg_id=?,sent_at=?,reason_kind='',reason='' WHERE id=?",
                         (msg_id, now, recipient["id"]))

    def _close_recipient(self, recipient_id: int, status: str, kind: str, reason: str) -> None:
        with self.store.tx() as conn:
            conn.execute("UPDATE recipients SET status=?,reason_kind=?,reason=? WHERE id=? AND status IN ('queued','sending')",
                         (status, kind, reason, recipient_id))

    def _fail_attempt(self, attempt_id: int | None, error: GatewayError, conn) -> None:
        if attempt_id:
            conn.execute("UPDATE attempts SET status='failed',error_kind=?,error_detail=?,wait_seconds=?,finished_at=? "
                         "WHERE id=?", (error.kind, scrub(error.detail)[:300], error.seconds, iso(self.clock()), attempt_id))

    async def _handle_recipient_error(self, campaign, account_id, recipient, attempt_id, error: GatewayError):
        label = gw.KIND_LABELS.get(error.kind, error.kind)
        if error.kind == gw.NETWORK:
            raise _NetworkTrouble()  # attempt stays 'sending' and is reconciled next time
        if error.kind == gw.FLOOD_WAIT:
            with self.store.tx() as conn:
                self._fail_attempt(attempt_id, error, conn)
                conn.execute("UPDATE recipients SET status='queued' WHERE id=?", (recipient["id"],))
            self._flood_wait(account_id, int(error.seconds or 60), campaign["id"])
            return
        if error.kind in gw.QUARANTINE_KINDS:
            with self.store.tx() as conn:
                self._fail_attempt(attempt_id, error, conn)
                conn.execute("UPDATE recipients SET status='queued' WHERE id=?", (recipient["id"],))
            await self._handle_account_error(account_id, error)
            raise _AccountStopped()
        with self.store.tx() as conn:
            self._fail_attempt(attempt_id, error, conn)
            if error.kind in gw.UNREACHABLE_KINDS:
                conn.execute("UPDATE recipients SET status='unreachable',reason_kind=?,reason=? WHERE id=?",
                             (error.kind, label, recipient["id"]))
                if campaign["kind"] == "normal" and recipient["username"]:
                    conn.execute("INSERT OR IGNORE INTO unreachable VALUES (?,?,?)",
                                 (f"username:{recipient['username']}", error.kind, iso(self.clock())))
            else:
                conn.execute("UPDATE recipients SET status='failed',reason_kind=?,reason=? WHERE id=?",
                             (error.kind, f"{label}: {scrub(error.detail)[:120]}", recipient["id"]))
            self.store.event(error.kind, f"{recipient['business_name']}: {label}", level="warning",
                             account_id=account_id, campaign_id=campaign["id"], recipient_id=recipient["id"], conn=conn)

    def _flood_wait(self, account_id: int, seconds: int, campaign_id: int | None) -> None:
        policy = self.policy()
        now = self.clock()
        if seconds <= policy.flood_wait_max:
            until = now + timedelta(seconds=seconds + self.rng.randint(5, 30))
            text = f"Telegram попросил подождать {seconds} с — ждём"
        else:
            until = max(policy.end_of_day(now), now + timedelta(seconds=seconds))
            text = f"Telegram попросил подождать {seconds} с — аккаунт на паузе до {iso(until)}"
        with self.store.tx() as conn:
            conn.execute("UPDATE accounts SET status='paused',status_reason=?,paused_until=?,updated_at=? WHERE id=?",
                         (text, iso(until), iso(now), account_id))
            self.store.event("flood_wait", text, level="warning", account_id=account_id,
                             campaign_id=campaign_id, conn=conn)

    async def _handle_account_error(self, account_id: int, error: GatewayError) -> None:
        label = gw.KIND_LABELS.get(error.kind, error.kind)
        if error.kind == gw.FLOOD_WAIT:
            self._flood_wait(account_id, int(error.seconds or 60), None)
            return
        if error.kind in gw.QUARANTINE_KINDS:
            reason = f"Карантин: {label}. Рассылка с аккаунта остановлена"
            with self.store.tx() as conn:
                conn.execute("UPDATE accounts SET status='quarantine',status_reason=?,updated_at=? WHERE id=?",
                             (reason, iso(self.clock()), account_id))
                self.store.event("quarantine", reason, level="error", account_id=account_id, conn=conn)
            self._stop_for_account(account_id, reason)
            try:
                await self.gateway.disconnect(account_id)
            finally:
                self._connected.discard(account_id)
            return
        self.store.event(error.kind, f"Ошибка подключения: {label}", level="error", account_id=account_id)

    def _stop_for_account(self, account_id: int, reason: str) -> None:
        """Stop every running campaign that uses this account. Traffic is never moved elsewhere."""
        with self.store.tx() as conn:
            ids = [row[0] for row in conn.execute(
                "SELECT DISTINCT c.id FROM campaigns c JOIN recipients r ON r.campaign_id=c.id "
                "WHERE r.account_id=? AND c.status='running'", (account_id,))]
            for campaign_id in ids:
                self._set_campaign(campaign_id, "stopped", reason, conn=conn)
                self.store.event("campaign_stopped", reason, level="error", account_id=account_id,
                                 campaign_id=campaign_id, conn=conn)

    # -------------------------------------------------------------- incoming
    async def on_incoming(self, account_id: int, from_user_id: int, text: str) -> None:
        rows = self.store.rows("SELECT r.id,r.business_id,r.username,r.business_name,c.kind FROM recipients r "
                               "JOIN campaigns c ON c.id=r.campaign_id WHERE r.account_id=? AND "
                               "r.telegram_user_id=? AND r.status='sent'", (account_id, str(from_user_id)))
        if not rows:
            return
        now = iso(self.clock())
        with self.store.tx() as conn:
            for row in rows:
                conn.execute("UPDATE recipients SET replied_at=COALESCE(replied_at,?) WHERE id=?", (now, row["id"]))
        is_stop = bool(STOP_WORDS.search(text or ""))
        name = rows[0]["business_name"]
        if is_stop:
            self.store.stop_add("telegram_user", str(from_user_id), name, "reply")
            if rows[0]["kind"] == "normal":
                self.store.stop_add("business", rows[0]["business_id"], name, "reply")
                if rows[0]["username"]:
                    self.store.stop_add("username", rows[0]["username"], name, "reply")
        self.store.event("reply_stop" if is_stop else "reply",
                         f"{name}: {'просит не писать — в стоп-листе' if is_stop else 'ответил'}",
                         account_id=account_id, recipient_id=rows[0]["id"])


class _NetworkTrouble(Exception):
    pass


class _AccountStopped(Exception):
    pass
