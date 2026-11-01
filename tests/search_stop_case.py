"""Child process for test_search_stop: DATA_DIR is set before app modules are imported.
No network: Yandex pages and sites are synthetic."""
import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import cancel, db, lead_parser  # noqa: E402
from app.desktop_services import stop_job  # noqa: E402
from app.jobs import run_job  # noqa: E402

db.init_desktop_db()
out = {}
CARDS = 10


def search_page(count=CARDS):
    return "".join(f"<div class='search-business-snippet-view__content'><a href='/maps/org/cafe/{100 + i}/'>К</a></div>"
                   for i in range(count))


def card_page(identity, site=""):
    site_html = f"<a href='{site}'><span class='business-urls-view__text'>{site}</span></a>" if site else ""
    state = json.dumps({"stack": [{"results": {"items": [{
        "id": identity, "ratingData": {"ratingCount": 9, "reviewCount": 3}, "photos": {"count": 7}}]}}]})
    return (f"<h1 class='orgpage-header-view__header'>Кафе {identity}</h1>"
            "<a class='orgpage-header-view__address'>Казань, ул. Пушкина, 1</a>"
            "<div class='business-header-rating-view__text'>9 оценок</div>" + site_html +
            f"<script type='application/json' class='state-view'>{state}</script>")


REVIEWS = "".join(
    "<div class='business-reviews-card-view__review'><div class='business-review-view'>"
    "<meta itemprop='ratingValue' content='5.0'/><meta itemprop='datePublished' content='2026-09-1%dT10:00:00Z'/>"
    "<span class='business-review-view__date'>1%d сентября</span>"
    "<div class='business-review-view__body'><span>Отзыв номер %d, всё понравилось</span></div>"
    "</div></div>" % (i, i, i) for i in range(3))


def new_job(count=5, review_limit=3, niche="кафе", city="Казань"):
    created = db.create_desktop_parse_jobs({"country": "Россия", "niche": niche, "count": count,
                                            "types": ["no_site"], "review_limit": review_limit}, [city])
    return created[0]["id"]


def claim(job_id):
    item = db.claim_job()
    assert item["id"] == job_id, (item["id"], job_id)
    return item


def run_status(job_id):
    with db.connection() as conn:
        return conn.execute("SELECT status FROM parse_runs WHERE job_id=?", (job_id,)).fetchone()[0]


fast = [patch.object(lead_parser, "CARD_PAUSE", (0.0, 0.0)),
        patch.object(lead_parser.random, "uniform", return_value=0.0)]
for item in fast:
    item.start()

# 1. A queued job stops at once and is never claimed.
queued = new_job()
out["queued_stop"] = stop_job(queued)["status"]
out["queued_job_status"] = db.job(queued)["status"]
out["queued_run_status"] = run_status(queued)
out["queued_claimed"] = db.claim_job() is not None

# 2. A running job stops between cards; what was found stays.
running = new_job(count=8, review_limit=0, niche="кафе один")
requests_seen = []


def fake_running(_session, url):
    requests_seen.append(url)
    if "/maps/org/" not in url:
        return url, search_page()
    if sum("/maps/org/" in item for item in requests_seen) == 4:
        stop_job(running)            # pressed «Остановить» while the 4th card loads
    return url, card_page(url.rstrip("/").split("/")[-1])


with patch.object(lead_parser, "request_page", side_effect=fake_running):
    out["running_status"] = run_job(claim(running))
job = db.job(running)
out["running_saved"] = db.job_result_count(running)
out["running_result_matched"] = job["result"].get("matched")
out["running_run_status"] = run_status(running)
out["running_leads_exist"] = all(db.business(str(100 + i)) for i in range(out["running_saved"]))
out["running_card_requests"] = sum("/maps/org/" in item for item in requests_seen)

