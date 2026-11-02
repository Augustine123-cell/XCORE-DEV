import httpx

from .. import config


async def request(host: str, path: str, params: dict | None = None, method: str = "GET", data=None):
    cfg = config.get()
    if not cfg.osint.rapidapi_key:
        raise RuntimeError("RAPIDAPI_KEY is not configured.")
    if not host or not path:
        raise ValueError("RapidAPI host/path not configured.")
    headers = {"x-rapidapi-key": cfg.osint.rapidapi_key, "x-rapidapi-host": host}
    async with httpx.AsyncClient(timeout=cfg.api.timeout) as http:
        r = await http.request(
            method,
            f"https://{host}{path}",
            headers=headers,
            params=params if method == "GET" else None,
            json=data,
        )
        r.raise_for_status()
        return r.json()
