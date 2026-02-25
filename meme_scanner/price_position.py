import pandas as pd


# 価格位置スコア（PPS）の加減点テーブル
# 安値圏ほど高得点（星が多い）、高値圏ほど減点
_PPS_BONUS = {
    5: +10,   # ⭐⭐⭐⭐⭐ 強い安値圏
    4: +5,    # ⭐⭐⭐⭐  やや安値圏
    3:  0,    # ⭐⭐⭐   中間
    2: -5,    # ⭐⭐     やや高値圏
    1: -10,   # ⭐       強い高値圏
}

_PPS_LABEL = {
    5: "強い安値圏",
    4: "やや安値圏",
    3: "中間",
    2: "やや高値圏",
    1: "強い高値圏",
}

_PPS_STARS = {
    5: "⭐⭐⭐⭐⭐",
    4: "⭐⭐⭐⭐",
    3: "⭐⭐⭐",
    2: "⭐⭐",
    1: "⭐",
}


def calc_price_position(df: pd.DataFrame, vwap: float, rsi: float) -> dict:
    """
    現在の価格が直近レンジの中でどの位置にいるかを5段階で評価する。
    安値圏ほど星が多く（PPS=5）、高値圏ほど星が少ない（PPS=1）。

    評価軸（3軸の平均でPPSを決定）:
      1. レンジ内位置: (close - 期間最安値) / (期間最高値 - 期間最安値)
         低いほど安値圏
      2. VWAPからの乖離率: (close - vwap) / vwap × 100
         マイナスほど安値圏
      3. RSIの位置
         低いほど安値圏（売られすぎ）

    Args:
        df   : OHLCVのDataFrame（100本）
        vwap : 計算済みVWAP値
        rsi  : 計算済みRSI値

    Returns:
        pps        : int   1〜5（5=強い安値圏, 1=強い高値圏）
        pps_label  : str   "強い安値圏" など
        pps_stars  : str   "⭐⭐⭐⭐⭐" など
        pps_bonus  : float スコアへの加減点（+10〜-10）
        range_pct  : float レンジ内位置（0.0=最安値, 1.0=最高値）
        vwap_dev   : float VWAPからの乖離率（%）
        rsi_val    : float RSI値（参照用）
    """
    close = float(df["close"].iloc[-1])

    # ── 軸1: レンジ内位置（0.0〜1.0、低いほど安値圏） ─────────────────
    highest = float(df["high"].max())
    lowest  = float(df["low"].min())
    rang    = highest - lowest

    if rang > 0:
        range_pct = (close - lowest) / rang   # 0.0=最安値, 1.0=最高値
    else:
        range_pct = 0.5

    # レンジ内位置 → 5段階（低いほど安値圏 → 星多い）
    if range_pct <= 0.20:
        axis1 = 5
    elif range_pct <= 0.40:
        axis1 = 4
    elif range_pct <= 0.60:
        axis1 = 3
    elif range_pct <= 0.80:
        axis1 = 2
    else:
        axis1 = 1

    # ── 軸2: VWAPからの乖離率（マイナスほど安値圏 → 星多い） ────────────
    if vwap > 0:
        vwap_dev = (close - vwap) / vwap * 100
    else:
        vwap_dev = 0.0

    if vwap_dev <= -5.0:
        axis2 = 5
    elif vwap_dev <= -1.0:
        axis2 = 4
    elif vwap_dev <= +1.0:
        axis2 = 3
    elif vwap_dev <= +5.0:
        axis2 = 2
    else:
        axis2 = 1

    # ── 軸3: RSIの位置（低いほど安値圏 → 星多い） ───────────────────────
    if rsi <= 30:
        axis3 = 5
    elif rsi <= 45:
        axis3 = 4
    elif rsi <= 55:
        axis3 = 3
    elif rsi <= 70:
        axis3 = 2
    else:
        axis3 = 1

    # ── 3軸の平均で最終PPS決定（四捨五入） ──────────────────────────────
    raw = (axis1 + axis2 + axis3) / 3.0
    pps = max(1, min(5, round(raw)))

    return {
        "pps":       pps,
        "pps_label": _PPS_LABEL[pps],
        "pps_stars": _PPS_STARS[pps],
        "pps_bonus": float(_PPS_BONUS[pps]),
        "range_pct": round(range_pct, 3),
        "vwap_dev":  round(vwap_dev, 2),
        "rsi_val":   round(rsi, 1),
    }
