"""In-memory Telegram for tests. Simulates delivery, random_id dedupe and server errors."""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .entities import parse as parse_entities
from .gateway import (AUTH_CODE_INVALID, NOT_FOUND, NOT_USER, GatewayError, IncomingHandler,
                      LoginStep, Peer, SentMessage, TelegramGateway)


@dataclass
class Delivered:
    account_id: int
    user_id: int
    text: str
    random_id: int
    msg_id: int
    date: datetime
    raw: str = ""                 # text as the engine passed it (with [слова](url) markup)
    entities: tuple = ()          # what Telegram would get: clickable links


@dataclass
class FakeGateway(TelegramGateway):
    users: dict[str, Peer] = field(default_factory=dict)        # username (casefold) -> peer
    contacts: dict[str, Peer] = field(default_factory=dict)     # phone digits -> peer
    phones: dict[str, Peer] = field(default_factory=dict)       # numbers whose account resolves (resolvePhone)
    delivered: list[Delivered] = field(default_factory=list)
    # Errors to raise: key is (operation, target) where target is username / user id / "*".
    errors: dict[tuple[str, str], list[GatewayError]] = field(default_factory=dict)
    send_delay: float = 0.0
    lose_reply_once: bool = False   # deliver, then pretend the answer was lost (NETWORK)
    crash_after_delivery: int | None = None  # after the Nth delivery: act like the app died
    history_broken: bool = False              # find_outgoing finds nothing (forces random_id path)
    clock: object = None                      # callable returning aware datetime (engine clock)
    login_code: str = "12345"
    login_password: str = ""
    calls: list[tuple] = field(default_factory=list)
    inbox: list[tuple[int, int, int, str]] = field(default_factory=list)  # (account, from user, msg id, text)

    def __post_init__(self):
        self._ids = itertools.count(1000)
        self._connected: dict[int, int] = {}
        self._logins: dict[str, dict] = {}
        self._handler: IncomingHandler | None = None
        self._next_user = itertools.count(5_000_000)

    def _now(self) -> datetime:
        return self.clock() if self.clock else datetime.now(timezone.utc)

    # helpers for tests -------------------------------------------------
    def add_user(self, username: str, kind: str = "user") -> Peer:
        peer = Peer(next(self._next_user), 77, kind)
        self.users[username.casefold()] = peer
        return peer

    def fail(self, operation: str, target: str, error: GatewayError, times: int = 1) -> None:
        self.errors.setdefault((operation, str(target)), []).extend([error] * times)

    def _maybe_fail(self, operation: str, *targets: str) -> None:
        for target in (*targets, "*"):
            queue = self.errors.get((operation, str(target)))
            if queue:
                raise queue.pop(0)

    async def reply(self, account_id: int, from_user_id: int, text: str) -> None:
        self.inbox.append((account_id, from_user_id, next(self._ids), text))
        if self._handler:
            await self._handler(account_id, from_user_id, text)

    def reply_while_closed(self, account_id: int, from_user_id: int, text: str) -> None:
        """A reply that arrives while nobody listens: only reading the dialog finds it."""
        self.inbox.append((account_id, from_user_id, next(self._ids), text))

    async def incoming_after(self, account_id, peer, after_msg_id):
        self.calls.append(("incoming", account_id, peer.user_id))
        return [(msg_id, text) for account, user, msg_id, text in self.inbox
                if account == account_id and user == peer.user_id and msg_id > after_msg_id]

    def sent_to(self, user_id: int) -> list[Delivered]:
        return [item for item in self.delivered if item.user_id == user_id]

    # login --------------------------------------------------------------
    async def request_code(self, api_id, api_hash, phone):
        self.calls.append(("request_code",))
        token = f"login-{len(self._logins) + 1}"
        self._logins[token] = {"phone": phone}
        return LoginStep("code_sent", token)

    async def submit_code(self, token, code):
        if code != self.login_code:
            raise GatewayError(AUTH_CODE_INVALID, "wrong code")
        if self.login_password:
            return LoginStep("password_needed", token)
        return LoginStep("authorized", token, user_id=4242, session="fake-session")

    async def submit_password(self, token, password):
        if password != self.login_password:
            raise GatewayError("auth_password_invalid", "wrong password")
        return LoginStep("authorized", token, user_id=4242, session="fake-session")

    async def cancel_login(self, token):
        self._logins.pop(token, None)

    # session ------------------------------------------------------------
    async def connect(self, account_id, api_id, api_hash, session):
        self._maybe_fail("connect", str(account_id))
        self._connected[account_id] = 4242 + account_id
        return self._connected[account_id]

    async def disconnect(self, account_id):
        self._connected.pop(account_id, None)

    def export_session(self, account_id):
        return "fake-session" if account_id in self._connected else None

    # messaging -----------------------------------------------------------
    async def resolve_username(self, account_id, username):
        self.calls.append(("resolve", account_id, username))
        self._maybe_fail("resolve", username.casefold())
        peer = self.users.get(username.casefold())
        if not peer:
            raise GatewayError(NOT_FOUND, "USERNAME_NOT_OCCUPIED")
        if peer.kind != "user":
            raise GatewayError(NOT_USER, peer.kind)
        return peer

    async def resolve_contact(self, account_id, phone):
        peer = self.contacts.get("".join(ch for ch in phone if ch.isdigit()))
        if not peer:
            raise GatewayError(NOT_FOUND, "not in contacts")
        return peer

    async def resolve_phone(self, account_id, phone):
        """Numbers that show their account to everyone are in `phones`; others are «not found»."""
        self.calls.append(("resolve_phone", account_id, phone))
        digits = "".join(ch for ch in phone if ch.isdigit())
        self._maybe_fail("resolve_phone", digits)
        peer = self.phones.get(digits)
        if not peer:
            raise GatewayError(NOT_FOUND, "PHONE_NOT_OCCUPIED")
        return peer

    def add_phone(self, digits: str) -> Peer:
        peer = Peer(next(self._next_user), 77, "user")
        self.phones["".join(ch for ch in digits if ch.isdigit())] = peer
        return peer

    async def send_message(self, account_id, peer, text, random_id):
        self.calls.append(("send", account_id, peer.user_id, random_id))
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        self._maybe_fail("send", str(peer.user_id))
        existing = next((item for item in self.delivered
                         if item.account_id == account_id and item.random_id == random_id), None)
        if existing:
            raise GatewayError("duplicate", "RANDOM_ID_DUPLICATE")
        plain, entities = parse_entities(text)
        item = Delivered(account_id, peer.user_id, plain, random_id, next(self._ids), self._now(), text, tuple(entities))
        self.delivered.append(item)
        if self.crash_after_delivery and len(self.delivered) == self.crash_after_delivery:
            self.crash_after_delivery = None
            raise asyncio.CancelledError()
        if self.lose_reply_once:
            self.lose_reply_once = False
            raise GatewayError("network", "answer lost")
        return SentMessage(item.msg_id, item.date, f"Message(id={item.msg_id})")

    async def find_outgoing(self, account_id, peer, text, since):
        self.calls.append(("history", account_id, peer.user_id))
        if self.history_broken:
            return None
        for item in reversed(self.delivered):
            if item.account_id == account_id and item.user_id == peer.user_id \
                    and item.raw == text and item.date >= since:
                return item.msg_id
        return None

    def set_incoming_handler(self, handler):
        self._handler = handler
