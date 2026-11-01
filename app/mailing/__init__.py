"""Telegram mailing module for the desktop app (MTProto user accounts).

Layout:
- gateway.py          TelegramGateway interface and error kinds
- telethon_gateway.py real implementation (Telethon)
- fake_gateway.py     FakeGateway for tests, can simulate Telegram errors
- store.py            SQLite state (separate mailing.sqlite3, WAL)
- policy.py           limits, warm-up, pauses, recipient send window
- engine.py           campaign runner (idempotent sending)
- service.py          operations used by the desktop bridge
- protect.py          DPAPI encryption and folder permissions
- logsafe.py          log filter that masks phones, keys and sessions
"""
