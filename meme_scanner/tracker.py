"""
tracker.py
==========
スキャン結果と60分後の値動きを CSV に記録し、
設定値の最適化分析に使えるログシートを作成する。

記録タイミング:
  - スキャンのたびにスコア計算済みの全ペアを記録（閾値未満も含む）
  - 60分後にバックグラウンドで価格を取得して結果（WIN/LOSS）を更新

出力ファイル: logs/signal_log.csv（Excel/Numbers で直接開ける UTF-8 BOM 形式）
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests

import config

logger = logging.getLogger(__name__)
JST = timezone(timedelta(hours=9))

# ログフォルダとファイルのパス
LOG_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "signal_log.csv")

# シグナルから何秒後に結果を確認するか（60分）
OUTCOME_CHECK_DELAY = 3600

# GeckoTerminal API リトライ設定
_MAX_RETRIES = 3
_RETRY_WAIT  = 10.0  # 429時のリトライ待機秒数

# これより古いシグナルでも取得できなければ UNKNOWN に確定する（2時間）
OUTCOME_UNKNOWN_AFTER = 2 * 3600  # 2時間

# これより古いシグナルは GeckoTerminal のデータ範囲外になるため確認を諦める
OUTCOME_MAX_AGE = 10 * 3600  # 10時間

# CSV の列定義（順番がシートの列順になる）
COLUMNS = [
    # ── シグナル情報 ─────────────────────────────────────────
    "signal_id",           # 一意な識別子（8文字）
    "signal_time_jst",     # 検出日時（JST, 表示用）
    "signal_time_unix",    # 検出日時（Unix秒, 計算用）
    "symbol",              # トークンシンボル
    "mc_band",             # MC帯（$300K〜$1M / $1M〜$5M / $5M〜$50M）
    "mc",                  # 時価総額（$）
    "liquidity",           # 流動性（$）

    # ── 価格・指標 ────────────────────────────────────────────
    "entry_price",         # エントリー価格（検出時の終値）
    "sl_price",            # 損切り価格（entry - ATR × atr_sl_mult）
    "tp_price",            # 利確目標価格（entry + ATR × atr_tp_mult）
    "rr_ratio",            # リスクリワード比
    "atr",                 # ATR（平均的な価格の振れ幅）
    "vwap",                # VWAP（当日の出来高加重平均価格）
    "rsi",                 # RSI(9)の値
    "vol_surge",           # 出来高急増倍率（直近÷過去20本平均）

    # ── スコア内訳 ────────────────────────────────────────────
    "score_total",         # 合計スコア（0〜100点）
    "score_volume",        # 出来高急増スコア（0〜30点）
    "score_vwap",          # VWAP上抜けスコア（0 or 20点）
    "score_rsi",           # RSIスコア（0〜15点）
    "score_liquidity",     # 流動性スコア（廃止・常に0）
    "score_repro",         # 再現性スコア（0〜25点）
    "score_penalty",       # 過熱ペナルティ（0〜−15点）
    "score_pps_bonus",     # 価格位置ボーナス（+10〜-10点）

    # ── 価格位置スコア（PPS） ────────────────────────────────
    "pps",                 # 価格位置スコア（1〜5、5=強い安値圏）
    "pps_label",           # 価格位置ラベル（"強い安値圏" など）
    "range_pct",           # レンジ内位置（0.0=最安値〜1.0=最高値）
    "vwap_dev",            # VWAPからの乖離率（%）

    # ── 再現性の詳細 ──────────────────────────────────────────
    "signal_count",        # 過去シグナル発生回数
    "success_count",       # うち上昇成功回数
    "success_rate",        # 成功率（0.0〜1.0）
    "adjusted_rate",       # ベイズ補正後の成功率（0.0〜1.0）

    # ── 適用されたMC帯パラメータ ──────────────────────────────
    "ohlcv_aggregate",     # 使用した足の時間軸（分）
    "rsi_overbought",      # RSI過熱閾値
    "atr_sl_mult",         # 損切り倍率
    "atr_tp_mult",         # 利確倍率
    "volume_surge_min",    # 出来高急増の判定閾値

    # ── 通知情報 ──────────────────────────────────────────────
    "notified",            # Telegramに通知したか（True/False）
    "notify_threshold",    # 通知時の閾値設定値

    # ── リンク ────────────────────────────────────────────────
    "gecko_url",           # GeckoTerminal の URL
    "pool_address",        # GeckoTerminal のプールアドレス
    "token_address",       # トークンアドレス

    # ── 結果（60分後に自動記入） ──────────────────────────────
    "outcome_checked_at",  # 結果確認日時（JST）
    "price_15m",           # 15分後の価格
    "price_30m",           # 30分後の価格
    "price_60m",           # 60分後の価格
    "high_60m",            # 60分以内の最高値
    "low_60m",             # 60分以内の最安値
    "sl_hit",              # SL到達した（True/False）
    "tp_hit",              # TP到達した（True/False）
    "outcome",             # 結果: WIN/LOSS/WIN+/LOSS-/UNKNOWN/EXPIRED/OPEN
    "pnl_pct",             # 60分後の損益率（%）
]

# outcome の意味
# WIN     : TP に先着（最も良い結果）
# LOSS    : SL に先着（損切り発動）
# WIN+    : TP未到達だが60分後に上昇（小幅プラス）
# LOSS-   : SL未到達だが60分後に下落（小幅マイナス）
# BOTH    : 同一ローソク足でSL・TP両到達（判定不能）
# UNKNOWN : データ不足で判定不能
# EXPIRED : 10時間以上経過してデータ取得を諦めた
# OPEN    : まだ結果未確認


# ══════════════════════════════════════════════════════════════
#  内部ユーティリティ
# ══════════════════════════════════════════════════════════════

def _init_csv():
    """CSV が存在しない場合はヘッダー付きで新規作成する。logs/ フォルダも自動作成する。"""
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists(LOG_FILE):
        pd.DataFrame(columns=COLUMNS).to_csv(LOG_FILE, index=False, encoding="utf-8-sig")
        logger.info(f"[tracker] ログファイル新規作成: {LOG_FILE}")


def _read_csv() -> pd.DataFrame:
    """CSV を読み込む。失敗した場合は空の DataFrame を返す。"""
    try:
        return pd.read_csv(LOG_FILE, encoding="utf-8-sig", dtype={"signal_time_unix": "Int64"})
    except Exception as e:
        logger.error(f"[tracker] ログ読み込み失敗: {e}")
        return pd.DataFrame(columns=COLUMNS)


def _write_csv(df: pd.DataFrame):
    """DataFrame を CSV に書き込む。"""
    try:
        df.to_csv(LOG_FILE, index=False, encoding="utf-8-sig")
    except Exception as e:
        logger.error(f"[tracker] ログ書き込み失敗: {e}")


# ══════════════════════════════════════════════════════════════
#  公開 API
# ══════════════════════════════════════════════════════════════

def rotate_log() -> str | None:
    """
    設定変更時に呼び出す。
    現在の signal_log.csv をタイムスタンプ付き名でリネーム（アーカイブ）し、
    新しい空の signal_log.csv を作成する。

    Returns:
        アーカイブされたファイルの絶対パス。元ファイルが存在しなかった場合は None。
    """
    archived = None
    if os.path.exists(LOG_FILE):
        ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        archived = os.path.join(LOG_DIR, f"signal_log_until_{ts}.csv")
        os.rename(LOG_FILE, archived)
        logger.info(f"[tracker] アーカイブ: {os.path.basename(archived)}")
    # 新しい signal_log.csv を初期化
    _init_csv()
    logger.info("[tracker] 新しい signal_log.csv を作成しました")
    return archived


def has_old_open_signals() -> bool:
    """1時間以上経過した OPEN シグナルが存在するか確認する。"""
    _init_csv()
    df = _read_csv()
    if df.empty:
        return False
    now_unix = int(time.time())
    return bool(
        (
            (df["outcome"] == "OPEN") &
            (now_unix - df["signal_time_unix"].astype("Int64") >= OUTCOME_CHECK_DELAY)
        ).any()
    )


def is_token_open(token_address: str) -> bool:
    """指定トークンが OPEN 状態で記録されているか確認する。"""
    _init_csv()
    df = _read_csv()
    if df.empty:
        return False
    return bool(
        ((df["token_address"] == token_address) & (df["outcome"] == "OPEN")).any()
    )


def record_signal(
    pair_info: dict,
    result: dict,
    pool_address: str,
    notified: bool,
    notify_threshold: int,
) -> str:
    """
    スキャン結果を CSV の新規行として記録する。
    閾値未満のペアも含め、スコア計算できたすべてを記録する。
    同一トークンの outcome が OPEN のまま残っている場合は重複記録しない。

    Args:
        pair_info        : dex_scanner が返す正規化済みペア情報
        result           : scorer.calculate_score() の戻り値
        pool_address     : GeckoTerminal のプールアドレス
        notified         : Telegram 通知を送信したか
        notify_threshold : その時点の通知閾値

    Returns:
        signal_id（8文字の識別子）。スキップした場合は空文字列。
    """
    _init_csv()

    # OPEN 中の同トークンがあれば重複記録しない
    df = _read_csv()
    token = pair_info["token_address"]
    if not df.empty:
        already_open = (
            (df["token_address"] == token) &
            (df["outcome"] == "OPEN")
        ).any()
        if already_open:
            logger.info(f"[tracker] スキップ（OPEN中）: {pair_info['symbol']}")
            return ""

    mc_params = config.get_mc_params(pair_info["mc"])
    bd        = result["breakdown"]
    now       = datetime.now(JST)
    signal_id = str(uuid.uuid4())[:8]

    row = {
        "signal_id":         signal_id,
        "signal_time_jst":   now.strftime("%Y-%m-%d %H:%M:%S"),
        "signal_time_unix":  int(now.timestamp()),
        "symbol":            pair_info["symbol"],
        "mc_band":           result["mc_band"],
        "mc":                int(pair_info["mc"]),
        "liquidity":         int(pair_info["liquidity"]),
        "entry_price":       result["entry"],
        "sl_price":          result["stop_loss"],
        "tp_price":          result["take_profit"],
        "rr_ratio":          round(result["risk_reward"], 2),
        "atr":               result["atr"],
        "vwap":              result["vwap"],
        "rsi":               round(result["rsi"], 2),
        "vol_surge":         round(result["vol_surge"], 2),
        "score_total":       result["score"],
        "score_volume":      round(bd["vol_score"], 1),
        "score_vwap":        round(bd["vwap_score"], 1),
        "score_rsi":         round(bd["rsi_score"], 1),
        "score_liquidity":   0,
        "score_repro":       round(bd["repro_score"], 1),
        "score_penalty":     round(bd["penalty"], 1),
        "score_pps_bonus":   round(bd.get("pps_bonus", 0), 1),
        "pps":               result.get("pps", 3),
        "pps_label":         result.get("pps_label", "中間"),
        "range_pct":         result.get("range_pct", 0.5),
        "vwap_dev":          result.get("vwap_dev", 0.0),
        "signal_count":      result["signal_count"],
        "success_count":     result["success_count"],
        "success_rate":      round(result["success_rate"], 3),
        "adjusted_rate":     round(result.get("adjusted_rate", result["success_rate"]), 3),
        "ohlcv_aggregate":   mc_params["ohlcv_aggregate"],
        "rsi_overbought":    mc_params["rsi_overbought"],
        "atr_sl_mult":       mc_params["atr_sl_mult"],
        "atr_tp_mult":       mc_params["atr_tp_mult"],
        "volume_surge_min":  mc_params["volume_surge_min"],
        "notified":          notified,
        "notify_threshold":  notify_threshold,
        "gecko_url":         pair_info["gecko_url"],
        "pool_address":      pool_address,
        "token_address":     pair_info["token_address"],
        # 結果は後から記入
        "outcome_checked_at": "",
        "price_15m":          "",
        "price_30m":          "",
        "price_60m":          "",
        "high_60m":           "",
        "low_60m":            "",
        "sl_hit":             "",
        "tp_hit":             "",
        "outcome":            "OPEN",
        "pnl_pct":            "",
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write_csv(df)

    logger.info(
        f"[tracker] 記録: {pair_info['symbol']}  "
        f"スコア={result['score']}  notified={notified}"
    )
    return signal_id


def check_outcomes() -> int:
    """
    OPEN 状態のシグナルのうち、60分以上経過したものの結果を確認して CSV を更新する。
    GeckoTerminal の 5分足 OHLCV でシグナル後の値動きを取得し、
    SL/TP 到達・損益率を記録する。

    Returns:
        更新件数（int）
    """
    _init_csv()
    df       = _read_csv()
    now_unix = int(time.time())
    updated  = 0

    # OPEN かつ 60分以上経過したシグナルを対象
    mask = (
        (df["outcome"] == "OPEN") &
        (now_unix - df["signal_time_unix"].astype("Int64") >= OUTCOME_CHECK_DELAY)
    )
    pending = df[mask]

    if pending.empty:
        return 0

    logger.info(f"[tracker] 結果確認対象: {len(pending)}件")

    for idx, row in pending.iterrows():
        age = now_unix - int(row["signal_time_unix"])

        # 10時間以上前のシグナルはデータ取得を諦める
        if age > OUTCOME_MAX_AGE:
            df.at[idx, "outcome"]            = "EXPIRED"
            df.at[idx, "outcome_checked_at"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
            updated += 1
            logger.info(f"[tracker] EXPIRED: {row.get('symbol')} ({age//3600}時間経過)")
            continue

        try:
            outcome_data = _fetch_outcome(
                pool_address = str(row["pool_address"]),
                signal_unix  = int(row["signal_time_unix"]),
                entry_price  = float(row["entry_price"]),
                sl_price     = float(row["sl_price"]),
                tp_price     = float(row["tp_price"]),
            )
            if outcome_data:
                for col, val in outcome_data.items():
                    df.at[idx, col] = val
                updated += 1
                logger.info(
                    f"[tracker] 結果確認: {row.get('symbol')} "
                    f"→ {outcome_data['outcome']}  pnl={outcome_data['pnl_pct']}%"
                )
            elif age >= OUTCOME_UNKNOWN_AFTER:
                # 2時間以上経過してもデータが取れない場合は UNKNOWN に確定する
                df.at[idx, "outcome"]            = "UNKNOWN"
                df.at[idx, "outcome_checked_at"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
                updated += 1
                logger.info(f"[tracker] UNKNOWN（データ取得不可）: {row.get('symbol')} ({age//3600}時間{(age%3600)//60}分経過)")
        except Exception as e:
            logger.warning(f"[tracker] 結果確認失敗 ({row.get('symbol', '?')}): {e}")

        time.sleep(config.GT_REQUEST_INTERVAL)

    if updated > 0:
        _write_csv(df)
        logger.info(f"[tracker] {updated}件の結果を更新しました")

    return updated


def get_summary() -> dict:
    """
    ログの統計サマリーを返す（/logsummary コマンド用）。
    """
    _init_csv()
    df = _read_csv()

    if df.empty:
        return {"total": 0}

    WIN_OUTCOMES  = {"WIN", "WIN+"}
    LOSS_OUTCOMES = {"LOSS", "LOSS-"}

    resolved = df[df["outcome"].isin(WIN_OUTCOMES | LOSS_OUTCOMES | {"BOTH", "UNKNOWN"})]
    wins     = df[df["outcome"].isin(WIN_OUTCOMES)]
    losses   = df[df["outcome"].isin(LOSS_OUTCOMES)]
    notified = df[df["notified"].astype(str).str.lower() == "true"]
    open_    = df[df["outcome"] == "OPEN"]

    total_resolved = len(resolved)
    win_rate = round(len(wins) / total_resolved * 100, 1) if total_resolved > 0 else 0.0

    # 平均損益率（pnl_pct が数値の行のみ）
    pnl_series = (
        df["pnl_pct"]
        .replace("", pd.NA)
        .dropna()
        .astype(float)
    )
    avg_pnl = round(float(pnl_series.mean()), 2) if len(pnl_series) > 0 else 0.0

    # 通知済みシグナルの勝率・平均損益率
    notified_resolved = notified[notified["outcome"].isin(WIN_OUTCOMES | LOSS_OUTCOMES)]
    notified_wins     = notified[notified["outcome"].isin(WIN_OUTCOMES)]
    notified_win_rate = (
        round(len(notified_wins) / len(notified_resolved) * 100, 1)
        if len(notified_resolved) > 0 else 0.0
    )

    notified_pnl_series = (
        notified["pnl_pct"]
        .replace("", pd.NA)
        .dropna()
        .astype(float)
    )
    notified_avg_pnl = round(float(notified_pnl_series.mean()), 2) if len(notified_pnl_series) > 0 else 0.0

    return {
        "total":               len(df),
        "open":                len(open_),
        "resolved":            total_resolved,
        "wins":                len(wins),
        "losses":              len(losses),
        "win_rate":            win_rate,
        "notified":            len(notified),
        "notified_resolved":   len(notified_resolved),
        "notified_win_rate":   notified_win_rate,
        "notified_avg_pnl":    notified_avg_pnl,
        "avg_score":           round(float(df["score_total"].mean()), 1) if len(df) > 0 else 0.0,
        "avg_pnl":             avg_pnl,
        "log_file":            LOG_FILE,
    }


# ══════════════════════════════════════════════════════════════
#  内部: GeckoTerminal から結果を取得
# ══════════════════════════════════════════════════════════════

def _fetch_outcome(
    pool_address: str,
    signal_unix:  int,
    entry_price:  float,
    sl_price:     float,
    tp_price:     float,
) -> dict | None:
    """
    GeckoTerminal から 5分足 OHLCV を取得し、
    シグナル後 60分間の値動きを分析して結果を返す。

    before_timestamp を signal_unix + 65分 に指定することで
    シグナル後のデータを確実に取得する。
    """
    # signal の 65分後を before_timestamp に指定（ウィンドウ終点 + バッファ 5分）
    before_ts = signal_unix + 3900

    url = (
        f"{config.GT_BASE_URL}/networks/{config.CHAIN}"
        f"/pools/{pool_address}/ohlcv/minute"
    )
    params = {
        "aggregate":        5,           # 5分足で統一（MC帯問わず）
        "before_timestamp": before_ts,
        "limit":            100,
        "currency":         "usd",
        "token":            "base",
    }

    resp = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=config.GT_HEADERS, params=params, timeout=10)
            resp.raise_for_status()
            break
        except requests.exceptions.HTTPError as e:
            if resp is not None and resp.status_code == 429 and attempt < _MAX_RETRIES:
                wait = _RETRY_WAIT * (attempt + 1)
                logger.warning(
                    f"[tracker] OHLCV 429 レート制限 ({pool_address}) "
                    f"→ {wait:.0f}秒後にリトライ ({attempt + 1}/{_MAX_RETRIES})"
                )
                time.sleep(wait)
                continue
            logger.warning(f"[tracker] OHLCV取得失敗 ({pool_address}): {e}")
            return None
        except Exception as e:
            logger.warning(f"[tracker] OHLCV取得失敗 ({pool_address}): {e}")
            return None
    else:
        return None

    try:
        raw = resp.json()["data"]["attributes"]["ohlcv_list"]
        raw.reverse()  # 降順 → 昇順

        df = pd.DataFrame(
            raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df = df.astype({
            "timestamp": int, "open": float, "high": float,
            "low": float, "close": float, "volume": float,
        })

        # シグナル後 60分のウィンドウ
        # 5分足のタイムスタンプは300秒境界なので、シグナル時刻を含む足の開始時刻から始める
        candle_sec    = 300
        signal_candle = (signal_unix // candle_sec) * candle_sec
        window = df[
            (df["timestamp"] >= signal_candle) &
            (df["timestamp"] <= signal_unix + 3600)
        ].copy()

        if window.empty:
            logger.warning(
                f"[tracker] シグナル後ウィンドウにデータなし "
                f"(pool={pool_address}, signal={signal_unix}, candle_start={signal_candle})"
            )
            return None

        # 各時点の価格（その時点以前の最新終値）
        def price_at(target_unix: int) -> float | None:
            cands = df[df["timestamp"] <= target_unix]
            return float(cands["close"].iloc[-1]) if not cands.empty else None

        p15 = price_at(signal_unix + 900)
        p30 = price_at(signal_unix + 1800)
        p60 = price_at(signal_unix + 3600)

        high_60 = float(window["high"].max())
        low_60  = float(window["low"].min())

        # SL・TP 到達判定（どちらが先かを時系列で確認）
        sl_hit    = False
        tp_hit    = False
        first_hit = None

        for _, candle in window.sort_values("timestamp").iterrows():
            hit_sl = candle["low"]  <= sl_price
            hit_tp = candle["high"] >= tp_price

            if first_hit is None:
                if hit_sl and hit_tp:
                    # 同一ローソク足で両到達 → 先着不明
                    sl_hit = tp_hit = True
                    first_hit = "BOTH"
                elif hit_sl:
                    sl_hit    = True
                    first_hit = "LOSS"
                elif hit_tp:
                    tp_hit    = True
                    first_hit = "WIN"
            else:
                # 先着確定後も到達有無だけ記録
                if hit_sl: sl_hit = True
                if hit_tp: tp_hit = True

        # 結果分類
        if first_hit == "WIN":
            outcome = "WIN"       # TP 先着（利確成功）
        elif first_hit == "LOSS":
            outcome = "LOSS"      # SL 先着（損切り発動）
        elif first_hit == "BOTH":
            outcome = "BOTH"      # 同足で両到達（判定不能）
        elif p60 is not None and p60 > entry_price:
            outcome = "WIN+"      # TP 未到達だが 60分後にプラス
        elif p60 is not None:
            outcome = "LOSS-"     # SL 未到達だが 60分後にマイナス
        else:
            outcome = "UNKNOWN"

        pnl_pct = (
            round((p60 - entry_price) / entry_price * 100, 2)
            if p60 is not None else ""
        )

        return {
            "outcome_checked_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
            "price_15m":          p15,
            "price_30m":          p30,
            "price_60m":          p60,
            "high_60m":           high_60,
            "low_60m":            low_60,
            "sl_hit":             sl_hit,
            "tp_hit":             tp_hit,
            "outcome":            outcome,
            "pnl_pct":            pnl_pct,
        }

    except Exception as e:
        logger.warning(f"[tracker] OHLCV取得失敗 ({pool_address}): {e}")
        return None