# 3. Two passes: all card pages first, then reviews; one reviews page gives every review on it.
with db.connection() as conn:   # fresh organizations for the next searches
    conn.execute("DELETE FROM job_results"); conn.execute("DELETE FROM reviews")
    conn.execute("DELETE FROM business_details"); conn.execute("DELETE FROM business_tags")
    conn.execute("DELETE FROM parse_run_businesses"); conn.execute("DELETE FROM businesses")
    conn.execute("DELETE FROM search_seen")
two_pass = new_job(count=3, review_limit=3, niche="кафе два")
order = []


def fake_two_pass(_session, url):
    kind = "reviews" if "/reviews/" in url else "gallery" if "/gallery/" in url else "card" if "/maps/org/" in url else "search"
    order.append(kind)
    if kind == "search":
        return url, search_page()
    if kind == "reviews":
        return url, REVIEWS
    if kind == "gallery":
        return url, "<img class='media-wrapper__media' src='https://example.org/1.jpg'>"
    return url, card_page(url.rstrip("/").split("/")[-1])


with patch.object(lead_parser, "request_page", side_effect=fake_two_pass):
    out["two_pass_status"] = run_job(claim(two_pass))
cards_done = max(i for i, kind in enumerate(order) if kind == "card")
first_detail = min(i for i, kind in enumerate(order) if kind in {"reviews", "gallery"})
out["two_pass_cards_before_details"] = cards_done < first_detail
out["two_pass_review_pages"] = order.count("reviews")
out["two_pass_reviews"] = [db.business_review_count(str(100 + i)) for i in range(3)]
out["two_pass_photo_count"] = db.business_full("100")["details"]["photo_count"]
out["two_pass_gallery_pages"] = order.count("gallery")
out["two_pass_review_field"] = db.business("100")["reviews"]
progress = db.job(two_pass)["progress"]
out["two_pass_progress_phase"] = progress.get("phase")
out["two_pass_timing_stages"] = sorted(db.job(two_pass)["result"].get("timing", {}))

# 4. Stop during the review pass: cards stay, job ends as stopped.
review_stop = new_job(count=3, review_limit=3, niche="кафе три")


def fake_review_stop(_session, url):
    if "/reviews/" in url or "/gallery/" in url:
        stop_job(review_stop)
        return url, REVIEWS
    if "/maps/org/" in url:
        return url, card_page(url.rstrip("/").split("/")[-1])
    return url, search_page(20).replace("cafe/1", "cafe/2")


with patch.object(lead_parser, "request_page", side_effect=fake_review_stop):
    out["review_stop_status"] = run_job(claim(review_stop))
out["review_stop_saved"] = db.job_result_count(review_stop)

# 5. «Остановить» pressed at the moment the job was claimed (before it bound its token).
race = new_job(count=3, review_limit=0, niche="кафе четыре")
item = claim(race)
cancel.request(race)
calls = []
with patch.object(lead_parser, "request_page", side_effect=lambda s, u: calls.append(u) or (u, search_page())):
    out["race_status"] = run_job(item)
out["race_requests"] = len(calls)

# 6. A captcha is still the «капча» status, not an error and not «Остановлен».
blocked = new_job(count=3, review_limit=0, niche="кафе пять")


def fake_blocked(_session, url):
    if "/maps/org/" in url:
        raise lead_parser.SearchBlocked("Яндекс показал капчу")
    return url, search_page().replace("cafe/1", "cafe/3")


with patch.object(lead_parser, "request_page", side_effect=fake_blocked):
    out["blocked_status"] = run_job(claim(blocked))

# 7. Site checks run in parallel: 6 cards with slow sites take about one site time, not six.
with db.connection() as conn:
    conn.execute("DELETE FROM search_seen")
parallel = new_job(count=6, review_limit=0, niche="кафе шесть")
created = db.job(parallel)["params"]
created["types"] = ["unavailable"]
with db.connection() as conn:
    conn.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(created, ensure_ascii=False), parallel))


def fake_parallel(_session, url):
    if "/maps/org/" in url:
        identity = url.rstrip("/").split("/")[-1]
        return url, card_page(identity, f"https://site{identity}.example/")
    return url, search_page(6).replace("cafe/1", "cafe/4")


