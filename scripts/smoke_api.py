"""In-process API smoke test; does not contact Telegram or Yandex."""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
with tempfile.TemporaryDirectory(dir=ROOT / "tests") as data_dir:
    os.environ["DATA_DIR"] = data_dir
    from app import api, db

    api.BOT_TOKEN = "test-token"
    api.OWNER_TELEGRAM_ID = 123456
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": 123456}, separators=(",", ":"))}
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", b"test-token", hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    headers = {"X-Telegram-Init-Data": urlencode(fields)}

    with TestClient(api.app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/config").status_code == 401
        assert client.get("/api/config", headers=headers).status_code == 200
        response = client.post("/api/search-jobs", headers=headers, json={
            "niche": "кафе", "country": "Россия", "city": "Казань", "count": 2,
            "types": ["no_site", "weak_site"]})
        assert response.status_code == 200, response.text
        job_id = response.json()["id"]
        assert client.get(f"/api/jobs/{job_id}", headers=headers).status_code == 200
        db.upsert_business({"id": "123", "name": "Кафе", "phones": [], "socials": [],
                            "site_type": "no_site", "map_url": "https://yandex.ru/maps/org/cafe/123/"},
                           ("кафе", "Россия", "Казань"), job_id)
        response = client.patch("/api/businesses/123", headers=headers,
                                json={"status": "waiting", "note": "Тест"})
        assert response.status_code == 200, response.text
        assert response.json()["note"] == "Тест"
        assert client.get("/api/stats", headers=headers).json()["contacted"] == 1
        assert len(client.get(f"/api/jobs/{job_id}/businesses", headers=headers).json()) == 1
        assert client.get("/api/filters", headers=headers).json()["city"] == ["Казань"]
        assert len(client.get("/api/businesses/123/scripts", headers=headers).json()) == 5
        print("API smoke test passed")
