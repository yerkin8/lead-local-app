"""Real TelegramGateway on Telethon 1.45 (user accounts, MTProto)."""

from __future__ import annotations

import asyncio
import re
import secrets
from datetime import datetime, timezone

from . import gateway as gw
from .gateway import GatewayError, IncomingHandler, LoginStep, Peer, SentMessage, TelegramGateway
from .logsafe import get_logger

log = get_logger()

# RPC error names (from telethon/errors/rpcerrorlist.py) -> module kinds.
_BY_NAME = {
    "PEER_FLOOD": gw.PEER_FLOOD,
    "USER_DEACTIVATED": gw.ACCOUNT_BANNED, "USER_DEACTIVATED_BAN": gw.ACCOUNT_BANNED,
    "PHONE_NUMBER_BANNED": gw.ACCOUNT_BANNED,
    "AUTH_KEY_UNREGISTERED": gw.SESSION_INVALID, "SESSION_REVOKED": gw.SESSION_INVALID,
    "SESSION_EXPIRED": gw.SESSION_INVALID, "AUTH_KEY_DUPLICATED": gw.SESSION_INVALID,
    "FROZEN_METHOD_INVALID": gw.ACCOUNT_FROZEN, "FROZEN_PARTICIPANT_MISSING": gw.ACCOUNT_FROZEN,
    "USER_RESTRICTED": gw.ACCOUNT_LIMITED,
    "USER_PRIVACY_RESTRICTED": gw.PRIVACY, "PRIVACY_PREMIUM_REQUIRED": gw.PRIVACY,
    "ALLOW_PAYMENT_REQUIRED": gw.PRIVACY, "CHAT_WRITE_FORBIDDEN": gw.PRIVACY,
    "USER_IS_BLOCKED": gw.BLOCKED, "YOU_BLOCKED_USER": gw.BLOCKED,
    "USERNAME_NOT_OCCUPIED": gw.NOT_FOUND, "USERNAME_INVALID": gw.NOT_FOUND,
    "PEER_ID_INVALID": gw.NOT_FOUND,
    "INPUT_USER_DEACTIVATED": gw.RECIPIENT_GONE,
    "RANDOM_ID_DUPLICATE": gw.DUPLICATE,
    "PHONE_CODE_INVALID": gw.AUTH_CODE_INVALID, "PHONE_CODE_EXPIRED": gw.AUTH_CODE_INVALID,
    "PHONE_CODE_EMPTY": gw.AUTH_CODE_INVALID,
    "PASSWORD_HASH_INVALID": gw.AUTH_PASSWORD_INVALID,
    "PHONE_NUMBER_INVALID": gw.AUTH_PHONE_INVALID, "PHONE_NUMBER_UNOCCUPIED": gw.AUTH_PHONE_INVALID,
}


def classify(error: BaseException) -> GatewayError:
    from telethon import errors

    if isinstance(error, GatewayError):
        return error
    if isinstance(error, (errors.FloodWaitError, errors.FloodPremiumWaitError, errors.SlowModeWaitError)):
        return GatewayError(gw.FLOOD_WAIT, "FLOOD_WAIT", int(getattr(error, "seconds", 60) or 60))
    if isinstance(error, errors.RPCError):
        # Generated subclasses (PeerFloodError...) keep the generic message ("BAD_REQUEST");
        # the specific name is the class name. Unknown errors arrive as base classes with message.
        if type(error).__module__.endswith("rpcerrorlist"):
            name = re.sub(r"(?<!^)(?=[A-Z])", "_", type(error).__name__[:-5]).upper()
        else:
            name = str(getattr(error, "message", "") or "").upper()
        base = name.split("_X")[0]
        for key, kind in _BY_NAME.items():
            if name == key or base == key or name.startswith(key + "_"):
                return GatewayError(kind, key)
        if getattr(error, "code", None) == 420:
            return GatewayError(gw.FLOOD_WAIT, name, int(getattr(error, "seconds", 60) or 60))
        return GatewayError(gw.OTHER, name or type(error).__name__)
    if isinstance(error, (ConnectionError, OSError, asyncio.TimeoutError, TimeoutError)):
        return GatewayError(gw.NETWORK, type(error).__name__)
    return GatewayError(gw.OTHER, type(error).__name__)


def _message_id(result, random_id: int) -> tuple[int, datetime]:
    """Message id from SendMessage result: UpdateShortSentMessage or Updates with UpdateMessageID."""
    from telethon.tl.types import UpdateMessageID, UpdateNewMessage, UpdateShortSentMessage

    if isinstance(result, UpdateShortSentMessage):
        return int(result.id), result.date
    msg_id, date = 0, datetime.now(timezone.utc)
    for update in getattr(result, "updates", []) or []:
        if isinstance(update, UpdateMessageID) and update.random_id == random_id:
            msg_id = int(update.id)
        elif isinstance(update, UpdateNewMessage) and getattr(update.message, "out", False):
            msg_id = msg_id or int(update.message.id)
            date = update.message.date or date
    return msg_id, date


