import logging

import pandas as pd

import config

logger = logging.getLogger(__name__)


def calc_reproducibility(df: pd.DataFrame, mc: float) -> dict:
    """
    過去の再現性（シグナル後に上昇した割合）を計算する。

    Args:
        df    : OHLCVのDataFrame（100本）
        mc    : 時価総額（MC帯判定に使用）

    Returns:
        reproducibility_score : float  再現性スコア（0〜20点）
        signal_count          : int    シグナル発生回数
        success_count         : int    上昇成功回数
        success_rate          : float  成功率（0.0〜1.0）
    """
    from indicators import calc_rsi, calc_vwap, calc_atr, calc_volume_surge

    mc_params   = config.get_mc_params(mc)
    surge_min   = mc_params["volume_surge_min"]
    aggregate   = mc_params["ohlcv_aggregate"]

    # 60分 ÷ 時間軸（分） = 検証本数
    lookforward = int(60 / aggregate)  # 5分足→12, 10分足→6, 15分足→4

    signal_count  = 0
    success_count = 0

    min_idx = config.RSI_PERIOD + config.ATR_PERIOD  # = 23本目から

    for i in range(min_idx, len(df) - lookforward - 1):
        window = df.iloc[: i + 1]

        rsi       = calc_rsi(window["close"])
        atr       = calc_atr(window)
        vwap      = calc_vwap(window)
        close_i   = float(df["close"].iloc[i])
        vol_surge = calc_volume_surge(window)

        # シグナル判定（どれか1つ）
        sig_volume = vol_surge >= surge_min
        sig_rsi    = rsi > 50
        sig_vwap   = close_i > vwap

        if not (sig_volume or sig_rsi or sig_vwap):
            continue

        signal_count += 1

        # シグナル後lookforward本以内の最高値を取得
        future           = df["close"].iloc[i + 1 : i + 1 + lookforward]
        max_future_close = float(future.max())

        # 上昇判定: max(future_close) - signal_close >= ATR × 0.7
        if max_future_close - close_i >= atr * 0.7:
            success_count += 1

    # 成功率 → スコア変換（20点満点）
    if signal_count == 0:
        success_rate          = 0.0
        reproducibility_score = 0.0
    else:
        success_rate = success_count / signal_count
        if success_rate >= 0.5:
            reproducibility_score = 20.0
        elif success_rate >= 0.25:
            reproducibility_score = 20.0 * (success_rate - 0.25) / 0.25
        else:
            reproducibility_score = 0.0

    return {
        "reproducibility_score": reproducibility_score,
        "signal_count":          signal_count,
        "success_count":         success_count,
        "success_rate":          success_rate,
    }
