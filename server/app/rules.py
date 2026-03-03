import math

from .models import Listing


def true_price_for_mode(listing: Listing, mode: str) -> float:
    if mode == "sqm":
        if not listing.surface or listing.surface <= 0:
            raise ValueError("Surface invalide pour le mode €/m²")
        return listing.price / listing.surface
    return listing.price


def compute_round_score(guess: float, true_price: float, hint_penalty: int) -> dict[str, float | int]:
    if true_price <= 0:
        return {"errorPct": 1.0, "baseScore": 0, "finalScore": 0}

    error_pct = abs(guess - true_price) / true_price
    base_score = max(0, round(1000 * math.exp(-3 * error_pct)))
    final_score = max(0, base_score - hint_penalty)
    return {
        "errorPct": error_pct,
        "baseScore": base_score,
        "finalScore": final_score,
    }
