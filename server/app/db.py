from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Listing

MIN_RENT_PRICE = 100.0
MAX_RENT_PRICE = 30000.0


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('sale', 'rent')),
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    city TEXT NOT NULL,
    country TEXT NOT NULL,
    address TEXT,
    lat REAL,
    lng REAL,
    surface REAL,
    rooms INTEGER,
    dpe TEXT,
    source_url TEXT,
    images_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _ensure_column(conn, "listings", "address", "TEXT")
        _ensure_column(conn, "listings", "source_url", "TEXT")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, sql_type: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    known = {row["name"] for row in rows}
    if column not in known:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")


def upsert_listing(db_path: Path, payload: dict) -> None:
    images = payload.get("images", [])
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO listings (
                id, title, type, price, currency, city, country, address, lat, lng, surface, rooms, dpe, source_url, images_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                type=excluded.type,
                price=excluded.price,
                currency=excluded.currency,
                city=excluded.city,
                country=excluded.country,
                address=excluded.address,
                lat=excluded.lat,
                lng=excluded.lng,
                surface=excluded.surface,
                rooms=excluded.rooms,
                dpe=excluded.dpe,
                source_url=excluded.source_url,
                images_json=excluded.images_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                payload["id"],
                payload["title"],
                payload["type"],
                float(payload["price"]),
                payload.get("currency", "EUR"),
                payload["city"],
                payload.get("country", "FR"),
                payload.get("address"),
                payload.get("lat"),
                payload.get("lng"),
                payload.get("surface"),
                payload.get("rooms"),
                payload.get("dpe"),
                payload.get("source_url"),
                json.dumps(images),
            ),
        )


def row_to_listing(row: sqlite3.Row) -> Listing:
    return Listing(
        id=row["id"],
        title=row["title"],
        type=row["type"],
        price=float(row["price"]),
        currency=row["currency"],
        city=row["city"],
        country=row["country"],
        address=row["address"],
        lat=row["lat"],
        lng=row["lng"],
        surface=row["surface"],
        rooms=row["rooms"],
        dpe=row["dpe"],
        images=json.loads(row["images_json"]),
        source_url=row["source_url"],
    )


def get_listing_by_id(db_path: Path, listing_id: str) -> Listing | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if not row:
        return None
    return row_to_listing(row)


def get_random_listings(db_path: Path, count: int, mode: str, exclude_ids: set[str]) -> list[Listing]:
    sql = "SELECT * FROM listings WHERE 1=1"
    params: list = []

    if mode == "rent":
        sql += " AND type = 'rent' AND price >= ? AND price <= ?"
        params.extend([MIN_RENT_PRICE, MAX_RENT_PRICE])
    if mode == "sqm":
        sql += " AND surface IS NOT NULL AND surface > 0"

    if exclude_ids:
        placeholders = ",".join("?" for _ in exclude_ids)
        sql += f" AND id NOT IN ({placeholders})"
        params.extend(sorted(exclude_ids))

    sql += " ORDER BY RANDOM() LIMIT ?"
    params.append(count)

    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    return [row_to_listing(r) for r in rows]


def get_random_listings_from_ids(
    db_path: Path,
    ids: list[str],
    count: int,
    mode: str,
    exclude_ids: set[str],
) -> list[Listing]:
    if not ids:
        return []

    sql = "SELECT * FROM listings WHERE id IN ({id_placeholders})"
    id_placeholders = ",".join("?" for _ in ids)
    sql = sql.format(id_placeholders=id_placeholders)
    params: list = list(ids)

    if mode == "rent":
        sql += " AND type = 'rent' AND price >= ? AND price <= ?"
        params.extend([MIN_RENT_PRICE, MAX_RENT_PRICE])
    if mode == "sqm":
        sql += " AND surface IS NOT NULL AND surface > 0"
    if exclude_ids:
        placeholders = ",".join("?" for _ in exclude_ids)
        sql += f" AND id NOT IN ({placeholders})"
        params.extend(sorted(exclude_ids))

    sql += " ORDER BY RANDOM() LIMIT ?"
    params.append(count)

    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    return [row_to_listing(row) for row in rows]
