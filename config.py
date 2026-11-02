"""Environment configuration. Call get() — it raises ConfigError if required vars are missing."""
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from . import paths


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Owner:
    number: str
    name: str
    numbers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Bot:
    name: str
    mode: str


@dataclass(frozen=True)
class Telegram:
    token: str
    admin_id: int
    owner_ids: tuple[int, ...] = ()
    force_join_urls: tuple[str, ...] = ()
    premium_30d_stars: int = 0
    premium_365d_stars: int = 0


@dataclass(frozen=True)
class Api:
    url: str
    key: str
    header: str
    prefix: str
    method: str
    phone_field: str
    timeout: float  # seconds


@dataclass(frozen=True)
class Osint:
    apify_token: str
    apify_user_id: str
    apify_actors: tuple[tuple[str, str], ...]
    rapidapi_key: str
    age_api_url: str


@dataclass(frozen=True)
class Paths:
    data: Path
    sessions: Path
    db_file: Path


@dataclass(frozen=True)
class Config:
    owner: Owner
    bot: Bot
    telegram: Telegram
    api: Api
    osint: Osint
    prefix: str
    prefixes: tuple[str, ...]
    paths: Paths
    port: int


def _req(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v or v == "PASTE_HERE":
        raise ConfigError(f"Missing env: {name}")
    return v


def _opt(name: str, default: str) -> str:
    v = os.environ.get(name)
    return default if v is None or not str(v).strip() else str(v).strip()


def _num(name: str, default: int) -> int:
    try:
        return int(_opt(name, str(default)))
    except (ValueError, OverflowError) as exc:
        raise ConfigError(f"{name} must be a valid integer") from exc


def _build() -> Config:
    if not (os.environ.get("XCORE_NO_DOTENV") or os.environ.get("SHADOWBORNE_NO_DOTENV")):
        load_dotenv()
    data, sessions = paths.data_dir(), paths.session_dir()
    primary_owner = re.sub(r"\D", "", _opt("OWNER_NUMBER", "2348130300032"))
    owner_numbers = []
    for raw in _opt("OWNER_NUMBERS", primary_owner).split(","):
        number = re.sub(r"\D", "", raw)
        if number and number not in owner_numbers:
            owner_numbers.append(number)
    if primary_owner and primary_owner not in owner_numbers:
        owner_numbers.insert(0, primary_owner)
    if len(owner_numbers) > 2:
        raise ConfigError("OWNER_NUMBERS supports at most two WhatsApp owner numbers")
    if not owner_numbers or any(not 7 <= len(number) <= 15 for number in owner_numbers):
        raise ConfigError("OWNER_NUMBER/OWNER_NUMBERS must contain valid international phone numbers (7–15 digits)")
    admin_id = _num("TELEGRAM_ADMIN_ID", 7895316756)
    owner_ids = []
    for raw in _opt("OWNER_TELEGRAM_IDS", str(admin_id)).split(","):
        try:
            value = int(raw.strip())
        except ValueError as exc:
            raise ConfigError("OWNER_TELEGRAM_IDS must be comma-separated Telegram numeric IDs") from exc
        if value not in owner_ids:
            owner_ids.append(value)
    if len(owner_ids) > 2:
        raise ConfigError("OWNER_TELEGRAM_IDS supports at most two Telegram owner IDs")
    force_join_urls = tuple(x.strip() for x in (
        _opt("FORCE_JOIN_URL_1", ""), _opt("FORCE_JOIN_URL_2", ""), _opt("FORCE_JOIN_URL_3", "")
    ) if x.strip())
    method = _opt("BAN_CHECK_HTTP_METHOD", "POST").upper()
    if method not in {"GET", "POST", "PUT", "PATCH"}:
        raise ConfigError("BAN_CHECK_HTTP_METHOD must be GET, POST, PUT, or PATCH")
    timeout = _num("REQUEST_TIMEOUT_MS", 20000) / 1000
    if timeout <= 0:
        raise ConfigError("REQUEST_TIMEOUT_MS must be greater than zero")
    port = _num("PORT", 3000)
    if not 0 <= port <= 65535:
        raise ConfigError("PORT must be between 0 and 65535")
    prefixes = tuple(dict.fromkeys(x.strip() for x in _opt("COMMAND_PREFIXES", "®,@").split(",") if x.strip()))
    if not prefixes:
        prefixes = ("®", "@")
    return Config(
        owner=Owner(primary_owner, _opt("DEV_NAME", "𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽"), tuple(owner_numbers)),
        bot=Bot(_opt("BOT_NAME", "𝚇𝙲𝙾𝚁𝙴®"), _opt("BOT_MODE", "public")),
        telegram=Telegram(_req("TELEGRAM_BOT_TOKEN"), admin_id, tuple(owner_ids), force_join_urls,
                           max(0, _num("PREMIUM_30D_STARS", 0)), max(0, _num("PREMIUM_365D_STARS", 0))),
        api=Api(
            url=_opt("BAN_CHECKER_API_URL", "https://baron0.com/api/v2/check"),
            key=_req("BAN_CHECKER_API_KEY"),
            header=_opt("BAN_CHECK_API_KEY_HEADER", "Authorization"),
            prefix=_opt("BAN_CHECK_API_KEY_PREFIX", "Bearer"),
            method=method,
            phone_field=_opt("BAN_CHECK_PHONE_FIELD", "phone"),
            timeout=timeout,
        ),
        osint=Osint(
            _opt("APIFY_TOKEN", ""),
            _opt("APIFY_USER_ID", ""),
            tuple(
                (alias.strip().lower(), actor.strip())
                for item in _opt("APIFY_ACTORS", "groups=lofomachines~whatsapp-group-search").split(",")
                if "=" in item
                for alias, actor in [item.split("=", 1)]
                if alias.strip() and actor.strip()
            ),
            _opt("RAPIDAPI_KEY", ""),
            _opt("AGE_API_URL", ""),
        ),
        prefix=prefixes[0],
        prefixes=prefixes,
        paths=Paths(data, sessions, data / "db.json"),
        port=port,
    )


_cfg = None


def get() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = _build()
    return _cfg


def reset() -> None:
    """Drop the cached config (used by tests)."""
    global _cfg
    _cfg = None
