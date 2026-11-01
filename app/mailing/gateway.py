"""One interface for everything that talks to Telegram."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

# Error kinds. The engine reacts to the kind, never to library classes.
FLOOD_WAIT = "flood_wait"            # wait N seconds (server said so)
PEER_FLOOD = "peer_flood"            # account limited (spam reports) -> quarantine
ACCOUNT_BANNED = "account_banned"    # deactivated / banned / phone banned -> quarantine
SESSION_INVALID = "session_invalid"  # auth key unregistered / session revoked -> quarantine
ACCOUNT_FROZEN = "account_frozen"    # FROZEN_METHOD_INVALID -> quarantine
ACCOUNT_LIMITED = "account_limited"  # USER_RESTRICTED and similar -> quarantine
PRIVACY = "privacy"                  # recipient privacy, premium-only or paid messages
BLOCKED = "blocked"                  # recipient blocked us, or we blocked them
NOT_FOUND = "not_found"              # username does not exist / invalid
NOT_USER = "not_user"                # username belongs to a channel, group or bot
RECIPIENT_GONE = "recipient_gone"    # recipient account deleted
NETWORK = "network"                  # no answer: the outcome of a send is unknown
DUPLICATE = "duplicate"              # RANDOM_ID_DUPLICATE: message was already sent
AUTH_CODE_INVALID = "auth_code_invalid"
AUTH_PASSWORD_INVALID = "auth_password_invalid"
AUTH_PHONE_INVALID = "auth_phone_invalid"
OTHER = "other"

QUARANTINE_KINDS = {PEER_FLOOD, ACCOUNT_BANNED, SESSION_INVALID, ACCOUNT_FROZEN, ACCOUNT_LIMITED}
UNREACHABLE_KINDS = {PRIVACY, BLOCKED, NOT_FOUND, NOT_USER, RECIPIENT_GONE}

KIND_LABELS = {
    FLOOD_WAIT: "Telegram попросил подождать",
    PEER_FLOOD: "Аккаунт ограничен Telegram (жалобы на спам)",
    ACCOUNT_BANNED: "Аккаунт заблокирован или удалён",
    SESSION_INVALID: "Сессия больше не действует",
    ACCOUNT_FROZEN: "Аккаунт заморожен Telegram",
    ACCOUNT_LIMITED: "Аккаунт ограничен Telegram",
    PRIVACY: "Настройки приватности получателя",
    BLOCKED: "Получатель заблокировал аккаунт",
    NOT_FOUND: "Username не существует",
    NOT_USER: "Ссылка ведёт на канал, группу или бота",
    RECIPIENT_GONE: "Аккаунт получателя удалён",
    NETWORK: "Нет связи с Telegram",
    DUPLICATE: "Сообщение уже было отправлено",
    OTHER: "Другая ошибка Telegram",
}


class GatewayError(Exception):
    def __init__(self, kind: str, detail: str = "", seconds: int | None = None):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail
        self.seconds = seconds


@dataclass(frozen=True)
class Peer:
    user_id: int
    access_hash: int
    kind: str = "user"  # user | bot | channel


@dataclass(frozen=True)
class SentMessage:
    msg_id: int
    date: datetime
    response: str = ""


@dataclass(frozen=True)
class LoginStep:
    status: str          # code_sent | password_needed | authorized
    token: str = ""      # opaque login handle kept in memory only
    user_id: int | None = None
    session: str = ""    # session string when authorized (caller encrypts it)


IncomingHandler = Callable[[int, int, str], Awaitable[None]]  # (account_id, from_user_id, text)


class TelegramGateway(ABC):
    """Everything the module needs from Telegram. Implementations must not log secrets."""

    @abstractmethod
    async def request_code(self, api_id: int, api_hash: str, phone: str) -> LoginStep: ...

    @abstractmethod
    async def submit_code(self, token: str, code: str) -> LoginStep: ...

    @abstractmethod
    async def submit_password(self, token: str, password: str) -> LoginStep: ...

    @abstractmethod
    async def cancel_login(self, token: str) -> None: ...

    @abstractmethod
    async def connect(self, account_id: int, api_id: int, api_hash: str, session: str) -> int:
        """Open an authorized client and return the Telegram user id of the account."""

    @abstractmethod
    async def disconnect(self, account_id: int) -> None: ...

    @abstractmethod
    def export_session(self, account_id: int) -> str | None:
        """Current session string after reconnects (to re-encrypt and save)."""

    @abstractmethod
    async def resolve_username(self, account_id: int, username: str) -> Peer: ...

    @abstractmethod
    async def resolve_contact(self, account_id: int, phone: str) -> Peer:
        """Find a peer among existing contacts only (no contact import)."""

    @abstractmethod
    async def send_message(self, account_id: int, peer: Peer, text: str, random_id: int) -> SentMessage: ...

    @abstractmethod
    async def find_outgoing(self, account_id: int, peer: Peer, text: str, since: datetime) -> int | None:
        """Message id of our message with this text sent after `since`, if any."""

    async def incoming_after(self, account_id: int, peer: Peer, after_msg_id: int) -> list[tuple[int, str]]:
        """Incoming messages from this peer with id > after_msg_id, oldest first: (msg_id, text)."""
        return []

    @abstractmethod
    def set_incoming_handler(self, handler: IncomingHandler | None) -> None: ...
