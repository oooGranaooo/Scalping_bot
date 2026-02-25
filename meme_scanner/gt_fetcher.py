from __future__ import annotations

import logging
import time

import pandas as pd
import requests

import config

logger = logging.getLogger(__name__)


def get_pool_address(token_address: str) -> str | None:
    """
    トークンアドレス → 最流動性プールアドレスを取得。
    取得失敗またはデータなしの場合は None を返す。
    """
    url = f"{config.GT_BASE_URL}/networks/{config.CHAIN}/tokens/{token_address}/pools"
    try:
        resp = requests.get(url, headers=config.GT_HEADERS, params={"page": 1}, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return None
        return data[0]["attributes"]["address"]
    except Exception as e:
        logger.warning(f"プールアドレス取得失敗 ({token_address}): {e}")
        return None


_MAX_RETRIES = 3
_RETRY_WAIT  = 10.0  # 429時のリトライ待機秒数


def fetch_ohlcv(pool_address: str, mc: float) -> pd.DataFrame | None:
    """
    プールアドレスとMCを受け取り、MC帯に応じた時間軸のOHLCVを返す。
    columns: ["timestamp", "open", "high", "low", "close", "volume"]
    429 Too Many Requests の場合は最大 _MAX_RETRIES 回リトライする。
    取得失敗の場合は None を返す。
    """
    mc_params = config.get_mc_params(mc)
    aggregate = mc_params["ohlcv_aggregate"]

    url = (
        f"{config.GT_BASE_URL}/networks/{config.CHAIN}"
        f"/pools/{pool_address}/ohlcv/{config.OHLCV_TIMEFRAME}"
    )
    params = {
        "aggregate": aggregate,
        "limit":     config.OHLCV_LIMIT,
        "currency":  "usd",
        "token":     "base",
    }

    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=config.GT_HEADERS, params=params, timeout=10)
            resp.raise_for_status()
            raw = resp.json()["data"]["attributes"]["ohlcv_list"]
            raw.reverse()  # 降順 → 昇順（古い順）に変換

            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
            df = df.astype({
                "open":   float,
                "high":   float,
                "low":    float,
                "close":  float,
                "volume": float,
            })
            return df
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 429 and attempt < _MAX_RETRIES:
                wait = _RETRY_WAIT * (attempt + 1)
                logger.warning(
                    f"OHLCV 429 レート制限 ({pool_address}) "
                    f"→ {wait:.0f}秒後にリトライ ({attempt + 1}/{_MAX_RETRIES})"
                )
                time.sleep(wait)
                continue
            logger.warning(f"OHLCV取得失敗 ({pool_address}): {e}")
            return None
        except Exception as e:
            logger.warning(f"OHLCV取得失敗 ({pool_address}): {e}")
            return None

    return None


if __name__ == "__main__":
    import dex_scanner

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    pairs = dex_scanner.get_filtered_pairs()
    if not pairs:
        print("フィルタ通過ペアなし")
    else:
        pair = pairs[0]
        print(f"\n対象: {pair['symbol']}  token={pair['token_address']}")

        pool_addr = get_pool_address(pair["token_address"])
        print(f"プールアドレス: {pool_addr}")

        if pool_addr:
            time.sleep(config.GT_REQUEST_INTERVAL)
            df = fetch_ohlcv(pool_addr, pair["mc"])
            if df is not None:
                print(f"\nOHLCV取得: {len(df)}本")
                print(df.tail())
                print(f"\ntimestamp昇順確認: {df['timestamp'].is_monotonic_increasing}")
            else:
                print("OHLCVの取得に失敗")
