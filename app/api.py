from __future__ import annotations

import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .bot import start_bot
from .config import BOT_TOKEN, DEV_AUTH, OWNER_TELEGRAM_ID
from .domain import COUNTRIES, SITE_TYPES, STATUSES, render_template
from .script_templates import FIRST_BY_SITE_TYPE


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    start_bot()
    yield


app = FastAPI(title="Lead CRM Mini App", lifespan=lifespan)


def verify_init_data(raw: str) -> bool:
    if not BOT_TOKEN or not OWNER_TELEGRAM_ID or not raw:
        return False
    try:
        pairs = dict(parse_qsl(raw, keep_blank_values=True, strict_parsing=True))
        supplied_hash = pairs.pop("hash")
        data_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, data_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied_hash, expected):
            return False
        auth_date = int(pairs["auth_date"])
        if abs(time.time() - auth_date) > 86400:
            return False
        user = json.loads(pairs["user"])
        return int(user["id"]) == OWNER_TELEGRAM_ID
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return False


def owner(request: Request, x_telegram_init_data: str = Header(default=""), x_dev_auth: str = Header(default="")):
    if DEV_AUTH and request.client and request.client.host in {"127.0.0.1", "::1"} and x_dev_auth == "1":
        return
    if not verify_init_data(x_telegram_init_data):
        raise HTTPException(status_code=401, detail="Откройте приложение через своего Telegram-бота")


class SearchInput(BaseModel):
    niche: str = Field(min_length=2, max_length=100)
    country: str
    city: str = Field(min_length=2, max_length=100)
    count: int = Field(ge=1, le=100)
    types: list[str] = Field(min_length=1)


class BusinessInput(BaseModel):
    status: str | None = None
    note: str | None = Field(default=None, max_length=10000)


class TemplateInput(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/config", dependencies=[Depends(owner)])
def config():
    return {"countries": COUNTRIES, "statuses": STATUSES,
            "site_types": {"no_site": "Без сайта", "weak_site": "Конструктор",
                           "unavailable": "Сайт недоступен"}}


@app.post("/api/search-jobs", dependencies=[Depends(owner)])
def start_search(data: SearchInput):
    if data.country not in COUNTRIES:
        raise HTTPException(422, "Страна не поддерживается")
    if not set(data.types).issubset(SITE_TYPES):
        raise HTTPException(422, "Неизвестный тип сайта")
    params = data.model_dump()
    params["niche"] = params["niche"].strip()
    params["city"] = params["city"].strip()
    params["types"] = list(dict.fromkeys(params["types"]))
    job_id = db.create_job("search", params)
    return {"id": job_id}


@app.get("/api/jobs", dependencies=[Depends(owner)])
def jobs(kind: str = "search"):
    if kind not in {"search", "images", "products"}:
        raise HTTPException(422, "Неизвестный тип задания")
    return db.list_jobs(kind)


@app.get("/api/jobs/{job_id}", dependencies=[Depends(owner)])
def get_job(job_id: str):
    item = db.job(job_id)
    if not item:
        raise HTTPException(404, "Задание не найдено")
    return item


@app.get("/api/jobs/{job_id}/businesses", dependencies=[Depends(owner)])
def job_businesses(job_id: str):
    item = db.job(job_id)
    if not item or item["kind"] != "search":
        raise HTTPException(404, "Поиск не найден")
    with db.connection() as conn:
        ids = [row[0] for row in conn.execute(
            "SELECT business_id FROM job_results WHERE job_id=? ORDER BY rowid DESC", (job_id,))]
    return [db.business(business_id) for business_id in ids]


@app.get("/api/filters", dependencies=[Depends(owner)])
def filters():
    return db.filter_options()


@app.get("/api/businesses", dependencies=[Depends(owner)])
def businesses(niche: str = "", country: str = "", city: str = "", site_type: str = "",
               status: str = "", q: str = "", limit: int = Query(50, ge=1, le=100),
               offset: int = Query(0, ge=0)):
    return db.list_businesses({"niche": niche, "country": country, "city": city,
                               "site_type": site_type, "status": status, "q": q}, limit=limit, offset=offset)


@app.get("/api/businesses/{business_id}", dependencies=[Depends(owner)])
def get_business(business_id: str):
    item = db.business(business_id)
    if not item:
        raise HTTPException(404, "Бизнес не найден")
    return item


@app.patch("/api/businesses/{business_id}", dependencies=[Depends(owner)])
def patch_business(business_id: str, data: BusinessInput):
    try:
        found = db.set_business(business_id, data.status, data.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not found:
        raise HTTPException(404, "Бизнес не найден")
    return db.business(business_id)


@app.get("/api/stats", dependencies=[Depends(owner)])
def statistics():
    return db.stats()


@app.get("/api/templates", dependencies=[Depends(owner)])
def get_templates():
    return db.templates()


@app.put("/api/templates/{slug}", dependencies=[Depends(owner)])
def put_template(slug: str, data: TemplateInput):
    if not db.save_template(slug, data.body):
        raise HTTPException(404, "Шаблон не найден")
    return {"ok": True}


@app.get("/api/businesses/{business_id}/scripts", dependencies=[Depends(owner)])
def business_scripts(business_id: str):
    item = db.business(business_id)
    if not item:
        raise HTTPException(404, "Бизнес не найден")
    first = FIRST_BY_SITE_TYPE.get(item["site_type"], "first")
    selected = {first, "short", "detailed", "call", "followup"}
    return [{"slug": template["slug"], "title": template["title"],
             "text": render_template(template["body"], item)}
            for template in db.templates() if template["slug"] in selected]


STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
