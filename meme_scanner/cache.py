import logging
import os
import time

import pandas as pd

from config import NOTIFY_TTL

logger = logging.getLogger(__name__)

_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signal_log.csv")


class NotificationCache:
    def __init__(self, ttl: int = NOTIFY_TTL):
        self._store: dict[str, float] = {}
        self.ttl = ttl
        self._restore_from_log()

    def _restore_from_log(self):
        """Bot再起動時に signal_log.csv からTTL内の通知済みトークンを復元する。"""
        if not os.path.exists(_LOG_FILE):
            return
        try:
            df = pd.read_csv(_LOG_FILE, encoding="utf-8-sig", usecols=["token_address", "signal_time_unix", "notified"])
            now = time.time()
            recent = df[
                (df["notified"].astype(str).str.lower() == "true") &
                (now - df["signal_time_unix"].astype(float) < self.ttl)
            ]
            for _, row in recent.iterrows():
                token = str(row["token_address"])
                notify_time = float(row["signal_time_unix"])
                # すでに復元済みの場合は最新の通知時刻で上書き
                if token not in self._store or self._store[token] < notify_time:
                    self._store[token] = notify_time
            if len(recent) > 0:
                logger.info(f"[cache] 起動時に {len(recent)}件の通知キャッシュを復元しました")
        except Exception as e:
            logger.warning(f"[cache] キャッシュ復元失敗（無視して続行）: {e}")

    def is_recent(self, key: str) -> bool:
        ts = self._store.get(key)
        return ts is not None and (time.time() - ts) < self.ttl

    def mark(self, key: str):
        self._store[key] = time.time()
