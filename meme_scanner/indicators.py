import logging

import pandas as pd

from config import RSI_PERIOD, ATR_PERIOD

logger = logging.getLogger(__name__)


def calc_rsi(closes: pd.Series, period: int = RSI_PERIOD) -> float:
    """RSI(9) — Wilder平滑化（EWM）"""
    delta    = closes.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, float("inf"))
    rsi      = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def calc_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    """ATR(14) — True Range の EWM"""
    high       = df["high"]
    low        = df["low"]
    close      = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return float(atr.iloc[-1])


def calc_vwap(df: pd.DataFrame) -> float:
    """VWAP（当日分のみ）"""
    today   = df[df["timestamp"].dt.date == df["timestamp"].dt.date.iloc[-1]].copy()
    typical = (today["high"] + today["low"] + today["close"]) / 3
    total_vol = today["volume"].sum()
    if total_vol == 0:
        return float(df["close"].iloc[-1])
    return float((typical * today["volume"]).sum() / total_vol)


def calc_volume_surge(df: pd.DataFrame) -> float:
    """直近1本 vs 直前20本平均の出来高倍率"""
    recent = df["volume"].iloc[-1]
    avg    = df["volume"].iloc[-21:-1].mean()
    return float(recent / avg) if avg > 0 else 0.0


if __name__ == "__main__":
    import time
    import logging
    import dex_scanner
    import gt_fetcher
    import config

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    pairs = dex_scanner.get_filtered_pairs()
    if not pairs:
        print("フィルタ通過ペアなし")
    else:
        pair = pairs[0]
        pool_addr = gt_fetcher.get_pool_address(pair["token_address"])
        if pool_addr:
            time.sleep(config.GT_REQUEST_INTERVAL)
            df = gt_fetcher.fetch_ohlcv(pool_addr, pair["mc"])
            if df is not None and len(df) >= config.MIN_CANDLES:
                rsi        = calc_rsi(df["close"])
                atr        = calc_atr(df)
                vwap       = calc_vwap(df)
                vol_surge  = calc_volume_surge(df)
                close      = float(df["close"].iloc[-1])

                print(f"\n{pair['symbol']} インジケーター")
                print(f"  RSI(9)      : {rsi:.2f}")
                print(f"  ATR(14)     : {atr:.8f}")
                print(f"  VWAP        : {vwap:.8f}")
                print(f"  Vol Surge   : {vol_surge:.2f}x")
                print(f"  Close       : {close:.8f}")
                print(f"  VWAP上抜け  : {'YES' if close > vwap else 'NO'}")
