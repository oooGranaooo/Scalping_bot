import logging

import pandas as pd

import config
from indicators import calc_rsi, calc_atr, calc_vwap, calc_volume_surge
from reproducibility import calc_reproducibility
from price_position import calc_price_position

logger = logging.getLogger(__name__)


def get_mc_band_label(mc: float) -> str:
    if mc < 1_000_000:
        return "$300K〜$1M"
    elif mc < 5_000_000:
        return "$1M〜$5M"
    else:
        return "$5M〜$50M"


def calculate_score(df: pd.DataFrame, pair_info: dict) -> dict:
    """
    スコアを計算して結果辞書を返す。

    Args:
        df        : OHLCVのDataFrame
        pair_info : dex_scanner.py が返す正規化済み辞書（mc フィールドを含む）

    Returns:
        score, breakdown, mc_band, rsi, atr, vwap, vol_surge,
        entry, stop_loss, take_profit, risk_reward,
        atr_sl_mult, atr_tp_mult,
        signal_count, success_count, success_rate, low_sample
    """
    mc        = pair_info["mc"]
    liquidity = pair_info["liquidity"]
    mc_params = config.get_mc_params(mc)

    # ── インジケーター計算 ──────────────────────────────────────
    rsi       = calc_rsi(df["close"])
    atr       = calc_atr(df)
    vwap      = calc_vwap(df)
    vol_surge = calc_volume_surge(df)
    close     = float(df["close"].iloc[-1])

    # ── 出来高急増スコア（25点） ──────────────────────────────
    surge_min  = mc_params["volume_surge_min"]
    surge_half = surge_min * 0.7

    if vol_surge >= surge_min:
        vol_score = 25.0
    elif vol_surge >= surge_half:
        vol_score = 25.0 * (vol_surge - surge_half) / (surge_min - surge_half)
    else:
        vol_score = 0.0

    # ── VWAP上抜けスコア（20点） ─────────────────────────────
    vwap_score = 20.0 if close > vwap else 0.0

    # ── RSI(9)スコア（20点）＋ 過熱ペナルティ（−15点） ────────
    rsi_ob = mc_params["rsi_overbought"]

    if 50 < rsi <= rsi_ob:
        rsi_score = 20.0
        penalty   = 0.0
    elif rsi_ob < rsi <= rsi_ob + 5:
        rsi_score = 20.0
        penalty   = -15.0 * (rsi - rsi_ob) / 5
    elif rsi > rsi_ob + 5:
        rsi_score = 20.0
        penalty   = -15.0
    else:
        rsi_score = 0.0
        penalty   = 0.0

    # ── 流動性スコア（15点） ──────────────────────────────────
    if liquidity >= 50_000:
        liq_score = 15.0
    elif liquidity >= 10_000:
        liq_score = 15.0 * (liquidity - 10_000) / (50_000 - 10_000)
    else:
        liq_score = 0.0

    # ── 再現性スコア（20点） ─────────────────────────────────
    repro      = calc_reproducibility(df, mc)
    repro_score = repro["reproducibility_score"]
    low_sample  = repro["signal_count"] < 5

    # ── 価格位置スコア（PPS）加減点（+10〜-10点） ────────────────
    pps_result = calc_price_position(df, vwap, rsi)
    pps_bonus  = pps_result["pps_bonus"]

    # ── 合計スコア ────────────────────────────────────────────
    total = vol_score + vwap_score + rsi_score + liq_score + repro_score + penalty + pps_bonus
    score = max(0, min(100, round(total)))

    # ── 損切り・利確 ──────────────────────────────────────────
    atr_sl_mult = mc_params["atr_sl_mult"]
    atr_tp_mult = mc_params["atr_tp_mult"]

    entry       = close
    stop_loss   = entry - atr * atr_sl_mult
    take_profit = entry + atr * atr_tp_mult

    sl_dist     = entry - stop_loss
    risk_reward = (take_profit - entry) / sl_dist if sl_dist > 0 else 0.0

    return {
        "score":         score,
        "breakdown": {
            "vol_score":   vol_score,
            "vwap_score":  vwap_score,
            "rsi_score":   rsi_score,
            "liq_score":   liq_score,
            "repro_score": repro_score,
            "penalty":     penalty,
            "pps_bonus":   pps_bonus,
        },
        "mc_band":       get_mc_band_label(mc),
        "rsi":           rsi,
        "atr":           atr,
        "vwap":          vwap,
        "vol_surge":     vol_surge,
        "entry":         entry,
        "stop_loss":     stop_loss,
        "take_profit":   take_profit,
        "risk_reward":   risk_reward,
        "atr_sl_mult":   atr_sl_mult,
        "atr_tp_mult":   atr_tp_mult,
        "surge_min":     surge_min,
        "rsi_ob":        rsi_ob,
        "pps":           pps_result["pps"],
        "pps_label":     pps_result["pps_label"],
        "pps_stars":     pps_result["pps_stars"],
        "range_pct":     pps_result["range_pct"],
        "vwap_dev":      pps_result["vwap_dev"],
        "signal_count":  repro["signal_count"],
        "success_count": repro["success_count"],
        "success_rate":  repro["success_rate"],
        "adjusted_rate": repro.get("adjusted_rate", repro["success_rate"]),
        "low_sample":    low_sample,
    }


if __name__ == "__main__":
    import time
    import dex_scanner
    import gt_fetcher

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    pairs = dex_scanner.get_filtered_pairs()
    if not pairs:
        print("フィルタ通過ペアなし")
    else:
        pair = pairs[0]
        print(f"\n対象: {pair['symbol']}  MC=${pair['mc']:,.0f}")

        pool_addr = gt_fetcher.get_pool_address(pair["token_address"])
        if pool_addr:
            time.sleep(config.GT_REQUEST_INTERVAL)
            df = gt_fetcher.fetch_ohlcv(pool_addr, pair["mc"])
            if df is not None and len(df) >= config.MIN_CANDLES:
                result = calculate_score(df, pair)
                bd = result["breakdown"]
                print(f"\n[スコア: {result['score']}/100]  MC帯: {result['mc_band']}")
                print(f"  出来高急増: {bd['vol_score']:.1f}/25  (×{result['vol_surge']:.2f})")
                print(f"  VWAP上抜け: {bd['vwap_score']:.1f}/20")
                print(f"  RSI(9):    {bd['rsi_score']:.1f}/20  (RSI={result['rsi']:.1f})")
                print(f"  流動性:    {bd['liq_score']:.1f}/15")
                print(f"  再現性:    {bd['repro_score']:.1f}/20  ({result['success_count']}/{result['signal_count']}回)")
                print(f"  ペナルティ:{bd['penalty']:.1f}")
                print(f"\n  Entry:  ${result['entry']:.8f}")
                print(f"  SL:     ${result['stop_loss']:.8f}  (ATR×{result['atr_sl_mult']})")
                print(f"  TP:     ${result['take_profit']:.8f}  (ATR×{result['atr_tp_mult']})")
                print(f"  RR比:   1:{result['risk_reward']:.2f}")
            else:
                print(f"OHLCVデータ不足（取得本数: {len(df) if df is not None else 0}）")
