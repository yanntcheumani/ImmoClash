from __future__ import annotations

import json
import shutil
import sqlite3

import socketio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import SETTINGS
from .db import connect, init_db, upsert_listing
from .models import PRICE_MODES
from .scraper import run_scrape_job
from .socket_server import sio

fastapi_app = FastAPI(title="ImmoClash Server", version="1.0.0")

cors_origins = list(SETTINGS.cors_origins)
allow_credentials = "*" not in SETTINGS.cors_origins

fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

SETTINGS.public_dir.mkdir(parents=True, exist_ok=True)
fastapi_app.mount("/public", StaticFiles(directory=str(SETTINGS.public_dir)), name="public")


class LiveScrapeRequest(BaseModel):
    searchQuery: str = Field(default=SETTINGS.default_search_query, min_length=2, max_length=80)
    roundsCount: int = Field(default=5, ge=1, le=20)
    priceMode: str = Field(default="rent")


def _bootstrap_public_assets_if_empty() -> None:
    source_listings = SETTINGS.repo_root / "public" / "listings"
    target_listings = SETTINGS.public_dir / "listings"
    if not source_listings.exists():
        return

    if not target_listings.exists():
        target_listings.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_listings, target_listings)
        return

    try:
        has_any_file = any(target_listings.rglob("*"))
    except OSError:
        has_any_file = False

    if not has_any_file:
        shutil.copytree(source_listings, target_listings, dirs_exist_ok=True)


def _seed_db_from_repo_snapshot_if_empty() -> int:
    seed_db_path = SETTINGS.repo_root / "data" / "immo_clash.db"
    if not seed_db_path.exists():
        return 0

    try:
        same_file = seed_db_path.resolve() == SETTINGS.db_path.resolve()
    except OSError:
        same_file = False
    if same_file:
        return 0

    with connect(SETTINGS.db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM listings").fetchone()
        if row and int(row["c"]) > 0:
            return 0

    with sqlite3.connect(str(seed_db_path)) as seed_conn:
        seed_conn.row_factory = sqlite3.Row
        rows = seed_conn.execute(
            """
            SELECT
                id, title, type, price, currency, city, country,
                address, lat, lng, surface, rooms, dpe, source_url, images_json
            FROM listings
            """
        ).fetchall()

    inserted = 0
    for row in rows:
        try:
            images = json.loads(row["images_json"]) if row["images_json"] else []
        except ValueError:
            images = []
        payload = {
            "id": row["id"],
            "title": row["title"],
            "type": row["type"],
            "price": row["price"],
            "currency": row["currency"],
            "city": row["city"],
            "country": row["country"],
            "address": row["address"],
            "lat": row["lat"],
            "lng": row["lng"],
            "surface": row["surface"],
            "rooms": row["rooms"],
            "dpe": row["dpe"],
            "source_url": row["source_url"],
            "images": images,
        }
        upsert_listing(SETTINGS.db_path, payload)
        inserted += 1

    return inserted


@fastapi_app.on_event("startup")
async def on_startup() -> None:
    _bootstrap_public_assets_if_empty()
    init_db(SETTINGS.db_path)
    _seed_db_from_repo_snapshot_if_empty()


@fastapi_app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@fastapi_app.get("/api/admin/listings-count")
async def listings_count() -> dict:
    with connect(SETTINGS.db_path) as conn:
        row = conn.execute("SELECT COUNT(*) as c FROM listings").fetchone()
    return {"count": int(row["c"]) if row else 0}


@fastapi_app.post("/api/admin/scrape")
async def admin_scrape(payload: LiveScrapeRequest) -> dict:
    try:
        if payload.priceMode not in PRICE_MODES:
            raise ValueError("priceMode invalide")
        return await run_scrape_job(
            db_path=SETTINGS.db_path,
            public_dir=SETTINGS.public_dir,
            search_query=payload.searchQuery,
            rounds_count=payload.roundsCount,
            price_mode=payload.priceMode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app, socketio_path="socket.io")
