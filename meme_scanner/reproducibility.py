import logging

import pandas as pd

import config

logger = logging.getLogger(__name__)


def calc_reproducibility(df: pd.DataFrame, mc: float) -> dict:
    """
    過去の再現性（シグナル後に上昇した割合）を計算する。

    改善点:
      1. シグナル条件を scorer.py と完全一致させる
         （出来高急増 AND VWAP上抜け の両方必要 / RSI は 50 < RSI <= rsi_overbought のみ）
      2. SL/TP を考慮した勝敗判定（先に触れた方を優先）
      3. サンプル数を信頼性に反映（ベイズ補正 + 信頼度係数）
      4. 成功率 50% 以上も差をつける（75% で満点）

    Args:
        df    : OHLCVのDataFrame（100本）
        mc    : 時価総額（MC帯判定に使用）

    Returns:
        reproducibility_score : float  再現性スコア（0〜20点）
        signal_count          : int    シグナル発生回数
        success_count         : int    上昇成功回数（SL/TP基準）
        success_rate          : float  補正前の生の成功率（0.0〜1.0）
        adjusted_rate         : float  ベイズ補正後の成功率（0.0〜1.0）
    """
    from indicators import calc_rsi_series, calc_atr_series

    mc_params        = config.get_mc_params(mc)
    surge_min        = mc_params["volume_surge_min"]
    aggregate        = mc_params["ohlcv_aggregate"]
    rsi_overbought   = mc_params["rsi_overbought"]
    atr_sl_mult      = mc_params["atr_sl_mult"]
    atr_tp_mult      = mc_params["atr_tp_mult"]

    # 60分 ÷ 時間軸（分） = 検証本数
    lookforward = int(60 / aggregate)  # 5分足→12, 15分足→4

    signal_count  = 0
    success_count = 0

    min_idx = config.RSI_PERIOD + config.ATR_PERIOD  # = 23本目から

    # ── インジケーターをSeries化（ループ内での再計算を避ける） ────────────────
    rsi_series = calc_rsi_series(df["close"])
    atr_series = calc_atr_series(df)

    # 出来高急増: 直近1本 vs 直前20本平均
    vol_avg          = df["volume"].shift(1).rolling(20).mean()
    vol_surge_series = df["volume"] / vol_avg.replace(0, float("nan"))

    # VWAP: cumsumなのでi本目まででのVWAPになる（Look-ahead biasなし）
    typical     = (df["high"] + df["low"] + df["close"]) / 3
    cum_tpv     = (typical * df["volume"]).cumsum()
    cum_vol     = df["volume"].cumsum()
    vwap_series = cum_tpv / cum_vol.replace(0, float("nan"))

    for i in range(min_idx, len(df) - lookforward - 1):
        rsi       = float(rsi_series.iloc[i])
        atr       = float(atr_series.iloc[i])
        close_i   = float(df["close"].iloc[i])
        vol_surge = float(vol_surge_series.iloc[i]) if not pd.isna(vol_surge_series.iloc[i]) else 0.0
        vwap      = float(vwap_series.iloc[i])      if not pd.isna(vwap_series.iloc[i])      else close_i

        # ── 改善1: シグナル条件を scorer.py と完全一致 ────────────────────────
        # 出来高急増 AND VWAP上抜け の両方が必要（精度重視）
        # RSI は 50 < RSI <= rsi_overbought のみ（過熱域は除外）
        sig_volume = vol_surge >= surge_min
        sig_vwap   = close_i > vwap
        sig_rsi    = 50 < rsi <= rsi_overbought

        # 出来高+VWAP両方 OR RSI単独での高品質シグナル
        high_quality = (sig_volume and sig_vwap)
        rsi_only     = sig_rsi and not (sig_volume or sig_vwap)

        if not (high_quality or rsi_only):
            continue

        signal_count += 1

        # ── 改善2: SL/TP を考慮した勝敗判定 ────────────────────────────────
        sl = close_i - atr * atr_sl_mult
        tp = close_i + atr * atr_tp_mult

        future_df = df.iloc[i + 1 : i + 1 + lookforward]

        outcome = "open"
        for _, row in future_df.iterrows():
            hit_sl = row["low"]  <= sl
            hit_tp = row["high"] >= tp
            if hit_sl and hit_tp:
                # 同一ローソク足でSL/TP両方タッチ → 判定不能（失敗扱い）
                outcome = "both"
                break
            elif hit_tp:
                outcome = "win"
                break
            elif hit_sl:
                outcome = "loss"
                break

        if outcome == "win":
            success_count += 1

    # ── 改善3: サンプル数を信頼性に反映（ベイズ補正） ────────────────────────
    # 仮想サンプル: 5件分の「50%成功率」を事前分布として加える
    # サンプルが少ないほど 50% に引き戻される効果がある
    PRIOR_WEIGHT = 5
    PRIOR_RATE   = 0.5

    if signal_count == 0:
        success_rate  = 0.0
        adjusted_rate = 0.0
    else:
        success_rate  = success_count / signal_count
        adjusted_rate = (success_count + PRIOR_WEIGHT * PRIOR_RATE) / (signal_count + PRIOR_WEIGHT)

    # サンプル数に応じた信頼度係数（10件で100%、少ないほど割り引く）
    confidence = min(signal_count / 10.0, 1.0)

    # ── 改善4: 成功率 50% 以上も差をつける（75% で満点） ─────────────────────
    #
    #  adjusted_rate < 25%  → 0点
    #  25% ≤ rate < 50%     → 線形補間（0〜10点）
    #  50% ≤ rate < 75%     → 線形補間（10〜20点）
    #  rate ≥ 75%           → 満点 20点
    #
    if adjusted_rate >= 0.75:
        raw_score = 20.0
    elif adjusted_rate >= 0.50:
        raw_score = 10.0 + 10.0 * (adjusted_rate - 0.50) / 0.25
    elif adjusted_rate >= 0.25:
        raw_score = 10.0 * (adjusted_rate - 0.25) / 0.25
    else:
        raw_score = 0.0

    # 信頼度係数で割り引く
    reproducibility_score = raw_score * confidence

    logger.debug(
        f"[repro] signals={signal_count}, success={success_count}, "
        f"raw_rate={success_rate:.2f}, adj_rate={adjusted_rate:.2f}, "
        f"confidence={confidence:.2f}, score={reproducibility_score:.1f}"
    )

    return {
        "reproducibility_score": reproducibility_score,
        "signal_count":          signal_count,
        "success_count":         success_count,
        "success_rate":          success_rate,
        "adjusted_rate":         adjusted_rate,
    }
