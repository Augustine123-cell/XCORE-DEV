"""Tiny console logger matching the original bot's output style."""
import sys
import time

for _s in (sys.stdout, sys.stderr):  # symbols like ✓ ⚠ must not crash on Windows consoles
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _ts() -> str:
    return time.strftime("%H:%M:%S", time.gmtime())


def _out(sym: str, args, err: bool = False) -> None:
    print(f"[{_ts()}] {sym}", *args, file=sys.stderr if err else sys.stdout, flush=True)


def info(*a): _out("▸", a)
def ok(*a): _out("✓", a)
def warn(*a): _out("⚠", a, True)
def error(*a): _out("✗", a, True)
def dim(*a): _out(" ", a)
