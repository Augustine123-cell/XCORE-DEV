import httpx

from .. import config


async def age(user_id) -> dict:
    cfg = config.get()
    if not cfg.osint.age_api_url:
        raise RuntimeError("AGE_API_URL is not configured.")
    async with httpx.AsyncClient(timeout=cfg.api.timeout) as http:
        r = await http.get(cfg.osint.age_api_url, params={"id": str(user_id)})
        r.raise_for_status()
        return r.json()
