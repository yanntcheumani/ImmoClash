from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


PRICE_MODES = {"total", "rent", "sqm"}
HINT_KEYS = {"surface", "rooms", "dpe"}

_TITLE_PRICE_PATTERNS = (
    re.compile(r"(?:€|euros?|\$|£)\s*[0-9][0-9\s.,]{0,16}", flags=re.IGNORECASE),
    re.compile(
        r"[0-9][0-9\s.,]{0,16}\s*(?:€|euros?|\$|£)(?:\s*(?:/ ?mois|mensuel|cc|charges comprises?))?",
        flags=re.IGNORECASE,
    ),
)


def _sanitize_title_for_round(title: str) -> str:
    # Retire les montants explicites du titre pour éviter les fuites de prix pendant la manche.
    cleaned = title or ""
    for pattern in _TITLE_PRICE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -|,:")
    cleaned = re.sub(r"\(\s*\)", "", cleaned).strip(" -|,:")
    return cleaned or "Annonce logement"


@dataclass
class Listing:
    id: str
    title: str
    type: str
    price: float
    currency: str
    city: str
    country: str
    address: str | None
    lat: float | None
    lng: float | None
    surface: float | None
    rooms: int | None
    dpe: str | None
    images: list[str]
    source_url: str | None

    def as_round_payload(self) -> dict[str, Any]:
        return {
            "listingId": self.id,
            "title": _sanitize_title_for_round(self.title),
            "city": self.city,
            "country": self.country,
            "address": self.address,
            "lat": self.lat,
            "lng": self.lng,
            "imageUrls": [f"/public/{img}" for img in self.images],
            "availableHints": {
                "surface": self.surface is not None,
                "rooms": self.rooms is not None,
                "dpe": bool(self.dpe),
            },
        }

    def hint_value(self, hint_key: str) -> Any:
        if hint_key == "surface":
            return self.surface
        if hint_key == "rooms":
            return self.rooms
        if hint_key == "dpe":
            return self.dpe
        return None


@dataclass
class RoomConfig:
    rounds_count: int
    timer_seconds: int
    price_mode: str
    hints_enabled: bool
    search_query: str


@dataclass
class Player:
    id: str
    nickname: str
    sid: str
    connected: bool = True
    score: int = 0


@dataclass
class Guess:
    value: float


@dataclass
class RoundState:
    index: int
    listing: Listing
    ends_at_ms: int
    guesses: dict[str, Guess] = field(default_factory=dict)
    hints_used: dict[str, set[str]] = field(default_factory=dict)
    locked: bool = False


@dataclass
class Room:
    code: str
    host_player_id: str
    config: RoomConfig
    players: dict[str, Player]
    phase: str = "lobby"  # lobby | in_round | reveal | finished
    round_index: int = 0
    listing_ids: list[str] = field(default_factory=list)
    used_listing_ids: set[str] = field(default_factory=set)
    current_round: RoundState | None = None
