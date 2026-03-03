from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin

import httpx
from bs4 import BeautifulSoup

from .config import SETTINGS
from .db import upsert_listing


_CITY_TO_SITE = {
    "paris": "paris",
    "lyon": "lyon",
    "marseille": "marseilles",
    "bordeaux": "bordeaux",
    "lille": "paris",
    "nantes": "paris",
    "toulouse": "paris",
}

_FR_FALLBACK_QUERIES = (
    "Marseille, France",
    "Nantes, France",
    "Lyon, France",
    "Bordeaux, France",
    "Lille, France",
    "Toulouse, France",
    "Rennes, France",
    "Montpellier, France",
    "Strasbourg, France",
    "Nice, France",
)

_APT_POSITIVE_HINTS = (
    "appartement",
    "apartment",
    "apt",
    "studio",
    "flat",
    "condo",
    "loft",
    "t1",
    "t2",
    "t3",
    "t4",
    "1br",
    "2br",
    "3br",
)

_APT_NEGATIVE_HINTS = (
    "maison",
    "house",
    "villa",
    "chambre",
    "roommate",
    "room for rent",
    "shared room",
    "colocation",
    "office",
    "bureau",
    "garage",
    "parking",
)

_geo_cache: dict[str, tuple[float | None, float | None]] = {}

_MIN_RENT_PRICE = 100.0
_MAX_RENT_PRICE = 30000.0


def _normalize_text(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip().lower())


def _parse_price(raw: str) -> float | None:
    text = (raw or "").strip()
    if not text:
        return None
    clean = text.replace("\xa0", " ")
    clean = re.sub(r"[^\d,.\s]", "", clean)
    clean = re.sub(r"\s+", "", clean)
    if not clean:
        return None

    cents_match = re.fullmatch(r"(.+?)[,.](\d{2})", clean)
    if cents_match:
        clean = cents_match.group(1)

    digits = re.sub(r"[^\d]", "", clean)
    if not digits:
        return None

    value = float(digits)
    if value <= 0:
        return None
    return value


def _is_plausible_rent_price(value: float | None) -> bool:
    if value is None:
        return False
    return _MIN_RENT_PRICE <= value <= _MAX_RENT_PRICE


def _extract_currency_prices(text: str) -> list[float]:
    values: list[float] = []
    if not text:
        return values

    patterns = (
        r"([0-9][0-9 \u00a0.,]{0,16})\s*(?:€|euros?)",
        r"(?:€|euros?)\s*([0-9][0-9 \u00a0.,]{0,16})",
        r"\$\s*([0-9][0-9 \u00a0.,]{0,16})",
        r"([0-9][0-9 \u00a0.,]{0,16})\s*\$",
    )

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            parsed = _parse_price(match.group(1))
            if parsed is None:
                continue
            values.append(parsed)

    return values


def _extract_monthly_rent_price(text: str) -> float | None:
    if not text:
        return None

    monthly_patterns = (
        r"loyer(?:[^0-9€$]{0,30})([0-9][0-9 \u00a0.,]{0,16})\s*(?:€|euros?)",
        r"([0-9][0-9 \u00a0.,]{0,16})\s*(?:€|euros?)\s*(?:/ ?mois|mensuel|cc|charges comprises?)",
        r"(?:€|euros?)\s*([0-9][0-9 \u00a0.,]{0,16})\s*(?:/ ?mois|mensuel|cc|charges comprises?)?",
        r"\$\s*([0-9][0-9 \u00a0.,]{0,16})\s*(?:/ ?mo|/ ?month|monthly)?",
        r"([0-9][0-9 \u00a0.,]{0,16})\s*\$\s*(?:/ ?mo|/ ?month|monthly)?",
    )

    for pattern in monthly_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            parsed = _parse_price(match.group(1))
            if _is_plausible_rent_price(parsed):
                return parsed

    for parsed in _extract_currency_prices(text):
        if _is_plausible_rent_price(parsed):
            return parsed

    return None


