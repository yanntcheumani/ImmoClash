import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    db_path: Path
    public_dir: Path
    data_dir: Path
    inter_round_delay_seconds: int = 5
    hint_penalty: int = 120
    default_search_query: str = "Paris, France"
    scrape_timeout_seconds: float = 10.0
    scrape_candidate_multiplier: int = 3
    scrape_max_candidate_pages: int = 2
    scrape_providers_fr: tuple[str, ...] = ("pap", "craigslist_rss", "craigslist")
    scrape_providers_other: tuple[str, ...] = ("craigslist_rss", "craigslist", "pap")
    cors_origins: tuple[str, ...] = ("*",)
    scrape_user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name, "").strip()
    return raw or default


def _env_origins(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    if raw == "*":
        return ("*",)
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or default


DATA_DIR = _env_path("IMMOCLASH_DATA_DIR", REPO_ROOT / "data")
DB_PATH = _env_path("IMMOCLASH_DB_PATH", DATA_DIR / "immo_clash.db")
PUBLIC_DIR = _env_path("IMMOCLASH_PUBLIC_DIR", REPO_ROOT / "public")
DEFAULT_SEARCH_QUERY = _env_str("IMMOCLASH_DEFAULT_SEARCH_QUERY", "Paris, France")
CORS_ORIGINS = _env_origins("IMMOCLASH_CORS_ORIGINS", ("*",))

SETTINGS = Settings(
    repo_root=REPO_ROOT,
    db_path=DB_PATH,
    public_dir=PUBLIC_DIR,
    data_dir=DATA_DIR,
    default_search_query=DEFAULT_SEARCH_QUERY,
    cors_origins=CORS_ORIGINS,
)
