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
# Lead statuses in «Лиды» that leave a business out of repeats (already talking or refused).
REPEAT_SKIP_STATUSES = {"replied": "Ответили", "working": "В работе", "completed": "Договорились",
                        "unsuitable": "Отказ"}
CATCH_UP_DAYS = 60  # replies to messages older than this are not looked for after a restart
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
                 on_sent: Callable[[dict], None] | None = None,
                 on_reply: Callable[[dict, bool], None] | None = None,
                 lead_status: Callable[[str], str] | None = None,
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
        self.on_sent = on_sent or (lambda _recipient: None)
        self.on_reply = on_reply or (lambda _recipient, _stop: None)
        self.lead_status = lead_status or (lambda _business_id: "")
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
        asyncio.run_coroutine_threadsafe(self._sync_quietly(), self._loop)

    async def _sync_quietly(self) -> None:
        try:
            await self.sync_replies()
        except Exception:
            log.exception("Дочитка ответов при запуске")

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

    async def forget_account(self, account_id: int) -> None:
        """Logout: close the client and drop it from the connected set (next use reconnects)."""
        self._connected.discard(account_id)
        await self.gateway.disconnect(account_id)

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
        elif remaining and self._campaign_active(campaign_id):
            self._set_campaign(campaign_id, "paused", "Отправка прервана: аккаунт недоступен. Проверьте аккаунты и продолжите")
            self.store.event("paused", "Отправка прервана, кампания на паузе", level="warning", campaign_id=campaign_id)

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
            paused_until = parse(account["paused_until"]) if account else None
            if account and account["status"] == "paused" and paused_until and paused_until <= self.clock():
                with self.store.tx() as conn:
                    conn.execute("UPDATE accounts SET status='active',status_reason='',paused_until=NULL "
                                 "WHERE id=? AND status='paused'", (account_id,))
                self.store.event("resumed", "Пауза аккаунта закончилась", account_id=account_id)
                continue
            if not account or account["status"] in ("quarantine", "needs_login"):
                self._stop_for_account(account_id, account["status_reason"] if account else "Аккаунт удалён",
                                       status="paused" if account and account["status"] == "needs_login" else "stopped")
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
                    # Everything is read again after the lock: another campaign may have written to this
                    # organization or used the pause, a reply or a stop-list entry may have come (E-M-08/09/11).
                    recipient = self.store.row("SELECT * FROM recipients WHERE id=?", (recipient["id"],))
                    account = self.store.row("SELECT * FROM accounts WHERE id=?", (account_id,))
                    campaign = self.store.row("SELECT * FROM campaigns WHERE id=?", (campaign_id,))
                    if not recipient or not account or recipient["status"] not in ("queued", "sending"):
                        continue
                    if recipient["status"] == "queued":
                        verdict = self.precheck(campaign, account, recipient)
                        if verdict:
                            self._close_recipient(recipient["id"], *verdict)
                            continue
                    if self.earliest_send(account, recipient) > self.clock():
                        continue
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
            except _Retry:
                continue

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
            verdict = self.send_guard(campaign, recipient, self.store.value)
            if verdict:
                return verdict
            if campaign.get("parent_id"):
                verdict = self._repeat_still_wanted(campaign, recipient)
                if verdict:
                    return verdict
        if account["is_test"] and username not in self.test_recipients():
            if not self.store.value("SELECT 1 FROM events WHERE kind='test_guard' AND campaign_id=?", (campaign["id"],)):
                self.store.event("test_guard", "Тестовый аккаунт может писать только тестовым адресатам",
                                 level="warning", account_id=account["id"], campaign_id=campaign["id"])
            return "skipped", "test_guard", "Тестовый аккаунт пишет только тестовым адресатам"
        if not username:
            return "skipped", "no_contact", "Нет username Telegram"
        return None

    def send_guard(self, campaign: dict, recipient: dict, value) -> tuple[str, str, str] | None:
        """Rules that must hold at the very moment of sending; `value(sql, args)` reads either the store
        or the open transaction that marks the message «sending».

        - An ordinary campaign: one first message per organization, counting messages being sent right
          now by another campaign (E-M-08).
        - A repeat of a batch: only to those who really got the message it repeats (E-M-13), and each
          stage (reminder, repeat) at most once per organization in the batch (E-M-10)."""
        if campaign["kind"] != "normal":
            return None
        username = (recipient["username"] or "").casefold()
        if not campaign.get("parent_id"):
            if value("SELECT 1 FROM recipients r JOIN campaigns c ON c.id=r.campaign_id "
                     "WHERE c.kind='normal' AND r.status IN ('sent','sending') AND r.id<>? AND "
                     "(r.business_id=? OR (r.username IS NOT NULL AND r.username=?)) LIMIT 1",
                     (recipient["id"], recipient["business_id"], username)):
                return "skipped", "already_sent", "Этой организации уже писали"
            return None
        got = ("sent",) if campaign["mode"] == "live" else ("sent", "dry_sent")
        if not value(f"SELECT 1 FROM recipients WHERE campaign_id=? AND business_id=? AND status IN "
                     f"({','.join('?' * len(got))}) LIMIT 1", (campaign["parent_id"], recipient["business_id"], *got)):
            return "skipped", "no_first", "Предыдущее сообщение пачки не получал"
        batch_id = campaign.get("batch_id") or campaign["parent_id"]
        if value("SELECT 1 FROM recipients r JOIN campaigns c ON c.id=r.campaign_id "
                 "WHERE COALESCE(c.batch_id,c.id)=? AND c.stage=? AND r.status IN ('sent','sending') "
                 "AND r.id<>? AND r.business_id=? LIMIT 1",
                 (batch_id, campaign.get("stage") or "first", recipient["id"], recipient["business_id"])):
            return "skipped", "already_sent", ("Напоминание этой организации уже отправлено"
                                               if campaign.get("stage") == "reminder"
                                               else "Этот этап пачки организация уже получила")
        return None

    def _repeat_still_wanted(self, campaign: dict, recipient: dict) -> tuple[str, str, str] | None:
        """Read before every repeat message, not only when the repeat was created (E-M-11)."""
        batch_id = campaign.get("batch_id") or campaign["parent_id"]
        if self.store.value("SELECT 1 FROM batch_exclusions WHERE batch_id=? AND business_id=?",
                            (batch_id, recipient["business_id"])):
            return "skipped", "excluded", "Убран из пачки"
        if self.store.value("SELECT 1 FROM recipients r JOIN campaigns c ON c.id=r.campaign_id "
                            "WHERE COALESCE(c.batch_id,c.id)=? AND r.business_id=? AND r.replied_at IS NOT NULL "
                            "LIMIT 1", (batch_id, recipient["business_id"])):
            return "skipped", "replied", "Ответил в Telegram"
        status = ""
        try:
            status = self.lead_status(recipient["business_id"]) or ""
        except Exception:
            log.exception("Статус лида для повтора")
        if status in REPEAT_SKIP_STATUSES:
            return "skipped", "lead_status", f"В «Лидах»: {REPEAT_SKIP_STATUSES[status]}"
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
        # Test recipients are the owner's accounts: Moscow window, like everyone (E-M-04). «Без ожидания»
        # (test campaigns only) skips the window and the pause between messages, never the limits.
        no_wait = recipient.get("no_wait")
        if no_wait is None and recipient.get("campaign_id"):
            no_wait = self.store.value("SELECT no_wait FROM campaigns WHERE id=? AND kind='test'",
                                       (recipient["campaign_id"],))
        if recipient.get("status") != "sending" and not no_wait:
            pause_until = self.store.setting(f"next_send:{account['id']}")
            if last_send is not None:
                candidates.append(last_send)
            elif pause_until:
                candidates.append(parse(pause_until))
        due = max(candidates)
        if no_wait:
            return due
        return policy.next_window_start(due, recipient["tz"] or DEFAULT_TZ)

    # ------------------------------------------------------------------ send
    async def _ensure_connected(self, account_id: int) -> None:
        if account_id in self._connected:
            return
        try:
            credentials = self.credentials(account_id)
        except (GatewayError, ValueError) as error:
            # No session (logged out) or no keys: the account needs a login, it is not restricted.
            reason = "Нужен вход в аккаунт" if isinstance(error, GatewayError) else str(error)
            with self.store.tx() as conn:
                conn.execute("UPDATE accounts SET status='needs_login',status_reason=?,updated_at=? WHERE id=?",
                             (reason, iso(self.clock()), account_id))
                self.store.event("needs_login", reason, level="warning", account_id=account_id, conn=conn)
            self._stop_for_account(account_id, reason, status="paused")
            raise _AccountStopped() from None
        try:
            user_id = await self.gateway.connect(account_id, credentials.api_id, credentials.api_hash,
                                                 credentials.session)
        except GatewayError as error:
            if error.kind in gw.QUARANTINE_KINDS:
                await self._handle_account_error(account_id, error)
                raise _AccountStopped() from error
            if error.kind == gw.FLOOD_WAIT:
                self._flood_wait(account_id, int(error.seconds or 60), None)
                raise _Retry() from error
            raise _NetworkTrouble() from error
        self._connected.add(account_id)
        with self.store.tx() as conn:
            conn.execute("UPDATE accounts SET tg_user_id=?,updated_at=? WHERE id=?",
                         (str(user_id), iso(self.clock()), account_id))
        session = self.gateway.export_session(account_id)
        if session:
            self.save_session(account_id, session)
        await self.catch_up(account_id)

    async def catch_up(self, account_id: int) -> int:
        """Replies and «СТОП» that came while the app was closed (E-M-14): read the dialogs with everyone
        this account wrote to and pass new incoming messages through the usual handler. Returns how many
        dialogs had something new. Errors never stop sending: the next connection tries again."""
        since = iso(self.clock() - timedelta(days=CATCH_UP_DAYS))
        rows = self.store.rows(
            "SELECT r.telegram_user_id AS uid, MIN(r.msg_id) AS first_id, p.access_hash "
            "FROM recipients r JOIN peers p ON p.account_id=r.account_id AND p.telegram_user_id=r.telegram_user_id "
            "WHERE r.account_id=? AND r.status='sent' AND r.msg_id IS NOT NULL AND r.sent_at>=? "
            "GROUP BY r.telegram_user_id", (account_id, since))
        found = 0
        for row in rows:
            key = f"seen_in:{account_id}:{row['uid']}"
            after = max(int(row["first_id"] or 0), int(self.store.setting(key, 0) or 0))
            try:
                messages = await self.gateway.incoming_after(
                    account_id, Peer(int(row["uid"]), int(row["access_hash"] or 0)), after)
            except GatewayError as error:
                log.warning("Входящие не дочитаны: %s", error.kind)
                return found
            if not messages:
                continue
            found += 1
            self.store.set_setting(key, max(msg_id for msg_id, _text in messages))
            await self.on_incoming(account_id, int(row["uid"]), "\n".join(text for _id, text in messages))
        if found:
            self.store.event("catch_up", f"Дочитаны ответы, пришедшие без приложения: {found}",
                             account_id=account_id)
        return found

    async def sync_replies(self) -> int:
        """At start: connect every ready account that has written to someone, which also reads missed replies.
        Connected clients then keep receiving replies while the app is open, even with no campaign."""
        if self.store.setting("emergency_stop", False):
            return 0
        since = iso(self.clock() - timedelta(days=CATCH_UP_DAYS))
        ids = [row["id"] for row in self.store.rows(
            "SELECT DISTINCT a.id FROM accounts a JOIN recipients r ON r.account_id=a.id "
            "WHERE a.status IN ('active','paused') AND r.status='sent' AND r.sent_at>=?", (since,))]
        for account_id in ids:
            if account_id in self._connected:
                await self.catch_up(account_id)
                continue
            try:
                await self._ensure_connected(account_id)
            except (_NetworkTrouble, _AccountStopped, _Retry):
                continue
        return len(ids)

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
            # Not delivered: the stop-list and the other checks apply again before a new try (E-M-07).
            verdict = self.precheck(campaign, account, recipient)
            if verdict:
                with self.store.tx() as conn:
                    conn.execute("UPDATE attempts SET status='failed',error_kind=?,error_detail=?,finished_at=? "
                                 "WHERE id=?", (verdict[1], verdict[2], iso(self.clock()), attempt["id"]))
                self._close_recipient(recipient["id"], *verdict)
                return
            random_id = attempt["random_id"]
            attempt_id = attempt["id"]
        else:
            random_id = self.rng.getrandbits(62) + 1
            with self.store.tx() as conn:
                state = conn.execute("SELECT status FROM recipients WHERE id=?", (recipient["id"],)).fetchone()
                if not state or state["status"] != "queued":
                    return
                # Same transaction that claims the message: two campaigns can never both pass (E-M-08).
                verdict = self.send_guard(campaign, recipient,
                                          lambda sql, args: (conn.execute(sql, args).fetchone() or [None])[0])
                if verdict:
                    conn.execute("UPDATE recipients SET status=?,reason_kind=?,reason=? WHERE id=?",
                                 (*verdict, recipient["id"]))
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
        try:
            self.on_sent(dict(recipient))   # «Лиды»: status «Написал», reminder in «Сегодня»
        except Exception:
            log.exception("Отметка отправки в «Лидах»")

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
            self._connected.discard(account_id)
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

    def _stop_for_account(self, account_id: int, reason: str, status: str = "stopped") -> None:
        """Stop every running campaign that uses this account. Traffic is never moved elsewhere."""
        with self.store.tx() as conn:
            ids = [row[0] for row in conn.execute(
                "SELECT DISTINCT c.id FROM campaigns c JOIN recipients r ON r.campaign_id=c.id "
                "WHERE r.account_id=? AND c.status='running'", (account_id,))]
            for campaign_id in ids:
                self._set_campaign(campaign_id, status, reason, conn=conn)
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
        for row in rows:
            if row["kind"] == "normal":
                try:
                    self.on_reply(dict(row), is_stop)
                except Exception:
                    log.exception("Отметка ответа в «Лидах»")
        self.store.event("reply_stop" if is_stop else "reply",
                         f"{name}: {'просит не писать — в стоп-листе' if is_stop else 'ответил'}",
                         account_id=account_id, recipient_id=rows[0]["id"])


class _NetworkTrouble(Exception):
    pass


class _AccountStopped(Exception):
    pass


class _Retry(Exception):
    pass