def slow_site(_site):
    time.sleep(0.6)
    return None, True   # site does not answer → «unavailable»


started = time.monotonic()
with patch.object(lead_parser, "request_page", side_effect=fake_parallel), \
     patch.object(lead_parser, "fetch_site", side_effect=slow_site):
    out["parallel_status"] = run_job(claim(parallel))
out["parallel_seconds"] = round(time.monotonic() - started, 2)
out["parallel_saved"] = db.job_result_count(parallel)
out["threads_left"] = sum(1 for t in threading.enumerate() if t.name.startswith("LEM site check") and t.is_alive())

# 8. One card answers 404: it is skipped and counted, the search goes on and ends «Готово» (E-S-11).
one_bad = new_job(count=3, review_limit=0, niche="кафе семь")


def fake_one_bad(_session, url):
    if url.rstrip("/").endswith("/600"):
        raise lead_parser.PageFailed("Яндекс.Карты вернули HTTP 404")
    if "/maps/org/" in url:
        return url, card_page(url.rstrip("/").split("/")[-1])
    return url, search_page().replace("cafe/1", "cafe/6")


with patch.object(lead_parser, "request_page", side_effect=fake_one_bad):
    out["one_bad_status"] = run_job(claim(one_bad))
out["one_bad_saved"] = db.job_result_count(one_bad)
out["one_bad_errors"] = db.job(one_bad)["result"]["errors"]

# 9. No connection: every card fails. The search is cut and is not «Готово».
no_net = new_job(count=3, review_limit=0, niche="кафе восемь")


def fake_no_net(_session, url):
    if "/maps/org/" in url:
        raise lead_parser.PageFailed("Не удалось загрузить Яндекс.Карты: ConnectionError")
    return url, search_page().replace("cafe/1", "cafe/7")


with patch.object(lead_parser, "request_page", side_effect=fake_no_net):
    out["no_net_status"] = run_job(claim(no_net))
out["no_net_run_status"] = run_status(no_net)

# 10. A captcha during the review pass; the next run of the same search loads the missing reviews (E-S-12).
first_run = new_job(count=3, review_limit=3, niche="кафе девять")


def fake_review_captcha(_session, url):
    if "/reviews/" in url:
        raise lead_parser.SearchBlocked("Яндекс показал капчу")
    if "/maps/org/" in url:
        return url, card_page(url.rstrip("/").split("/")[-1])
    return url, search_page(3).replace("cafe/1", "cafe/8")


with patch.object(lead_parser, "request_page", side_effect=fake_review_captcha):
    out["captcha_first_status"] = run_job(claim(first_run))
out["captcha_first_reviews"] = [db.business_review_count(str(800 + i)) for i in range(3)]
second_run = new_job(count=3, review_limit=3, niche="кафе девять")


def fake_second(_session, url):
    if "/reviews/" in url:
        return url, REVIEWS
    if "/maps/org/" in url:
        return url, card_page(url.rstrip("/").split("/")[-1])
    return url, search_page(3).replace("cafe/1", "cafe/8")


with patch.object(lead_parser, "request_page", side_effect=fake_second):
    out["captcha_second_status"] = run_job(claim(second_run))
out["captcha_second_reviews"] = [db.business_review_count(str(800 + i)) for i in range(3)]

# 11. A typo in the city: every card is from another city, the reason says to check the city (E-S-13).
typo = new_job(count=3, review_limit=0, niche="кафе десять", city="Казнь")


def fake_typo(_session, url):
    if "/maps/org/" in url:
        return url, card_page(url.rstrip("/").split("/")[-1])
    return url, search_page(5).replace("cafe/1", "cafe/9")


with patch.object(lead_parser, "request_page", side_effect=fake_typo):
    out["typo_status"] = run_job(claim(typo))
out["typo_reason"] = db.job(typo)["result"]["reason"]
print(json.dumps(out, ensure_ascii=False))
