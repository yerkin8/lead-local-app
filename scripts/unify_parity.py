"""Same inputs through the Mini App code and the Desktop code; outputs compared byte for byte (docs/unify U2).

Each side runs in its own process, with its own repository on sys.path, its own interpreter and a temporary
DATA_DIR. Inputs: four reference organisations, parser strings and a small search page, a copy of a Mini App
nightly base (stats) and a copy of the real Desktop base (migration). Nothing touches real data.
Usage: python scripts/unify_parity.py MINI_PYTHON DESK_PYTHON MINI_ROOT NIGHTLY_COPY DESK_BASE_COPY
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DESK_ROOT = Path(__file__).resolve().parents[1]
OUT = DESK_ROOT / "verification" / "unify"

PROBE = r'''
import json, sys, sqlite3, hashlib, shutil
from pathlib import Path
from bs4 import BeautifulSoup
nightly, desk_base, work = sys.argv[1:4]
from app import db, config, lead_parser, script_templates
out = {}
# 1. Offers on the organisation card for four reference organisations.
db.init_desktop_db()
rows = db.templates()
orgs = [
  {"id": "1", "name": "Кафе Ромашка", "category": "Кафе", "niche": "кафе", "city": "Тула", "site_type": "no_site"},
  {"id": "2", "name": "Барбершоп Борода", "category": "Барбершоп", "niche": "барбершоп", "city": "Тула", "site_type": "weak_site", "site_engine": "Tilda"},
  {"id": "3", "name": "Окна Плюс", "category": "Окна", "niche": "окна", "city": "Тула", "site_type": "unavailable"},
  {"id": "4", "name": "Нотариус Иванова", "category": "Нотариусы", "niche": "нотариус", "city": "Тула", "site_type": "no_site"},
]
out["offers"] = [[(o["category"], o.get("slug"), o.get("text")) for o in script_templates.offers(b, rows)] for b in orgs]
# 2. Parser: review counts, threshold, next pages after a yandex.com redirect.
out["review_count"] = [lead_parser.review_count(s) for s in ("1 234 отзыва", "1 234", "1,2 тыс.", "15 отзывов", "", None)]
out["min_reviews"] = [lead_parser.fits({"reviews": r}, {"min_reviews": 50}) for r in (10, 50, 500, None)]
page = '<a href="/maps/org/x/1/">a</a><a href="?page=2">2</a><a href="https://yandex.com/maps/?text=x&page=3">3</a>'
out["search_links"] = lead_parser.http_search_links("https://yandex.com/maps/?text=x", BeautifulSoup(page, "html.parser"))
# 3. Statistics on the same Mini App nightly base.
stats_db = Path(work) / "stats.sqlite3"; shutil.copy(nightly, stats_db)
db.init_desktop_db(stats_db)
out["stats"] = db.stats(stats_db)
# 4. Migration of the real Desktop base (copy): scripts and row counts after.
mig = Path(work) / "mig.sqlite3"; shutil.copy(desk_base, mig)
db.init_desktop_db(mig); db.init_desktop_db(mig)
with sqlite3.connect(mig) as c:
    out["templates"] = sorted((r[0], r[1], r[2], r[3], hashlib.sha256(r[4].encode()).hexdigest()[:12])
                              for r in c.execute("SELECT slug,category,stage,subtype,body FROM templates"))
    out["rows"] = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
                   ("businesses", "business_tags", "status_events", "jobs", "parse_runs", "templates")}
    out["schema"] = c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
print(json.dumps(out, ensure_ascii=False, default=str))
'''


def side(python: str, root: Path, nightly: Path, base: Path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "DATA_DIR": tmp, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(root)}
        out = subprocess.run([python, "-c", PROBE, str(nightly), str(base), tmp], cwd=root, env=env,
                             capture_output=True, text=True, encoding="utf-8", timeout=300)
        if out.returncode:
            raise SystemExit(f"{root}: {out.stderr[-2000:]}")
        return json.loads(out.stdout.strip().splitlines()[-1])


def main(mini_py: str, desk_py: str, mini_root: str, nightly: str, base: str) -> int:
    mini = side(mini_py, Path(mini_root), Path(nightly), Path(base))
    desk = side(desk_py, DESK_ROOT, Path(nightly), Path(base))
    lines, same = [], True
    for key in mini:
        equal = mini[key] == desk[key]
        same &= equal or key == "schema"
        lines.append(f"{key}: {'ОДИНАКОВО' if equal else 'РАЗЛИЧАЕТСЯ'}")
        if not equal:
            lines.append(f"  mini: {json.dumps(mini[key], ensure_ascii=False)[:600]}")
            lines.append(f"  desk: {json.dumps(desk[key], ensure_ascii=False)[:600]}")
    lines.append("schema: отличие ожидаемо — Desktop 7, Mini App 6 (docs/unify/DECISIONS.md U5)")
    lines.append(f"offers (desk, для отчёта): {json.dumps([[o[:2] for o in x] for x in desk['offers']], ensure_ascii=False)}")
    lines.append(f"review_count: {desk['review_count']}; search_links: {desk['search_links']}")
    lines.append(f"stats: {json.dumps(desk['stats'], ensure_ascii=False)[:400]}")
    lines.append(f"rows after migration: {desk['rows']}")
    lines.append("ИТОГ: " + ("совпадает по всем пунктам, кроме номера схемы" if same else "ЕСТЬ РАСХОЖДЕНИЯ"))
    (OUT / "parity.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:6]))
