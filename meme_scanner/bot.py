from __future__ import annotations

import asyncio
import html
import logging
import os
import subprocess
from datetime import datetime, timezone, timedelta, time as dtime

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import config
import dex_scanner
import gt_fetcher
import scorer
import tracker
from cache import NotificationCache

# ── ロギング設定 ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("scanner.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# ── グローバル状態 ───────────────────────────────────────────────
cache            = NotificationCache()
notify_threshold = config.NOTIFY_THRESHOLD
scan_interval    = config.SCAN_INTERVAL
scan_running     = False
last_scan_time   = "未実行"


# ── メッセージフォーマット ────────────────────────────────────────
def format_message(pair: dict, result: dict, pool_address: str) -> str:
    bd         = result["breakdown"]
    ts         = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    low_warn   = " ⚠️ サンプル少" if result["low_sample"] else ""
    symbol     = html.escape(pair["symbol"])
    name       = html.escape(pair["name"])
    ca         = html.escape(pair["token_address"])

    # 現在価格からサプライを逆算し、各指標をMC換算する
    entry  = result["entry"]
    mc     = pair["mc"]
    supply = mc / entry if entry > 0 else 0
    sl_mc  = result["stop_loss"]   * supply
    tp_mc  = result["take_profit"] * supply
    vwap_mc = result["vwap"]       * supply
    atr_pct = result["atr"] / entry * 100 if entry > 0 else 0
    atr_mc  = result["atr"] * supply

    msg = (
        f"🚨 ミームコインアラート 🚨\n"
        f"\n"
        f"🪙 {symbol} ({name})\n"
        f"🔗 Solana  |  📦 MC帯: {result['mc_band']}\n"
        f"📊 スコア: {result['score']}/100\n"
        f"\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📈 スコア内訳\n"
        f"  出来高急増:  {bd['vol_score']:.0f}/25  "
        f"(×{result['vol_surge']:.1f} / 閾値×{result['surge_min']:.0f})\n"
        f"  VWAP上抜け: {bd['vwap_score']:.0f}/20\n"
        f"  RSI(9):     {bd['rsi_score']:.0f}/20  "
        f"(RSI: {result['rsi']:.1f} / 過熱閾値: {result['rsi_ob']})\n"
        f"  流動性:     {bd['liq_score']:.0f}/15\n"
        f"  再現性:     {bd['repro_score']:.0f}/20  "
        f"({result['success_count']}/{result['signal_count']}回成功 / "
        f"{result['success_rate']:.0%}){low_warn}\n"
        f"  過熱ペナル: {bd['penalty']:.0f}/−15\n"
        f"\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 現在MC:    ${mc:,.0f}\n"
        f"📉 損切りMC:  ${sl_mc:,.0f}  (ATR×{result['atr_sl_mult']})\n"
        f"📈 利確目標MC:${tp_mc:,.0f}  (ATR×{result['atr_tp_mult']})\n"
        f"⚖️  RR比:     1:{result['risk_reward']:.1f}\n"
        f"📐 ATR:       {atr_pct:.2f}%  (${atr_mc:,.0f})\n"
        f"📊 VWAP MC:   ${vwap_mc:,.0f}\n"
        f"\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💧 流動性:   ${pair['liquidity']:,.0f}\n"
        f"🕐 1h出来高: ${pair['volume_h1']:,.0f}\n"
        f"\n"
        f"📋 CA（タップでコピー）\n"
        f"<code>{ca}</code>\n"
        f"⏰ {ts} JST"
    )
    return msg


