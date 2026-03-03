from __future__ import annotations

import socketio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import SETTINGS
from .db import connect, init_db
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


@fastapi_app.on_event("startup")
async def on_startup() -> None:
    init_db(SETTINGS.db_path)


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
