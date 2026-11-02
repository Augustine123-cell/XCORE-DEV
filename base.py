"""Shared test fixtures. Uses the real httpx if installed, otherwise a tiny in-memory stand-in."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HTTP_QUEUE: list = []  # responses (or exceptions) consumed by the stub client
HTTP_CALLS: list = []

try:
    import httpx as _real_httpx
except ImportError:
    _real_httpx = None

stub = types.ModuleType("httpx")
if _real_httpx is not None:
    # Preserve exception/type attributes but replace the network client and response constructor.
    stub.__dict__.update(_real_httpx.__dict__)

class _Resp:
    def __init__(self, status=200, body=None, headers=None, reason="OK", content=None, **kwargs):
        if "status_code" in kwargs:
            status = kwargs["status_code"]
        self.status_code, self.reason_phrase = status, reason
        self.headers = headers or {}
        self._body = body
        self.content = content if content is not None else json.dumps(body if body is not None else {}).encode()

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

class _Client:
    def __init__(self, *a, **kw):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def request(self, method, url, **kw):
        HTTP_CALLS.append((method, url, kw))
        if not HTTP_QUEUE:
            raise RuntimeError("No mocked HTTP response queued")
        item = HTTP_QUEUE.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    async def get(self, url, **kw):
        return await self.request("GET", url, **kw)
    async def post(self, url, **kw):
        return await self.request("POST", url, **kw)

if not hasattr(stub, "HTTPError"):
    class HTTPError(Exception):
        pass
    stub.HTTPError = HTTPError
if not hasattr(stub, "TimeoutException"):
    class TimeoutException(HTTPError):
        pass
    stub.TimeoutException = TimeoutException
if not hasattr(stub, "Timeout"):
    class Timeout:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs
    stub.Timeout = Timeout

stub.Response, stub.AsyncClient, stub._Resp = _Resp, _Client, _Resp
sys.modules["httpx"] = stub

def make_response(*a, **kw):
    return _Resp(*a, **kw)


# Every variable the app reads. Cleared before each test so a developer's real shell/.env/Railway
# settings (e.g. FORCE_JOIN_URL_1, OWNER_NUMBERS, COMMAND_PREFIX, RAILWAY_*) can never leak in.
ISOLATED_ENV = (
    "AGE_API_URL APIFY_TOKEN BAN_CHECKER_API_KEY BAN_CHECKER_API_URL BAN_CHECK_API_KEY_HEADER "
    "BAN_CHECK_API_KEY_PREFIX BAN_CHECK_HTTP_METHOD BAN_CHECK_PHONE_FIELD BOT_MODE BOT_NAME COMMAND_PREFIX COMMAND_PREFIXES "
    "DEV_NAME FORCE_JOIN_URL_1 FORCE_JOIN_URL_2 FORCE_JOIN_URL_3 OWNER_NUMBER OWNER_NUMBERS "
    "OWNER_TELEGRAM_IDS PORT RAPIDAPI_KEY REQUEST_TIMEOUT_MS TELEGRAM_ADMIN_ID TELEGRAM_BOT_TOKEN "
    "DATA_DIR SESSION_DIR PERSIST_DIR PREMIUM_30D_STARS PREMIUM_365D_STARS"
).split()


class Base(unittest.IsolatedAsyncioTestCase):
    OWNER = "2348130300032"

    def setUp(self):
        env_patch = mock.patch.dict(os.environ)  # restores the real environment after each test
        env_patch.start()
        self.addCleanup(env_patch.stop)
        saved = {k: v for k, v in sys.modules.items() if k == "neonize" or k.startswith("neonize.")}
        self.addCleanup(self._restore_neonize, saved)  # fake neonize must not outlive the test
        for k in list(os.environ):
            if k in ISOLATED_ENV or k.startswith("RAILWAY_"):
                del os.environ[k]
        self.tmp = Path(tempfile.mkdtemp(prefix="sbtest-"))
        os.environ.update(
            XCORE_NO_DOTENV="1",
            TELEGRAM_BOT_TOKEN="123:abc",
            BAN_CHECKER_API_KEY="k",
            DATA_DIR=str(self.tmp / "data"),
            SESSION_DIR=str(self.tmp / "sessions"),
            OWNER_NUMBER=self.OWNER,
            BOT_MODE="public",
            PORT="0",
        )
        from xcore import clans, config, db

        config.reset()
        db.reset_cache()
        clans._cache = None
        HTTP_QUEUE.clear()
        HTTP_CALLS.clear()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    @staticmethod
    def _restore_neonize(saved):
        for k in [k for k in sys.modules if k == "neonize" or k.startswith("neonize.")]:
            del sys.modules[k]
        sys.modules.update(saved)