# ── スキャン本体 ─────────────────────────────────────────────────
async def run_scan(context: ContextTypes.DEFAULT_TYPE):
    global last_scan_time, scan_running

    logger.info("スキャン開始")

    # Stage 1: GeckoTerminal trending_pools でMCフィルタ
    pairs = dex_scanner.get_filtered_pairs()
    logger.info(f"Stage1完了: MCレンジ内からランダム{len(pairs)}件をスキャン")

    for pair in pairs:
        token_address = pair["token_address"]

        # 重複チェック
        if cache.is_recent(token_address):
            logger.info(f"{pair['symbol']}: キャッシュ済みのためスキップ")
            continue

        # OPEN中チェック（OHLCV取得前にスキップして無駄なAPI呼び出しを防ぐ）
        if tracker.is_token_open(token_address):
            logger.info(f"{pair['symbol']}: OPEN中のためスキップ")
            continue

        # Stage 2: OHLCV取得
        # pair_address は trending_pools から取得済みのプールアドレスをそのまま使用
        pool_address = pair["pair_address"]
        if not pool_address:
            logger.warning(f"{pair['symbol']}: プールアドレスなし、スキップ")
            continue

        await asyncio.sleep(config.GT_REQUEST_INTERVAL)
        df = gt_fetcher.fetch_ohlcv(pool_address, pair["mc"])
        if df is None or len(df) < config.MIN_CANDLES:
            logger.warning(
                f"{pair['symbol']}: OHLCVデータ不足"
                f"（{len(df) if df is not None else 0}本）、スキップ"
            )
            continue

        # スコア計算
        try:
            result = scorer.calculate_score(df, pair)
        except Exception as e:
            logger.error(f"{pair['symbol']}: スコア計算エラー: {e}")
            continue

        logger.info(f"{pair['symbol']}: {result['score']}点")

        # 閾値超えたら通知
        notified = result["score"] >= notify_threshold
        if notified:
            msg = format_message(pair, result, pool_address)
            await context.bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=msg,
                parse_mode=ParseMode.HTML,
            )
            cache.mark(token_address)
            logger.info(f"{pair['symbol']}: 通知送信（{result['score']}点）")

        # スコア計算済みのすべてのペアをログに記録（閾値未満も含む）
        tracker.record_signal(pair, result, pool_address, notified, notify_threshold)

    last_scan_time = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    logger.info("スキャン完了")


async def check_outcomes_job(context: ContextTypes.DEFAULT_TYPE):
    """シグナルから60分後の値動きを確認してログを更新するバックグラウンドジョブ。"""
    updated = tracker.check_outcomes()
    if updated > 0:
        logger.info(f"[tracker] バックグラウンド結果確認: {updated}件更新")


# ── ヘルプテキスト（常に最新の設定値を返す関数） ──────────────────
def get_help_text() -> str:
    interval_disp = (
        f"{scan_interval // 60}分"
        if scan_interval % 60 == 0
        else f"{scan_interval}秒"
    )
    return (
        "🤖 Meme Scanner Bot\n"
        "Solana ミームコインをスキャンして高スコアのシグナルを通知します。\n"
        "\n"
        "━━━━━━━━━━━━━━━\n"
        "📋 コマンド一覧\n"
        "\n"
        "/start          自動スキャン開始\n"
        "/stop           自動スキャン停止\n"
        "/scan           今すぐスキャン実行\n"
        "/status         現在の設定・稼働状況を表示\n"
        "/help           このヘルプを表示\n"
        "\n"
        "⚙️ 設定変更\n"
        "/threshold <点数>          通知閾値を変更\n"
        "  例: /threshold 65\n"
        "/setmc <最小> <最大>       MCレンジを変更\n"
        "  例: /setmc 500K 50M\n"
        "/setinterval <秒|分m>      スキャン間隔を変更\n"
        "  例: /setinterval 300\n"
        "  例: /setinterval 10m\n"
        "/logsummary                ログの勝率・損益サマリーを表示\n"
        "\n"
        "━━━━━━━━━━━━━━━\n"
        "📊 スコア配点（100点満点）\n"
        "  出来高急増   25点\n"
        "  VWAP上抜け  20点\n"
        "  RSI(9)      20点\n"
        "  流動性       15点\n"
        "  再現性       20点\n"
        "  過熱ペナルティ −15点\n"
        "\n"
        "━━━━━━━━━━━━━━━\n"
        "🎯 現在の設定\n"
        f"  MCレンジ:     ${config.MC_MIN:,.0f} 〜 ${config.MC_MAX:,.0f}\n"
        f"  通知閾値:     {notify_threshold}点以上\n"
        f"  スキャン間隔: {interval_disp}"
    )


