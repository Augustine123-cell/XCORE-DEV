from urllib.parse import quote

import httpx

from .. import config


async def run_actor(actor_id: str, payload: dict | None = None) -> dict:
    cfg = config.get()
    if not cfg.osint.apify_token:
        raise RuntimeError("APIFY_TOKEN is not configured.")
    if not actor_id:
        raise ValueError("Apify actor ID is required.")
    url = f"https://api.apify.com/v2/acts/{quote(actor_id, safe='')}/runs"
    async with httpx.AsyncClient(timeout=cfg.api.timeout) as http:
        r = await http.post(url, params={"token": cfg.osint.apify_token}, json=payload or {})
        r.raise_for_status()
        return r.json()
