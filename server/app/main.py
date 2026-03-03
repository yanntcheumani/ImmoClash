from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

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

MIN_RENT_LISTINGS_SEED = 30


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

    # Synchronise aussi quand le volume persistant existe deja:
    # merge/copie les nouvelles images seed du repo vers /var/data/public/listings.
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


def _normalize_listing_from_json(raw: dict[str, object]) -> dict[str, object]:
    required = ("id", "title", "type", "price", "city", "country", "images")
    missing = [key for key in required if key not in raw]
    if missing:
        raise ValueError(f"listing invalide: champs manquants {', '.join(missing)}")

    listing_id = str(raw["id"])
    images: list[str] = []
    for image in raw.get("images", []):  # type: ignore[arg-type]
        clean = str(image).lstrip("/")
        if not clean.startswith("listings/"):
            clean = f"listings/{listing_id}/{clean}"
        images.append(clean)

    return {
        "id": listing_id,
        "title": str(raw["title"]),
        "type": str(raw["type"]),
        "price": float(raw["price"]),
        "currency": str(raw.get("currency", "EUR")),
        "city": str(raw["city"]),
        "country": str(raw["country"]),
        "address": raw.get("address"),
        "lat": raw.get("lat"),
        "lng": raw.get("lng"),
        "surface": raw.get("surface"),
        "rooms": raw.get("rooms"),
        "dpe": raw.get("dpe"),
        "source_url": raw.get("source_url"),
        "images": images,
    }


def _seed_db_from_repo_json_if_empty() -> int:
    with connect(SETTINGS.db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM listings").fetchone()
        if row and int(row["c"]) > 0:
            return 0

    json_path: Path = SETTINGS.repo_root / "data" / "listings.json"
    if not json_path.exists():
        return 0

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0

    if not isinstance(data, list):
        return 0

    inserted = 0
    for raw in data:
        if not isinstance(raw, dict):
            continue
        try:
            listing = _normalize_listing_from_json(raw)
            upsert_listing(SETTINGS.db_path, listing)
            inserted += 1
        except Exception:
            continue

    return inserted


def _seed_builtin_fallback_if_needed(min_rent_count: int = MIN_RENT_LISTINGS_SEED) -> int:
    with connect(SETTINGS.db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM listings WHERE type = 'rent'").fetchone()
        current_rent_count = int(row["c"]) if row else 0

        existing_auto_rows = conn.execute(
            "SELECT id FROM listings WHERE id LIKE 'fallback-rent-auto-%'"
        ).fetchall()

    if current_rent_count >= min_rent_count:
        return 0

    existing_auto_ids = {str(row["id"]) for row in existing_auto_rows}
    next_auto_index = 1
    while f"fallback-rent-auto-{next_auto_index:03d}" in existing_auto_ids:
        next_auto_index += 1

    city_pool = (
        ("Paris", 48.8566, 2.3522, 1180),
        ("Lyon", 45.7640, 4.8357, 920),
        ("Bordeaux", 44.8378, -0.5792, 980),
        ("Toulouse", 43.6047, 1.4442, 890),
        ("Nantes", 47.2184, -1.5536, 860),
        ("Lille", 50.6292, 3.0573, 910),
        ("Rennes", 48.1173, -1.6778, 840),
        ("Montpellier", 43.6110, 3.8767, 870),
        ("Strasbourg", 48.5734, 7.7521, 900),
        ("Marseille", 43.2965, 5.3698, 930),
    )
    dpe_pool = ("A", "B", "C", "D", "E")

    inserted = 0
    missing = max(0, min_rent_count - current_rent_count)
    for offset in range(missing):
        city, base_lat, base_lng, base_price = city_pool[(next_auto_index + offset) % len(city_pool)]
        rooms = 1 + ((next_auto_index + offset) % 4)
        surface = 19 + ((next_auto_index + offset) % 8) * 7 + rooms * 3
        price = base_price + rooms * 65 + int(surface * 1.4) + ((next_auto_index + offset) % 5) * 35
        dpe = dpe_pool[(next_auto_index + offset) % len(dpe_pool)]

        listing = {
            "id": f"fallback-rent-auto-{next_auto_index + offset:03d}",
            "title": f"Location appartement {rooms} pieces {surface}m2 - {city}",
            "type": "rent",
            "price": float(price),
            "currency": "EUR",
            "city": city,
            "country": "FR",
            "address": city,
            "lat": base_lat + ((offset % 5) - 2) * 0.004,
            "lng": base_lng + ((offset % 7) - 3) * 0.004,
            "surface": float(surface),
            "rooms": rooms,
            "dpe": dpe,
            "source_url": None,
            "images": [],
        }
        upsert_listing(SETTINGS.db_path, listing)
        inserted += 1

    return inserted


@fastapi_app.on_event("startup")
async def on_startup() -> None:
    _bootstrap_public_assets_if_empty()
    init_db(SETTINGS.db_path)
    _seed_db_from_repo_snapshot_if_empty()
    _seed_db_from_repo_json_if_empty()
    _seed_builtin_fallback_if_needed()


@fastapi_app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@fastapi_app.get("/api/admin/listings-count")
async def listings_count() -> dict:
    with connect(SETTINGS.db_path) as conn:
        row = conn.execute("SELECT COUNT(*) as c FROM listings").fetchone()
    return {"count": int(row["c"]) if row else 0}


@fastapi_app.get("/api/admin/diagnostics")
async def diagnostics() -> dict:
    with connect(SETTINGS.db_path) as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM listings").fetchone()
        rent = conn.execute("SELECT COUNT(*) as c FROM listings WHERE type='rent'").fetchone()
    return {
        "dbPath": str(SETTINGS.db_path),
        "dbExists": SETTINGS.db_path.exists(),
        "publicDir": str(SETTINGS.public_dir),
        "publicDirExists": SETTINGS.public_dir.exists(),
        "seedSnapshotDbExists": (SETTINGS.repo_root / "data" / "immo_clash.db").exists(),
        "seedJsonExists": (SETTINGS.repo_root / "data" / "listings.json").exists(),
        "minRentSeedTarget": MIN_RENT_LISTINGS_SEED,
        "totalListings": int(total["c"]) if total else 0,
        "rentListings": int(rent["c"]) if rent else 0,
    }


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


@fastapi_app.post("/api/admin/seed-fallback")
async def admin_seed_fallback() -> dict:
    inserted = _seed_builtin_fallback_if_needed()
    with connect(SETTINGS.db_path) as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM listings").fetchone()
        rent = conn.execute("SELECT COUNT(*) as c FROM listings WHERE type='rent'").fetchone()
    return {
        "inserted": int(inserted),
        "totalListings": int(total["c"]) if total else 0,
        "rentListings": int(rent["c"]) if rent else 0,
        "targetRentListings": MIN_RENT_LISTINGS_SEED,
    }


app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app, socketio_path="socket.io")
