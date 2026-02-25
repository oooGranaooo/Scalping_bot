#!/bin/bash
# =============================================================================
#  commit_logs.sh
#  signal_log.csv を GitHub の logs ブランチに自動コミットするスクリプト
#
#  ブランチ切り替えは一切行わないため、ボット稼働中でも安全に実行できます。
#  bot.py の毎日0時JST ジョブから自動呼び出しされます。
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
LOG_FILE="$SCRIPT_DIR/signal_log.csv"

# signal_log.csv が存在しない場合はスキップ
if [ ! -f "$LOG_FILE" ]; then
    echo "signal_log.csv が見つかりません。スキップします。"
    exit 0
fi

cd "$REPO_DIR"

# リモートの logs ブランチを取得（初回または更新がある場合）
git fetch origin logs:logs 2>/dev/null || true

# signal_log.csv を git オブジェクトストアに追加し BLOB ハッシュを取得
BLOB=$(git hash-object -w "$LOG_FILE")

# ツリーオブジェクトを作成（logs ブランチには signal_log.csv だけ置く）
# printf で LF を確実に出力するために %b を使用
TREE=$(printf "100644 blob %s\tsignal_log.csv\n" "$BLOB" | git mktree)

# 親コミットを取得（logs ブランチがすでに存在する場合）
PARENT=$(git rev-parse --verify refs/heads/logs 2>/dev/null || echo "")

# コミットオブジェクトを作成
DATE=$(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M JST')
if [ -n "$PARENT" ]; then
    COMMIT=$(git commit-tree "$TREE" -p "$PARENT" -m "log: $DATE signal_log.csv 自動コミット")
else
    COMMIT=$(git commit-tree "$TREE" -m "log: $DATE signal_log.csv 自動コミット（初回）")
fi

# ローカルの logs ブランチポインタを更新
git update-ref refs/heads/logs "$COMMIT"

# GitHub へプッシュ
git push origin logs

echo "✅ signal_log.csv を logs ブランチにコミットしました（$DATE）"
