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
    from indicators import calc_rsi_series, calc_atr_series, calc_vwap, calc_volume_surge

    mc_params   = config.get_mc_params(mc)
    surge_min   = mc_params["volume_surge_min"]
    aggregate   = mc_params["ohlcv_aggregate"]

    # 60分 ÷ 時間軸（分） = 検証本数
    lookforward = int(60 / aggregate)  # 5分足→12, 10分足→6, 15分足→4

    signal_count  = 0
    success_count = 0

    min_idx = config.RSI_PERIOD + config.ATR_PERIOD  # = 23本目から

    # RSI・ATRは全インデックス分を一括計算（ループ内での再計算を避ける）
    rsi_series = calc_rsi_series(df["close"])
    atr_series = calc_atr_series(df)

    # 出来高急増: 直近1本 vs 直前20本平均をSeries化
    vol_avg = df["volume"].shift(1).rolling(20).mean()
    vol_surge_series = df["volume"] / vol_avg.replace(0, float("nan"))

    # VWAP: 当日フィルタを使うと再現性ループで不整合が出るため全期間VWAPをSeries化
    typical   = (df["high"] + df["low"] + df["close"]) / 3
    cum_tpv   = (typical * df["volume"]).cumsum()
    cum_vol   = df["volume"].cumsum()
    vwap_series = cum_tpv / cum_vol.replace(0, float("nan"))

    for i in range(min_idx, len(df) - lookforward - 1):
        rsi       = float(rsi_series.iloc[i])
        atr       = float(atr_series.iloc[i])
        close_i   = float(df["close"].iloc[i])
        vol_surge = float(vol_surge_series.iloc[i]) if not pd.isna(vol_surge_series.iloc[i]) else 0.0
        vwap      = float(vwap_series.iloc[i])      if not pd.isna(vwap_series.iloc[i])      else close_i

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
