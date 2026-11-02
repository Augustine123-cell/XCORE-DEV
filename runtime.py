"""Process-wide runtime facts."""
import time

START = time.time()


def uptime_seconds() -> int:
    return int(time.time() - START)
