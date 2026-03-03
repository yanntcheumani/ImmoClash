#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "server") not in sys.path:
    sys.path.append(str(ROOT / "server"))

from app.config import SETTINGS  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.scraper import scrape_and_store_live_listings  # noqa: E402


DEFAULT_QUERIES = [
    "Paris, France",
    "Marseille, France",
    "Nantes, France",
    "Lyon, France",
    "Toulouse, France",
    "Bordeaux, France",
    "Lille, France",
    "Rennes, France",
    "Montpellier, France",
    "Strasbourg, France",
    "Nice, France",
    "Grenoble, France",
    "Reims, France",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape de vraies annonces et export dataset JSON.")
    parser.add_argument("--count", type=int, default=30, help="Nombre d'annonces a exporter.")
    parser.add_argument("--batch", type=int, default=8, help="Nb de manches demandees par run scrape.")
    parser.add_argument("--max-runs", type=int, default=20, help="Nb max d'appels scrape.")
    parser.add_argument(
        "--queries",
        nargs="*",
        default=DEFAULT_QUERIES,
        help="Liste des zones de recherche (ex: 'Paris, France' 'Lyon, France').",
    )
    parser.add_argument("--db", default=str(ROOT / "data" / "immo_clash.db"), help="SQLite cible.")
    parser.add_argument("--public-dir", default=str(ROOT / "public"), help="Dossier public racine.")
    parser.add_argument("--out-json", default=str(ROOT / "data" / "listings.json"), help="JSON export.")
    return parser.parse_args()


def _fetch_scraped_rent_rows(db_path: Path) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                id, title, type, price, currency, city, country,
                address, lat, lng, surface, rooms, dpe, source_url, images_json
            FROM listings
            WHERE type = 'rent'
              AND source_url IS NOT NULL
              AND TRIM(source_url) <> ''
              AND price >= 100
              AND price <= 30000
            """
        ).fetchall()

    result: list[dict] = []
    for row in rows:
        try:
            images = json.loads(row["images_json"]) if row["images_json"] else []
        except ValueError:
            images = []
        images = [str(img) for img in images if str(img).strip()]
        if not images:
            continue

        result.append(
            {
                "id": row["id"],
                "title": row["title"],
                "type": row["type"],
                "price": float(row["price"]),
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
        )
    return result


async def _run_scrape(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    out_json = Path(args.out_json)
    public_dir = Path(args.public_dir)

    public_dir.mkdir(parents=True, exist_ok=True)
    (public_dir / "listings").mkdir(parents=True, exist_ok=True)
    init_db(db_path)

    queries = [str(q).strip() for q in args.queries if str(q).strip()]
    if not queries:
        queries = DEFAULT_QUERIES

    query_idx = 0
    for run_idx in range(args.max_runs):
        current = queries[query_idx % len(queries)]
        query_idx += 1

        result = await scrape_and_store_live_listings(
            db_path=db_path,
            public_dir=public_dir,
            search_query=current,
            rounds_count=max(1, int(args.batch)),
            price_mode="rent",
        )
        print(
            f"[scrape_seed] run={run_idx + 1}/{args.max_runs} query='{current}' "
            f"fetched={result.get('fetchedCount', 0)} inserted={result.get('inserted', 0)} "
            f"providers={result.get('providersTried', [])}"
        )

        rows = _fetch_scraped_rent_rows(db_path)
        print(f"[scrape_seed] scraped_rent_with_images={len(rows)} target={args.count}")
        if len(rows) >= args.count:
            break

    rows = _fetch_scraped_rent_rows(db_path)
    if len(rows) < args.count:
        print(
            f"[scrape_seed] KO: annonces reelles insuffisantes ({len(rows)}/{args.count}). "
            "Augmente --max-runs ou ajoute des --queries."
        )
        return 1

    random.shuffle(rows)
    selected = rows[: args.count]
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[scrape_seed] OK exported={len(selected)} -> {out_json}")
    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run_scrape(args))


if __name__ == "__main__":
    raise SystemExit(main())
