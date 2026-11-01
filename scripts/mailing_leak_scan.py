"""Scan files, folders or zip archives for secrets without printing their values.

Usage: python scripts/mailing_leak_scan.py PATH [PATH ...] [--known secrets/accounts.json]
Exit code 1 when anything is found. Output lists only path, rule and a masked hint.
--known: also searches for the exact api_id/api_hash/phone values from the owner's file.
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

NAME_RULES = [
    (re.compile(r"\.session(-journal)?$", re.I), "telethon-session-file"),
    (re.compile(r"(^|/)\.env($|\.)(?!example)", re.I), "env-file"),
    (re.compile(r"(^|/)secrets/", re.I), "secrets-folder"),
    (re.compile(r"(^|/)(telegram|login_state|accounts(\.local)?)\.json$", re.I), "telegram-config"),
    (re.compile(r"(^|/)mailing/(keys|sessions)/", re.I), "mailing-session-store"),
    (re.compile(r"(^|/)key\.txt$", re.I), "key-file"),
]
CONTENT_RULES = [
    (re.compile(rb"api_hash['\"]?\s*[:=]\s*['\"][0-9a-fA-F]{32}['\"]"), "api_hash-literal"),
    (re.compile(rb"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}\b"), "bot-token"),
    (re.compile(rb"sk-or-v1-[0-9a-f]{40,}"), "openrouter-key"),
    (re.compile(rb"\b1[A-Za-z0-9_-]{300,}={0,2}"), "telethon-string-session"),
    (re.compile(rb"SQLite format 3\x00.{0,4096}?CREATE TABLE sessions", re.S), "telethon-sqlite-session"),
]
SKIP_DIRS = {".git", "node_modules", ".venv", ".build-venv", "__pycache__", ".deps"}
TEXT_LIMIT = 30_000_000
BINARY_SUFFIXES = {".exe", ".dll", ".pyd", ".so", ".zip", ".woff2", ".png", ".ico", ".jpg", ".pdf"}
# Third-party package docs contain placeholder api_hash examples (e.g. telethon METADATA).
ALLOW_CONTENT = re.compile(r"\.dist-info/(METADATA|RECORD)$")


def mask(value: bytes) -> str:
    text = value.decode("latin-1")
    return (text[:2] + "••••") if len(text) > 2 else "••"


def known_values(path: Path | None) -> list[tuple[bytes, str]]:
    if not path or not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    values = []
    if data.get("api_id"):
        values.append((str(data["api_id"]).encode(), "owner-api_id"))
    if data.get("api_hash"):
        values.append((str(data["api_hash"]).encode(), "owner-api_hash"))
    for account in data.get("accounts", []):
        digits = re.sub(r"\D", "", str(account.get("phone", "")))
        if len(digits) >= 10:
            values.append((digits.encode(), "owner-phone"))
    for item in data.get("test_recipients", []) if isinstance(data.get("test_recipients"), list) else []:
        text = str(item.get("peer", item) if isinstance(item, dict) else item)
        digits = re.sub(r"\D", "", text)
        if len(digits) >= 10:
            values.append((digits.encode(), "owner-test-recipient-phone"))
        elif len(text) >= 5:
            values.append((text.lstrip("@").encode(), "owner-test-recipient"))
    return [v for v in values if len(v[0]) >= 5]


def scan_bytes(name: str, data: bytes, known, findings: list) -> None:
    norm = name.replace("\\", "/")
    for rule, label in NAME_RULES:
        if rule.search(norm) and not norm.endswith(".example.json"):
            findings.append((norm, label, ""))
    binary = Path(norm.split("!")[-1]).suffix.lower() in BINARY_SUFFIXES
    for rule, label in CONTENT_RULES:
        if ALLOW_CONTENT.search(norm) or (binary and label != "telethon-sqlite-session"):
            continue
        match = rule.search(data)
        if match:
            findings.append((norm, label, mask(match.group(0))))
    for value, label in known:
        if value in data:
            findings.append((norm, label, mask(value)))


def scan(path: Path, known, findings: list) -> int:
    count = 0
    if path.is_file() and path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                count += 1
                scan_bytes(f"{path.name}!{info.filename}", archive.read(info)[:TEXT_LIMIT], known, findings)
        return count
    files = [path] if path.is_file() else [p for p in path.rglob("*")
                                         if p.is_file() and not SKIP_DIRS & set(p.parts)]
    for file in files:
        count += 1
        try:
            data = file.read_bytes()[:TEXT_LIMIT]
        except OSError:
            continue
        scan_bytes(str(file), data, known, findings)
    return count


def main(argv: list[str]) -> int:
    known_path = None
    if "--known" in argv:
        index = argv.index("--known")
        known_path = Path(argv[index + 1])
        argv = argv[:index] + argv[index + 2:]
    known = known_values(known_path)
    findings: list = []
    total = sum(scan(Path(item), known, findings) for item in argv)
    print(f"scanned_files={total} known_values={len(known)} findings={len(findings)}")
    for name, label, hint in findings:
        print(f"FOUND {label}: {name} {hint}".rstrip())
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