# ── コマンドハンドラ ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global scan_running
    if scan_running:
        await update.message.reply_text("✅ 自動スキャンはすでに稼働中です。")
        return

    scan_running = True
    context.job_queue.run_repeating(
        run_scan,
        interval=scan_interval,
        first=0,
        name="auto_scan",
    )
    # 15分ごとにシグナルの結果（60分後の値動き）を確認するジョブ
    context.job_queue.run_repeating(
        check_outcomes_job,
        interval=900,   # 15分ごと
        first=60,       # /start から1分後に初回実行（起動直後の未確認分を早期に処理）
        name="outcome_check",
    )
    interval_disp = (
        f"{scan_interval // 60}分"
        if scan_interval % 60 == 0
        else f"{scan_interval}秒"
    )
    await update.message.reply_text(
        f"🚀 スキャンBot起動\n"
        f"⏱️ スキャン間隔: {interval_disp}\n"
        f"🎯 通知閾値: {notify_threshold}点以上"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_help_text())


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 即時スキャンを実行します...")
    await run_scan(context)
    await update.message.reply_text("✅ スキャン完了")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global scan_running
    for name in ("auto_scan", "outcome_check"):
        for job in context.job_queue.get_jobs_by_name(name):
            job.schedule_removal()
    scan_running = False
    await update.message.reply_text("⛔ 自動スキャンを停止しました。")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_text = (
        f"⚙️ 現在の設定\n"
        f"\n"
        f"📦 MCレンジ:     ${config.MC_MIN:,.0f} 〜 ${config.MC_MAX:,.0f}\n"
        f"🎲 スキャン対象: MCレンジ内からランダム10件\n"
        f"🎯 通知閾値:     {notify_threshold}点以上\n"
        f"⏱️ スキャン間隔: "
        f"{'%d分' % (scan_interval // 60) if scan_interval % 60 == 0 else '%d秒' % scan_interval}"
        f" ({scan_interval}秒)\n"
        f"🔄 自動スキャン: {'稼働中 ✅' if scan_running else '停止中 ⛔'}\n"
        f"⏰ 最終スキャン: {last_scan_time} JST"
    )
    await update.message.reply_text(status_text)


async def cmd_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global notify_threshold
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("❌ 使い方: /threshold <点数>  例: /threshold 65")
        return

    val = int(args[0])
    if not (0 <= val <= 100):
        await update.message.reply_text("❌ 0〜100の範囲で指定してください。")
        return

    notify_threshold = val
    await update.message.reply_text(f"✅ 通知閾値を {notify_threshold}点 に変更しました。")


async def cmd_logsummary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = tracker.get_summary()
    if s.get("total", 0) == 0:
        await update.message.reply_text(
            "📋 ログにまだデータがありません。\n/start でスキャンを開始してください。"
        )
        return

    msg = (
        f"📊 シグナルログ サマリー\n"
        f"\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📝 総記録数:        {s['total']}件\n"
        f"⏳ 未確認(OPEN):    {s['open']}件\n"
        f"✅ 確認済み:        {s['resolved']}件\n"
        f"  🏆 WIN/WIN+:     {s['wins']}件\n"
        f"  💀 LOSS/LOSS-:   {s['losses']}件\n"
        f"  📈 勝率:          {s['win_rate']}%\n"
        f"\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📣 通知済みシグナル: {s['notified']}件\n"
        f"  確認済み:         {s['notified_resolved']}件\n"
        f"  通知後の勝率:     {s['notified_win_rate']}%\n"
        f"\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📊 平均スコア:      {s['avg_score']}点\n"
        f"📈 平均損益率:      {s['avg_pnl']:+.2f}%\n"
        f"\n"
        f"💾 ログファイル:\n"
        f"  signal_log.csv\n"
        f"\n"
        f"📎 Claude に最適設定を分析させる方法:\n"
        f"  signal_log.csv を Claude に添付して\n"
        f"  「最適なconfig設定を提案して」と送る"
    )
    await update.message.reply_text(msg)


async def cmd_setinterval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global scan_interval, scan_running
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ 使い方: /setinterval <秒数 または 分mで指定>\n"
            "  例: /setinterval 300     （300秒）\n"
            "  例: /setinterval 5m      （5分）\n"
            f"  最低値: 60秒"
        )
        return

    raw = args[0].lower().strip()
    try:
        if raw.endswith("m"):
            seconds = int(float(raw[:-1]) * 60)
        else:
            seconds = int(raw)
    except ValueError:
        await update.message.reply_text("❌ 数値の形式が不正です。例: /setinterval 300 または /setinterval 5m")
        return

    if seconds < 60:
        await update.message.reply_text("❌ スキャン間隔は60秒以上に設定してください。")
        return

    scan_interval = seconds
    interval_disp = f"{seconds // 60}分" if seconds % 60 == 0 else f"{seconds}秒"

    # 自動スキャンが稼働中なら即座にジョブを再登録
    if scan_running:
        for job in context.job_queue.get_jobs_by_name("auto_scan"):
            job.schedule_removal()
        context.job_queue.run_repeating(
            run_scan,
            interval=scan_interval,
            first=scan_interval,  # 現在のスキャン完了を待って次のサイクルから
            name="auto_scan",
        )
        await update.message.reply_text(
            f"✅ スキャン間隔を {interval_disp} に変更しました\n"
            f"次のスキャンは {interval_disp} 後に実行されます。"
        )
    else:
        await update.message.reply_text(
            f"✅ スキャン間隔を {interval_disp} に変更しました\n"
            f"/start で自動スキャンを開始すると反映されます。"
        )


