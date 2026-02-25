#!/bin/bash
# ================================================================
#  meme_scanner セットアップスクリプト（初回のみ実行）
# ================================================================
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

VENV_DIR="$DIR/.venv"

echo "=============================="
echo "  meme_scanner セットアップ"
echo "=============================="

# Python3 の確認
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 が見つかりません。インストールしてください。"
    exit 1
fi
echo "[OK] Python: $(python3 --version)"

# 仮想環境の作成
if [ ! -d "$VENV_DIR" ]; then
    echo "[INFO] 仮想環境を作成中..."
    python3 -m venv "$VENV_DIR"
    echo "[OK] 仮想環境を作成しました: $VENV_DIR"
else
    echo "[SKIP] 仮想環境はすでに存在します"
fi

# 依存パッケージのインストール
echo "[INFO] 依存パッケージをインストール中..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$DIR/requirements.txt"
echo "[OK] パッケージのインストール完了"

# .env の作成
if [ ! -f "$DIR/.env" ]; then
    cp "$DIR/.env.example" "$DIR/.env"
    echo ""
    echo "[!] .env ファイルを作成しました。"
    echo "    以下の値を設定してから bot を起動してください:"
    echo "    $DIR/.env"
    echo ""
    echo "      TELEGRAM_TOKEN=your_bot_token_here"
    echo "      TELEGRAM_CHAT_ID=your_chat_id_here"
else
    echo "[SKIP] .env はすでに存在します"
fi

# start.sh に実行権限を付与
chmod +x "$DIR/start.sh" 2>/dev/null || true
chmod +x "$DIR/meme_scanner.command" 2>/dev/null || true

echo ""
echo "=============================="
echo "  セットアップ完了！"
echo "=============================="
echo ""
echo "次のステップ:"
echo "  1. $DIR/.env を編集して TELEGRAM_TOKEN と TELEGRAM_CHAT_ID を設定"
echo "  2. ./start.sh でBotを起動（または meme_scanner.command をダブルクリック）"
echo ""
