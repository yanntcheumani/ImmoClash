#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "server") not in sys.path:
    sys.path.append(str(ROOT / "server"))

from app.config import SETTINGS  # noqa: E402
from app.db import init_db, upsert_listing  # noqa: E402


def normalize_listing(raw: dict) -> dict:
    required = ["id", "title", "type", "price", "city", "country", "images"]
    missing = [key for key in required if key not in raw]
    if missing:
        raise ValueError(f"Listing invalide, champs manquants: {', '.join(missing)}")

    listing_id = str(raw["id"])
    images = []
    for image in raw.get("images", []):
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed SQLite depuis data/listings.json")
    parser.add_argument("--json", default=str(ROOT / "data" / "listings.json"), help="Chemin du JSON source")
    parser.add_argument("--db", default=str(SETTINGS.db_path), help="Chemin SQLite cible")
    args = parser.parse_args()

    json_path = Path(args.json)
    db_path = Path(args.db)

    if not json_path.exists():
        print(f"[seed] Fichier introuvable: {json_path}")
        return 1

    with json_path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)

    if not isinstance(data, list):
        print("[seed] listings.json doit contenir un tableau")
        return 1

    init_db(db_path)

    inserted = 0
    skipped = 0
    for raw in data:
        try:
            listing = normalize_listing(raw)
            upsert_listing(db_path, listing)
            inserted += 1
        except Exception as exc:
            skipped += 1
            print(f"[seed] skip {raw.get('id', '?')}: {exc}")

    print(f"[seed] OK inserted={inserted} skipped={skipped} db={db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
