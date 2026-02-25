#!/bin/bash
# ================================================================
#  設定エディタ起動スクリプト
#  venv の Python で config_editor.py を実行する
# ================================================================

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "[ERROR] 仮想環境が見つかりません。先に setup.sh を実行してください:"
    echo "  bash $DIR/setup.sh"
    exit 1
fi

exec "$VENV_DIR/bin/python3" "$DIR/config_editor.py"