class TelethonGateway(TelegramGateway):
    def __init__(self):
        self._clients: dict[int, object] = {}
        self._logins: dict[str, dict] = {}
        self._handler: IncomingHandler | None = None

    def _client(self, session: str, api_id: int, api_hash: str):
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        # flood_sleep_threshold=0: every FloodWait reaches the engine, nothing sleeps silently.
        # Device and app parameters are Telethon defaults; nothing is disguised.
        return TelegramClient(StringSession(session or None), int(api_id), api_hash,
                              flood_sleep_threshold=0, request_retries=1, connection_retries=2,
                              retry_delay=3, timeout=20, receive_updates=True)

    async def _guard(self, coroutine):
        try:
            return await coroutine
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - mapped to module kinds
            raise classify(error) from None

    # login ---------------------------------------------------------------
    async def request_code(self, api_id, api_hash, phone):
        client = self._client("", api_id, api_hash)
        await self._guard(client.connect())
        try:
            sent = await self._guard(client.send_code_request(phone))
        except GatewayError:
            await client.disconnect()
            raise
        token = secrets.token_urlsafe(16)
        self._logins[token] = {"client": client, "phone": phone, "hash": sent.phone_code_hash}
        return LoginStep("code_sent", token)

    async def _authorized(self, token: str, client) -> LoginStep:
        me = await self._guard(client.get_me())
        session = client.session.save()
        await client.disconnect()
        self._logins.pop(token, None)
        return LoginStep("authorized", token, user_id=int(me.id), session=session)

    async def submit_code(self, token, code):
        from telethon.errors import SessionPasswordNeededError

        login = self._logins.get(token)
        if not login:
            raise GatewayError(gw.AUTH_CODE_INVALID, "login expired")
        client = login["client"]
        try:
            await client.sign_in(login["phone"], str(code).strip(), phone_code_hash=login["hash"])
        except SessionPasswordNeededError:
            return LoginStep("password_needed", token)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            raise classify(error) from None
        return await self._authorized(token, client)

    async def submit_password(self, token, password):
        login = self._logins.get(token)
        if not login:
            raise GatewayError(gw.AUTH_PASSWORD_INVALID, "login expired")
        await self._guard(login["client"].sign_in(password=password))
        return await self._authorized(token, login["client"])

    async def cancel_login(self, token):
        login = self._logins.pop(token, None)
        if login:
            await login["client"].disconnect()

    # session -------------------------------------------------------------
    async def connect(self, account_id, api_id, api_hash, session):
        from telethon import events

        if account_id in self._clients:
            me = await self._guard(self._clients[account_id].get_me())
            return int(me.id)
        client = self._client(session, api_id, api_hash)
        await self._guard(client.connect())
        if not await self._guard(client.is_user_authorized()):
            await client.disconnect()
            raise GatewayError(gw.SESSION_INVALID, "not authorized")
        me = await self._guard(client.get_me())

        async def incoming(event):
            if self._handler and event.is_private and event.sender_id:
                try:
                    await self._handler(account_id, int(event.sender_id), event.raw_text or "")
                except Exception:
                    log.exception("Обработчик входящих сообщений завершился с ошибкой")

        client.add_event_handler(incoming, events.NewMessage(incoming=True))
        self._clients[account_id] = client
        return int(me.id)

    async def disconnect(self, account_id):
        client = self._clients.pop(account_id, None)
        if client:
            await client.disconnect()

    def export_session(self, account_id):
        client = self._clients.get(account_id)
        return client.session.save() if client else None

    def _require(self, account_id):
        client = self._clients.get(account_id)
        if not client:  # not a Telegram verdict about the account: reconnect, never quarantine
            raise GatewayError(gw.NETWORK, "not connected")
        return client

    # messaging -----------------------------------------------------------
    async def resolve_username(self, account_id, username):
        from telethon.tl.functions.contacts import ResolveUsernameRequest
        from telethon.tl.types import User

        client = self._require(account_id)
        result = await self._guard(client(ResolveUsernameRequest(username.lstrip("@"))))
        user = next((item for item in result.users if isinstance(item, User)), None)
        if not user or result.chats and not result.users:
            raise GatewayError(gw.NOT_USER, "channel or group")
        if user.bot:
            raise GatewayError(gw.NOT_USER, "bot")
        if user.deleted:
            raise GatewayError(gw.RECIPIENT_GONE, "deleted")
        return Peer(int(user.id), int(user.access_hash or 0))

    async def resolve_contact(self, account_id, phone):
        from telethon.tl.functions.contacts import GetContactsRequest

        client = self._require(account_id)
        digits = "".join(ch for ch in phone if ch.isdigit())
        contacts = await self._guard(client(GetContactsRequest(hash=0)))
        user = next((item for item in getattr(contacts, "users", [])
                     if "".join(ch for ch in (item.phone or "") if ch.isdigit()) == digits), None)
        if not user:
            raise GatewayError(gw.NOT_FOUND, "not in contacts")
        return Peer(int(user.id), int(user.access_hash or 0))

    async def send_message(self, account_id, peer, text, random_id):
        from telethon.tl.functions.messages import SendMessageRequest
        from telethon.tl.types import InputPeerUser

        client = self._require(account_id)
        request = SendMessageRequest(InputPeerUser(peer.user_id, peer.access_hash), text,
                                     no_webpage=True, random_id=random_id)
        result = await self._guard(client(request))
        return SentMessage(*_message_id(result, random_id), type(result).__name__)

    async def find_outgoing(self, account_id, peer, text, since):
        from telethon.tl.types import InputPeerUser

        client = self._require(account_id)
        messages = await self._guard(client.get_messages(InputPeerUser(peer.user_id, peer.access_hash), limit=20))
        for message in messages:
            if message.out and (message.message or "") == text and message.date >= since:
                return int(message.id)
        return None

    def set_incoming_handler(self, handler):
        self._handler = handler
