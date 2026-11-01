"""SHA-256 of read-only originals (parsers, original Mini App, owner folders).

Usage: python scripts/mailing_hash_originals.py OUT.txt
Compare two runs with: python scripts/mailing_hash_originals.py --compare BEFORE.txt AFTER.txt
"""
from __future__ import annotations

import csv
import hashlib
import subprocess
import sys
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent
MAIN = Path(r"C:\Users\User\Documents\projects\parserYand")
MINI = Path(r"C:\Users\User\Documents\projects\lead-mini-app")
DOCS = Path(r"C:\Users\User\Documents")
PARSERS = ("yandex_parser_fixed.py", "yandex_images_parser.py", "yandex_products_parser.py")
# Original Mini App inside parserYand: server API, bot and its static web page.
MINIAPP_IN_REPO = ("app/api.py", "app/bot.py", "app/static/index.html", "app/static/app.js",
                   "app/static/style.css")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def targets() -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    for root, label in ((WORKTREE, "worktree"), (MAIN, "main")):
        for name in (*PARSERS, *MINIAPP_IN_REPO):
            items.append((f"{label}:{name}", root / name))
    tracked = subprocess.run(["git", "-C", str(MINI), "ls-files", "-z"], capture_output=True,
                             check=True).stdout.decode("utf-8").split("\0")
    items += [(f"lead-mini-app:{name}", MINI / name) for name in tracked if name]
    for folder, label in ((DOCS / "knowledge_sales", "Documents/knowledge_sales"),
                          (DOCS / "база", "Documents/база"),
                          (MAIN / "LeadGen Base", "parserYand/LeadGen Base"),
                          (MAIN / "база", "parserYand/база"),
                          (MAIN / "knowledge_sales", "parserYand/knowledge_sales")):
        if folder.is_dir():
            items += [(f"{label}:{p.relative_to(folder).as_posix()}", p)
                      for p in sorted(folder.rglob("*")) if p.is_file()]
    return items


def write(out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["item", "sha256"])
        for label, path in targets():
            writer.writerow([label, sha(path) if path.is_file() else "MISSING"])


def compare(before: Path, after: Path) -> int:
    load = lambda p: dict(list(csv.reader(p.open(encoding="utf-8")))[1:])
    a, b = load(before), load(after)
    changed = sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))
    print(f"items before={len(a)} after={len(b)} changed={len(changed)}")
    for key in changed:
        print("CHANGED", key)
    return 1 if changed else 0


if __name__ == "__main__":
    if sys.argv[1] == "--compare":
        raise SystemExit(compare(Path(sys.argv[2]), Path(sys.argv[3])))
    write(Path(sys.argv[1]))
