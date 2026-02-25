#!/bin/bash
# ================================================================
#  meme_scanner 起動スクリプト
#  ターミナルからログをリアルタイムで確認できます
# ================================================================

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

VENV_DIR="$DIR/.venv"
ENV_FILE="$DIR/.env"

# セットアップ確認
if [ ! -d "$VENV_DIR" ]; then
    echo "[ERROR] 仮想環境が見つかりません。先に setup.sh を実行してください:"
    echo "  bash $DIR/setup.sh"
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "[ERROR] .env ファイルが見つかりません。"
    echo "  cp .env.example .env && vi .env"
    exit 1
fi

# TELEGRAM_TOKEN の未設定チェック
source "$ENV_FILE"
if [ -z "$TELEGRAM_TOKEN" ] || [ "$TELEGRAM_TOKEN" = "your_bot_token_here" ]; then
    echo "[ERROR] TELEGRAM_TOKEN が設定されていません。"
    echo "  $ENV_FILE を編集してください。"
    exit 1
fi
if [ -z "$TELEGRAM_CHAT_ID" ] || [ "$TELEGRAM_CHAT_ID" = "your_chat_id_here" ]; then
    echo "[ERROR] TELEGRAM_CHAT_ID が設定されていません。"
    echo "  $ENV_FILE を編集してください。"
    exit 1
fi

echo "=============================="
echo "  meme_scanner Bot 起動中..."
echo "=============================="
echo ""
echo "  Ctrl+C で停止"
echo "  ログファイル: $DIR/scanner.log"
echo ""
echo "=============================="
echo ""

# 仮想環境のPythonでbotを起動
# ログは標準出力＋scanner.log に同時出力（unbuffered）
exec "$VENV_DIR/bin/python3" -u "$DIR/bot.py"