def _absolute_url(base: str, href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return f"{base}{href}"
    return f"{base}/{href}"


def _guess_country(search_query: str) -> str:
    lowered = _normalize_text(search_query)
    if "france" in lowered or any(city in lowered for city in _CITY_TO_SITE):
        return "FR"
    return "US"


def _currency_for_country(country_code: str) -> str:
    if country_code.upper() in {"FR", "BE", "ES", "DE", "IT", "PT", "NL", "LU"}:
        return "EUR"
    return "USD"


def _is_rental_apartment_text(text: str) -> bool:
    lowered = _normalize_text(text)
    if any(bad in lowered for bad in _APT_NEGATIVE_HINTS):
        return False
    if any(ok in lowered for ok in _APT_POSITIVE_HINTS):
        return True
    return False


def _extract_surface_rooms(text: str) -> tuple[float | None, int | None]:
    surface = None
    rooms = None

    surface_match = re.search(r"(\d{1,4}(?:[\.,]\d{1,2})?)\s?(?:m2|m²|sqm)", text, flags=re.IGNORECASE)
    if surface_match:
        try:
            surface = float(surface_match.group(1).replace(",", "."))
        except ValueError:
            surface = None

    rooms_match = re.search(r"(\d{1,2})\s?(?:pi[eè]ces?|rooms?|br)", text, flags=re.IGNORECASE)
    if rooms_match:
        try:
            rooms = int(rooms_match.group(1))
        except ValueError:
            rooms = None

    return surface, rooms


async def _download_image(
    client: httpx.AsyncClient,
    image_url: str,
    dest_dir: Path,
    index: int,
    referer: str | None = None,
) -> str | None:
    try:
        headers = {"Referer": referer} if referer else None
        response = await client.get(image_url, headers=headers)
    except httpx.HTTPError:
        return None

    if response.status_code >= 400 or not response.content:
        return None

    content_type = response.headers.get("content-type", "")
    extension = ".jpg"
    if "png" in content_type:
        extension = ".png"
    elif "webp" in content_type:
        extension = ".webp"

    filename = f"{index:02d}{extension}"
    file_path = dest_dir / filename
    file_path.write_bytes(response.content)
    return filename


async def _geocode_city(client: httpx.AsyncClient, city: str, country: str) -> tuple[float | None, float | None]:
    key = f"{city}|{country}".lower().strip()
    if key in _geo_cache:
        return _geo_cache[key]

    if not city:
        _geo_cache[key] = (None, None)
        return _geo_cache[key]

    try:
        response = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{city}, {country}", "format": "json", "limit": 1},
        )
    except httpx.HTTPError:
        _geo_cache[key] = (None, None)
        return _geo_cache[key]

    if response.status_code >= 400:
        _geo_cache[key] = (None, None)
        return _geo_cache[key]

    try:
        items = response.json()
    except ValueError:
        items = []

    if isinstance(items, list) and items:
        first = items[0]
        try:
            lat = float(first.get("lat"))
            lng = float(first.get("lon"))
            _geo_cache[key] = (lat, lng)
            return _geo_cache[key]
        except (TypeError, ValueError):
            pass

    _geo_cache[key] = (None, None)
    return _geo_cache[key]


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        clean = url.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _is_likely_image_url(url: str) -> bool:
    return bool(re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", url, flags=re.IGNORECASE))


def _normalize_scraped_url(raw_url: str, base_url: str) -> str:
    clean = (raw_url or "").strip()
    if not clean:
        return ""
    if clean.startswith("//"):
        return f"https:{clean}"
    return urljoin(base_url, clean)


def _best_srcset_url(srcset: str) -> str | None:
    if not srcset:
        return None

    best_url: str | None = None
    best_score = -1

    for item in srcset.split(","):
        chunk = item.strip()
        if not chunk:
            continue

        parts = chunk.split()
        url = parts[0].strip()
        if not url:
            continue

        score = 0
        if len(parts) >= 2:
            descriptor = parts[1].strip().lower()
            if descriptor.endswith("w"):
                try:
                    score = int(descriptor[:-1])
                except ValueError:
                    score = 0
            elif descriptor.endswith("x"):
                try:
                    score = int(float(descriptor[:-1]) * 1000)
                except ValueError:
                    score = 0

        if score > best_score:
            best_url = url
            best_score = score

    return best_url


def _pick_craigslist_site(search_query: str) -> str:
    lowered = _normalize_text(search_query)
    for city, site in _CITY_TO_SITE.items():
        if city in lowered:
            return site
    return "paris"


def _extract_json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        text = script.get_text(strip=True)
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            payloads.append(parsed)
        elif isinstance(parsed, list):
            payloads.extend([item for item in parsed if isinstance(item, dict)])
    return payloads


def _extract_images_from_ld(ld_items: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for item in ld_items:
        image = item.get("image")
        if isinstance(image, str):
            urls.append(image)
        elif isinstance(image, list):
            urls.extend([img for img in image if isinstance(img, str)])
    return _dedupe_urls(urls)


def _extract_lat_lng_from_craigslist(soup: BeautifulSoup, ld_items: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    map_tag = soup.select_one("#map")
    if map_tag:
        lat_raw = map_tag.get("data-latitude")
        lng_raw = map_tag.get("data-longitude")
        try:
            lat = float(lat_raw) if lat_raw is not None else None
            lng = float(lng_raw) if lng_raw is not None else None
            if lat is not None and lng is not None:
                return lat, lng
        except (TypeError, ValueError):
            pass

    for item in ld_items:
        geo = item.get("geo")
        if isinstance(geo, dict):
            try:
                return float(geo.get("latitude")), float(geo.get("longitude"))
            except (TypeError, ValueError):
                continue

    return None, None


def _craigslist_query_variants(search_query: str) -> list[str]:
    city_only = search_query.split(",")[0].strip()
    variants: list[str] = []
    candidates = [
        search_query.strip(),
        city_only,
        f"{city_only} apartment".strip(),
        f"{city_only} appartement".strip(),
        "",
    ]
    for candidate in candidates:
        clean = candidate.strip()
        if clean in variants:
            continue
        variants.append(clean)
    return variants


async def _scrape_craigslist_candidates(
    client: httpx.AsyncClient,
    search_query: str,
    needed: int,
) -> list[dict[str, Any]]:
    site = _pick_craigslist_site(search_query)
    base = f"https://{site}.craigslist.org"
    category = "apa"  # apartment/housing rentals

    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    query_variants = [query for query in _craigslist_query_variants(search_query) if query]

    for query in query_variants:
        for page_idx in range(SETTINGS.scrape_max_candidate_pages):
            offset = page_idx * 120
            url = (
                f"{base}/search/{category}?query={quote_plus(query)}"
                f"&sort=date&hasPic=1&s={offset}"
            )

            try:
                response = await client.get(url)
            except httpx.HTTPError:
                continue

            if response.status_code >= 400:
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            rows = soup.select("li.cl-search-result")
            if not rows:
                rows = soup.select("li.result-row")
            if not rows:
                rows = soup.select("div.cl-search-result")

            for row in rows:
                link_tag = row.select_one("a.posting-title[href]") or row.select_one("a[href]")
                if not link_tag:
                    continue
                href = str(link_tag.get("href") or "").strip()
                if not href:
                    continue

                listing_url = _absolute_url(base, href)
                if listing_url in seen_urls:
                    continue
                seen_urls.add(listing_url)

                title = link_tag.get_text(" ", strip=True) or "Annonce logement"

                price_tag = row.select_one(".price") or row.select_one(".result-price") or row.select_one(".cl-price")
                price = _parse_price(price_tag.get_text(" ", strip=True) if price_tag else "")

                candidates.append(
                    {
                        "source_url": listing_url,
                        "title": title,
                        "price_hint": price,
                    }
                )

                if len(candidates) >= needed:
                    return candidates

    return candidates


async def _scrape_craigslist_candidates_rss(
    client: httpx.AsyncClient,
    search_query: str,
    needed: int,
) -> list[dict[str, Any]]:
    site = _pick_craigslist_site(search_query)
    base = f"https://{site}.craigslist.org"
    category = "apa"  # apartment/housing rentals

    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for query in _craigslist_query_variants(search_query):
        params = ["sort=date", "hasPic=1", "format=rss"]
        if query:
            params.append(f"query={quote_plus(query)}")
        url = f"{base}/search/{category}?{'&'.join(params)}"

        try:
            response = await client.get(url)
        except httpx.HTTPError:
            continue

        if response.status_code >= 400:
            continue

        rss = BeautifulSoup(response.text, "xml")
        items = rss.select("item")
        for item in items:
            link_tag = item.find("link")
            title_tag = item.find("title")
            desc_tag = item.find("description")

            listing_url = _normalize_scraped_url(link_tag.get_text(strip=True) if link_tag else "", base)
            if not listing_url or listing_url in seen_urls:
                continue
            seen_urls.add(listing_url)

            title = title_tag.get_text(" ", strip=True) if title_tag else "Annonce logement"
            desc = desc_tag.get_text(" ", strip=True) if desc_tag else ""
            price = _extract_monthly_rent_price(title) or _extract_monthly_rent_price(desc)

            candidates.append(
                {
                    "source_url": listing_url,
                    "title": title,
                    "price_hint": price,
                }
            )
            if len(candidates) >= needed:
                return candidates

    return candidates


async def _hydrate_craigslist_listing(
    client: httpx.AsyncClient,
    candidate: dict[str, Any],
    search_query: str,
    public_dir: Path,
) -> dict[str, Any] | None:
    source_url = str(candidate["source_url"])
    try:
        response = await client.get(source_url)
    except httpx.HTTPError:
        return None

    if response.status_code >= 400:
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    full_text = soup.get_text(" ", strip=True)

    title = (
        soup.select_one("#titletextonly").get_text(" ", strip=True)
        if soup.select_one("#titletextonly")
        else str(candidate.get("title") or "Annonce logement")
    )

    lowered_sample = _normalize_text(f"{title} {full_text[:800]}")
    if any(bad in lowered_sample for bad in _APT_NEGATIVE_HINTS):
        return None

    candidate_price = candidate.get("price_hint")
    price = float(candidate_price) if isinstance(candidate_price, (int, float)) else None
    if not _is_plausible_rent_price(price):
        price = _extract_monthly_rent_price(title) or _extract_monthly_rent_price(full_text)
    if not _is_plausible_rent_price(price):
        return None

    city = search_query.split(",")[0].strip() or "Unknown"
    country = _guess_country(search_query)

    ld_items = _extract_json_ld(soup)
    lat, lng = _extract_lat_lng_from_craigslist(soup, ld_items)

    if lat is None or lng is None:
        geo_lat, geo_lng = await _geocode_city(client, city, country)
        lat = lat if lat is not None else geo_lat
        lng = lng if lng is not None else geo_lng

    surface, rooms = _extract_surface_rooms(full_text)

    image_urls = _extract_images_from_ld(ld_items)
    if not image_urls:
        image_urls = re.findall(
            r"https://images\.craigslist\.org/[A-Za-z0-9_%-]+(?:_[0-9]{3,4}x[0-9]{3,4})?\.jpg",
            response.text,
        )

    image_urls = _dedupe_urls(image_urls)[:5]

    listing_id = f"web-cl-{hashlib.sha1(source_url.encode('utf-8')).hexdigest()[:12]}"
    listing_dir = public_dir / "listings" / listing_id
    listing_dir.mkdir(parents=True, exist_ok=True)

    downloads: list[str | None] = []
    if image_urls:
        downloads = await asyncio.gather(
            *[
                _download_image(
                    client,
                    _normalize_scraped_url(image_url, source_url),
                    listing_dir,
                    idx + 1,
                    referer=source_url,
                )
                for idx, image_url in enumerate(image_urls)
            ]
        )
    images = [f"listings/{listing_id}/{filename}" for filename in downloads if filename]

    return {
        "id": listing_id,
        "title": title,
        "type": "rent",
        "price": float(price),
        "currency": _currency_for_country(country),
        "city": city,
        "country": country,
        "address": city,
        "lat": lat,
        "lng": lng,
        "surface": surface,
        "rooms": rooms,
        "dpe": None,
        "images": images,
        "source_url": source_url,
    }


def _extract_pap_detail_urls(soup: BeautifulSoup) -> list[str]:
    urls: list[str] = []
    base = "https://www.pap.fr"
    for link in soup.select("a[href]"):
        href = str(link.get("href") or "")
        lowered = href.lower()
        if "/annonces/" not in lowered:
            continue
        if "-r" not in lowered:
            continue
        if "appartement" not in lowered and "location" not in lowered:
            continue
        urls.append(_normalize_scraped_url(href.split("?")[0], base))
    raw_html = str(soup)
    for href in re.findall(r"/annonces/[^\"' ]+-r\d+", raw_html, flags=re.IGNORECASE):
        lower_href = href.lower()
        if "appartement" not in lower_href and "location" not in lower_href:
            continue
        urls.append(_normalize_scraped_url(href.split("?")[0], base))
    return _dedupe_urls(urls)


def _extract_pap_city_pages(soup: BeautifulSoup, search_query: str) -> list[str]:
    query = _normalize_text(search_query)
    pages: list[str] = []
    base = "https://www.pap.fr"

    for link in soup.select("a[href]"):
        href = str(link.get("href") or "")
        lowered = href.lower()
        if "/annonce/locations-appartement-" not in lowered and "/annonces/location-appartement-" not in lowered:
            continue
        if "-g" not in lowered:
            continue
        full = _normalize_scraped_url(href.split("?")[0], base)
        pages.append(full)

    pages = _dedupe_urls(pages)
    if not pages:
        return []

    if not query:
        return pages[:1]

    tokens = [token for token in re.split(r"[,\s]+", query) if len(token) >= 3]
    if not tokens:
        return pages[:1]

    matched = [page for page in pages if any(token in _normalize_text(page) for token in tokens)]
    if matched:
        return matched[:2]

    return pages[:1]


async def _hydrate_pap_listing(
    client: httpx.AsyncClient,
    detail_url: str,
    search_query: str,
    public_dir: Path,
) -> dict[str, Any] | None:
    try:
        response = await client.get(detail_url)
    except httpx.HTTPError:
        return None

    if response.status_code >= 400:
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    page_text = soup.get_text(" ", strip=True)

    h1 = soup.select_one("h1")
    og_title = soup.select_one('meta[property="og:title"]')
    title = (
        h1.get_text(" ", strip=True)
        if h1
        else (str(og_title.get("content") or "").strip() if og_title else "Annonce location")
    )
    if not _is_rental_apartment_text(title):
        # URLs PAP ici sont déjà appartement, on garde un garde-fou anti colocation/chambre.
        if any(bad in _normalize_text(title) for bad in ("colocation", "chambre")):
            return None

    if any(bad in _normalize_text(page_text[:400]) for bad in ("chambre en colocation", "roommate")):
        return None

    price = _extract_monthly_rent_price(title)
    if not _is_plausible_rent_price(price):
        meta_price = soup.select_one('meta[property="product:price:amount"], meta[itemprop="price"]')
        if meta_price:
            price = _parse_price(str(meta_price.get("content") or ""))
    if not _is_plausible_rent_price(price):
        price = _extract_monthly_rent_price(page_text)
    if not _is_plausible_rent_price(price):
        return None

    city = search_query.split(",")[0].strip() or "France"
    country = "FR"

    city_match = re.search(r"([A-Za-zÀ-ÿ'\- ]+)\s*\((\d{2,5})\)", title)
    if city_match:
        city = city_match.group(1).strip()
        postal = city_match.group(2).strip()
        address = f"{city} ({postal})"
    else:
        address = city

    surface, rooms = _extract_surface_rooms(page_text)

    image_urls: list[str] = []
    domain_hints = ("pap.fr", "pap", "cdn")

    for link in soup.select("a[href]"):
        href = str(link.get("href") or "").strip()
        if not href:
            continue
        if _is_likely_image_url(href) and any(hint in href.lower() for hint in domain_hints):
            image_urls.append(_normalize_scraped_url(href, detail_url))

    for tag in soup.select("img, source"):
        for attr in ("src", "data-src", "data-lazy-src"):
            value = str(tag.get(attr) or "").strip()
            if not value:
                continue
            normalized = _normalize_scraped_url(value, detail_url)
            if _is_likely_image_url(normalized) and any(hint in normalized.lower() for hint in domain_hints):
                image_urls.append(normalized)
        for attr in ("srcset", "data-srcset"):
            srcset = str(tag.get(attr) or "").strip()
            if not srcset:
                continue
            best = _best_srcset_url(srcset)
            if not best:
                continue
            normalized = _normalize_scraped_url(best, detail_url)
            if _is_likely_image_url(normalized) and any(hint in normalized.lower() for hint in domain_hints):
                image_urls.append(normalized)

    for found in re.findall(r"https?://[^\"' >]+", response.text):
        if not _is_likely_image_url(found):
            continue
        if any(hint in found.lower() for hint in domain_hints):
            image_urls.append(found)

    image_urls = _dedupe_urls(image_urls)[:8]

    lat, lng = await _geocode_city(client, city, country)

    listing_id = f"web-pap-{hashlib.sha1(detail_url.encode('utf-8')).hexdigest()[:12]}"
    listing_dir = public_dir / "listings" / listing_id
    listing_dir.mkdir(parents=True, exist_ok=True)

    downloads: list[str | None] = []
    if image_urls:
        downloads = await asyncio.gather(
            *[
                _download_image(
                    client,
                    image_url,
                    listing_dir,
                    idx + 1,
                    referer=detail_url,
                )
                for idx, image_url in enumerate(image_urls)
            ]
        )
    images = [f"listings/{listing_id}/{filename}" for filename in downloads if filename]

    return {
        "id": listing_id,
        "title": title,
        "type": "rent",
        "price": float(price),
        "currency": "EUR",
        "city": city,
        "country": country,
        "address": address,
        "lat": lat,
        "lng": lng,
        "surface": surface,
        "rooms": rooms,
        "dpe": None,
        "images": images,
        "source_url": detail_url,
    }


async def _scrape_pap_rental_apartments(
    client: httpx.AsyncClient,
    search_query: str,
    needed: int,
    public_dir: Path,
) -> list[dict[str, Any]]:
    base_url = "https://www.pap.fr/annonce/locations-appartement"
    pages = [base_url]

    try:
        base_response = await client.get(base_url)
    except httpx.HTTPError:
        return []

    if base_response.status_code < 400:
        base_soup = BeautifulSoup(base_response.text, "html.parser")
        pages.extend(_extract_pap_city_pages(base_soup, search_query))

    pages = _dedupe_urls(pages)

    detail_urls: list[str] = []
    for page_url in pages:
        try:
            response = await client.get(page_url)
        except httpx.HTTPError:
            continue
        if response.status_code >= 400:
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        detail_urls.extend(_extract_pap_detail_urls(soup))

        if len(detail_urls) >= needed * 3:
            break

    detail_urls = _dedupe_urls(detail_urls)[: max(needed * 4, 30)]
    if not detail_urls:
        return []

    semaphore = asyncio.Semaphore(5)

    async def _worker(url: str) -> dict[str, Any] | None:
        async with semaphore:
            return await _hydrate_pap_listing(client, url, search_query, public_dir)

    hydrated = await asyncio.gather(*[_worker(url) for url in detail_urls])

    listings = [item for item in hydrated if item]
    return listings[:needed]


async def _scrape_craigslist_rental_apartments(
    client: httpx.AsyncClient,
    search_query: str,
    needed: int,
    public_dir: Path,
) -> list[dict[str, Any]]:
    candidates = await _scrape_craigslist_candidates(client, search_query, needed=max(needed * 2, 15))
    if not candidates:
        return []

    semaphore = asyncio.Semaphore(6)

    async def _worker(candidate: dict[str, Any]) -> dict[str, Any] | None:
        async with semaphore:
            return await _hydrate_craigslist_listing(client, candidate, search_query, public_dir)

    hydrated = await asyncio.gather(*[_worker(candidate) for candidate in candidates])
    listings = [item for item in hydrated if item]
    return listings[:needed]


async def _scrape_craigslist_rental_apartments_rss(
    client: httpx.AsyncClient,
    search_query: str,
    needed: int,
    public_dir: Path,
) -> list[dict[str, Any]]:
    candidates = await _scrape_craigslist_candidates_rss(client, search_query, needed=max(needed * 2, 20))
    if not candidates:
        return []

    semaphore = asyncio.Semaphore(6)

    async def _worker(candidate: dict[str, Any]) -> dict[str, Any] | None:
        async with semaphore:
            return await _hydrate_craigslist_listing(client, candidate, search_query, public_dir)

    hydrated = await asyncio.gather(*[_worker(candidate) for candidate in candidates])
    listings = [item for item in hydrated if item]
    return listings[:needed]


def _providers_order_for_query(search_query: str) -> tuple[str, ...]:
    country = _guess_country(search_query)
    if country == "FR":
        return SETTINGS.scrape_providers_fr
    return SETTINGS.scrape_providers_other


def _search_query_variants(search_query: str) -> list[str]:
    base = (search_query or "").strip()
    if not base:
        return ["France"]

    variants = [base]
    if _guess_country(base) == "FR":
        for city_query in _FR_FALLBACK_QUERIES:
            if _normalize_text(city_query) == _normalize_text(base):
                continue
            variants.append(city_query)
    return variants


async def scrape_live_listings(
    search_query: str,
    rounds_count: int,
    public_dir: Path,
) -> dict[str, Any]:
    target_count = max(rounds_count * SETTINGS.scrape_candidate_multiplier, rounds_count + 2)
    provider_order = _providers_order_for_query(search_query)
    search_variants = _search_query_variants(search_query)

    timeout = httpx.Timeout(SETTINGS.scrape_timeout_seconds)
    headers = {
        "User-Agent": SETTINGS.scrape_user_agent,
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }

    collected: list[dict[str, Any]] = []
    seen_source: set[str] = set()
    used_providers: list[str] = []
    provider_stats: list[dict[str, Any]] = []
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        for query in search_variants:
            remaining = target_count - len(collected)
            if remaining <= 0:
                break
            for provider in provider_order:
                remaining = target_count - len(collected)
                if remaining <= 0:
                    break

                if provider not in used_providers:
                    used_providers.append(provider)
                needed = max(remaining, rounds_count)
                stat: dict[str, Any] = {
                    "provider": provider,
                    "query": query,
                    "requested": needed,
                    "fetched": 0,
                    "accepted": 0,
                    "error": None,
                }

                try:
                    if provider == "pap":
                        batch = await _scrape_pap_rental_apartments(
                            client=client,
                            search_query=query,
                            needed=needed,
                            public_dir=public_dir,
                        )
                    elif provider == "craigslist_rss":
                        batch = await _scrape_craigslist_rental_apartments_rss(
                            client=client,
                            search_query=query,
                            needed=needed,
                            public_dir=public_dir,
                        )
                    elif provider == "craigslist":
                        batch = await _scrape_craigslist_rental_apartments(
                            client=client,
                            search_query=query,
                            needed=needed,
                            public_dir=public_dir,
                        )
                    else:
                        batch = []
                        stat["error"] = "provider_not_supported"
                except Exception as exc:
                    batch = []
                    err_text = f"{provider}:{type(exc).__name__}:{exc}"
                    errors.append(err_text[:400])
                    stat["error"] = err_text[:200]

                stat["fetched"] = len(batch)
                accepted = 0
                for listing in batch:
                    source_url = str(listing.get("source_url") or "")
                    if source_url and source_url in seen_source:
                        continue
                    if source_url:
                        seen_source.add(source_url)
                    collected.append(listing)
                    accepted += 1
                stat["accepted"] = accepted
                provider_stats.append(stat)

    return {
        "listings": collected,
        "providersTried": used_providers,
        "providerStats": provider_stats,
        "errors": errors,
        "targetCount": target_count,
    }


async def scrape_and_store_live_listings(
    db_path: Path,
    public_dir: Path,
    search_query: str,
    rounds_count: int,
    price_mode: str,
) -> dict[str, Any]:
    _ = price_mode  # les annonces scrapees sont forcees en location appartement.

    scrape_debug = await scrape_live_listings(
        search_query=search_query,
        rounds_count=rounds_count,
        public_dir=public_dir,
    )
    listings = list(scrape_debug["listings"])
    providers = list(scrape_debug["providersTried"])

    inserted = 0
    listing_ids: list[str] = []
    for listing in listings:
        upsert_listing(db_path, listing)
        inserted += 1
        listing_ids.append(str(listing["id"]))

    return {
        "inserted": inserted,
        "listingIds": listing_ids,
        "source": ",".join(providers),
        "query": search_query,
        "providersTried": providers,
        "providerStats": scrape_debug.get("providerStats", []),
        "errors": scrape_debug.get("errors", []),
        "targetCount": int(scrape_debug.get("targetCount", 0)),
        "fetchedCount": len(listings),
    }


async def run_scrape_job(
    db_path: Path,
    public_dir: Path,
    search_query: str,
    rounds_count: int,
    price_mode: str,
) -> dict[str, Any]:
    result = await scrape_and_store_live_listings(
        db_path=db_path,
        public_dir=public_dir,
        search_query=search_query,
        rounds_count=rounds_count,
        price_mode=price_mode,
    )

    return {
        "inserted": result["inserted"],
        "updated": 0,
        "skipped": 0,
        "source": result["source"],
        "query": result["query"],
        "providersTried": result.get("providersTried", []),
        "providerStats": result.get("providerStats", []),
        "errors": result.get("errors", []),
        "targetCount": int(result.get("targetCount", 0)),
        "fetchedCount": int(result.get("fetchedCount", 0)),
        "message": "Scraping live terminé (appartements en location).",
    }
