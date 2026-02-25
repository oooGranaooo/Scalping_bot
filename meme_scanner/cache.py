import time
from config import NOTIFY_TTL


class NotificationCache:
    def __init__(self, ttl: int = NOTIFY_TTL):
        self._store: dict[str, float] = {}
        self.ttl = ttl

    def is_recent(self, key: str) -> bool:
        ts = self._store.get(key)
        return ts is not None and (time.time() - ts) < self.ttl

    def mark(self, key: str):
        self._store[key] = time.time()