async def cmd_setmc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 2:
        await update.message.reply_text(
            "❌ 使い方: /setmc <最小> <最大>\n"
            "例: /setmc 500K 50M\n"
            "例: /setmc 1000000 30000000"
        )
        return

    def parse_value(s: str) -> float | None:
        s = s.upper().strip()
        try:
            if s.endswith("M"):
                return float(s[:-1]) * 1_000_000
            elif s.endswith("K"):
                return float(s[:-1]) * 1_000
            else:
                return float(s)
        except ValueError:
            return None

    mc_min = parse_value(args[0])
    mc_max = parse_value(args[1])

    if mc_min is None or mc_max is None:
        await update.message.reply_text("❌ 数値の形式が不正です。例: /setmc 500K 50M")
        return

    if mc_min >= mc_max:
        await update.message.reply_text("❌ 最小値は最大値より小さくしてください。")
        return

    if mc_min < 0:
        await update.message.reply_text("❌ 負の値は指定できません。")
        return

    config.MC_MIN = mc_min
    config.MC_MAX = mc_max

    await update.message.reply_text(
        f"✅ MCレンジを更新しました\n"
        f"📦 最小: ${mc_min:,.0f}\n"
        f"📦 最大: ${mc_max:,.0f}\n"
        f"次回スキャンから反映されます。"
    )


# ── 毎日ログコミットジョブ ────────────────────────────────────────
async def daily_log_commit_job(context: ContextTypes.DEFAULT_TYPE):
    """毎日 0:00 JST に signal_log.csv を GitHub の logs ブランチへコミットする"""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "commit_logs.sh")
    try:
        result = subprocess.run(
            ["bash", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            msg = result.stdout.strip()
            logger.info(f"[log_commit] {msg}")
            await context.bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=f"📊 signal_log.csv を GitHub (logs ブランチ) にコミットしました\n{msg}",
            )
        else:
            err = result.stderr.strip()
            logger.error(f"[log_commit] コミット失敗: {err}")
            await context.bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=f"⚠️ signal_log.csv のコミットに失敗しました\n{err}",
            )
    except Exception as e:
        logger.error(f"[log_commit] コミットエラー: {e}")


# ── 起動時フック ─────────────────────────────────────────────────
async def on_startup(app: Application) -> None:
    """Bot 起動直後に Telegram へヘルプメッセージを送信する"""
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    greeting = f"✅ Bot が起動しました（{ts} JST）\n\n" + get_help_text()
    await app.bot.send_message(chat_id=config.TELEGRAM_CHAT_ID, text=greeting)
    logger.info("起動通知を送信しました")


# ── エントリーポイント ────────────────────────────────────────────
def main():
    if not config.TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN が設定されていません。.env を確認してください。")
    if not config.TELEGRAM_CHAT_ID:
        raise ValueError("TELEGRAM_CHAT_ID が設定されていません。.env を確認してください。")

    app = (
        Application.builder()
        .token(config.TELEGRAM_TOKEN)
        .post_init(on_startup)
        .build()
    )

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("scan",        cmd_scan))
    app.add_handler(CommandHandler("stop",        cmd_stop))
    app.add_handler(CommandHandler("status",      cmd_status))
    app.add_handler(CommandHandler("threshold",   cmd_threshold))
    app.add_handler(CommandHandler("setmc",       cmd_setmc))
    app.add_handler(CommandHandler("setinterval", cmd_setinterval))
    app.add_handler(CommandHandler("logsummary",  cmd_logsummary))
    app.add_handler(CommandHandler("help",        cmd_help))

    # 毎日 0:00 JST に signal_log.csv を logs ブランチへコミット
    app.job_queue.run_daily(
        daily_log_commit_job,
        time=dtime(hour=0, minute=0, tzinfo=JST),
        name="daily_log_commit",
    )

    logger.info("Bot起動")
    app.run_polling()


if __name__ == "__main__":
    main()
