"""Live search speed measurement on a temporary DATA_DIR (never the owner's data).

    .build-venv\\Scripts\\python.exe scripts\\measure_search_speed.py --data .build-tmp\\speed --niche ресторан --city Тюмень --count 30 --reviews 10

Prints one JSON line per run: cards scanned, found, seconds, cards per minute and seconds per stage.
No organization names, URLs or phones are printed. A captcha ends the run with status «blocked».
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--niche", required=True)
    parser.add_argument("--city", required=True)
    parser.add_argument("--country", default="Россия")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--reviews", type=int, default=0)
    args = parser.parse_args()
    data = Path(args.data).resolve()
    appdata = Path(os.environ.get("APPDATA", "")) / "LeadEqualMoney"
    if appdata.exists() and (data == appdata or appdata in data.parents):
        raise SystemExit("Нельзя мерить на реальных данных владельца")
    os.environ["DATA_DIR"] = str(data)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import db
    from app.jobs import run_job

    db.init_desktop_db()
    created = db.create_desktop_parse_jobs({"country": args.country, "niche": args.niche, "count": args.count,
                                            "types": ["no_site", "weak_site", "unavailable"],
                                            "review_limit": args.reviews, "min_reviews": 0}, [args.city])
    item = db.claim_job()
    started = time.monotonic()
    status = run_job(item)
    seconds = time.monotonic() - started
    job = db.job(created[0]["id"])
    result = job.get("result") or {}
    progress = job.get("progress") or {}
    scanned = int(result.get("scanned") or progress.get("scanned") or 0)
    with db.connection() as conn:
        reviews = conn.execute("""SELECT COUNT(*) FROM reviews r JOIN job_results jr ON jr.business_id=r.business_id
            WHERE jr.job_id=?""", (created[0]["id"],)).fetchone()[0]
    print(json.dumps({
        "niche": args.niche, "city": args.city, "target": args.count, "review_limit": args.reviews,
        "status": status, "scanned": scanned, "found": db.job_result_count(created[0]["id"]),
        "reviews_saved": reviews, "seconds_total": round(seconds, 1),
        "seconds_cards_pass": round(seconds - sum(v["s"] for k, v in (result.get("timing") or {}).items()
                                                  if k in {"reviews", "gallery"}), 1),
        "cards_per_minute": round(scanned * 60 / seconds, 1) if seconds else None,
        "timing": result.get("timing"), "reason": result.get("reason") or job.get("error"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
