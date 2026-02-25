#!/bin/bash
# =============================================================================
#  commit_logs.sh
#  signal_log.csv を GitHub の logs ブランチに自動コミットするスクリプト
#
#  使い方:
#    引数なし  : logs/signal_log.csv を signal_log.csv としてコミット（毎日自動用）
#    引数あり  : logs/<ファイル名> をそのままlogsブランチルートに追加コミット（設定変更時）
#               例: bash commit_logs.sh signal_log_until_20260225_172345.csv
#
#  ブランチ切り替えは一切行わないため、ボット稼働中でも安全に実行できます。
#  bot.py の毎日0時JST ジョブから自動呼び出しされます。
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
LOGS_DIR="$SCRIPT_DIR/logs"

# リモートの logs ブランチを取得（初回または更新がある場合）
cd "$REPO_DIR"
git fetch origin logs:logs 2>/dev/null || true

# 親コミットを取得（logs ブランチがすでに存在する場合）
PARENT=$(git rev-parse --verify refs/heads/logs 2>/dev/null || echo "")

DATE=$(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M JST')

if [ -z "$1" ]; then
    # ─── 引数なし: 毎日の自動コミット ───────────────────────────────
    LOG_FILE="$LOGS_DIR/signal_log.csv"

    if [ ! -f "$LOG_FILE" ]; then
        echo "logs/signal_log.csv が見つかりません。スキップします。"
        exit 0
    fi

    BLOB=$(git hash-object -w "$LOG_FILE")

    if [ -n "$PARENT" ]; then
        # 既存のツリーを取得して signal_log.csv だけ上書き
        BASE_TREE=$(git cat-file -p "$PARENT^{tree}" | grep -v "signal_log" | awk '{printf "%s %s %s\t%s\n", $1, $2, $3, $4}')
        NEW_ENTRY=$(printf "100644 blob %s\tsignal_log.csv" "$BLOB")
        TREE=$(printf "%s\n%s\n" "$BASE_TREE" "$NEW_ENTRY" | git mktree)
    else
        TREE=$(printf "100644 blob %s\tsignal_log.csv\n" "$BLOB" | git mktree)
    fi

    if [ -n "$PARENT" ]; then
        COMMIT=$(git commit-tree "$TREE" -p "$PARENT" -m "log: $DATE signal_log.csv 自動コミット")
    else
        COMMIT=$(git commit-tree "$TREE" -m "log: $DATE signal_log.csv 自動コミット（初回）")
    fi

    git update-ref refs/heads/logs "$COMMIT"
    git push origin logs
    echo "✅ signal_log.csv を logs ブランチにコミットしました（$DATE）"

else
    # ─── 引数あり: アーカイブファイルの追加コミット ─────────────────
    ARCHIVE_NAME="$1"
    ARCHIVE_FILE="$LOGS_DIR/$ARCHIVE_NAME"

    if [ ! -f "$ARCHIVE_FILE" ]; then
        echo "エラー: $ARCHIVE_FILE が見つかりません。"
        exit 1
    fi

    BLOB=$(git hash-object -w "$ARCHIVE_FILE")

    if [ -n "$PARENT" ]; then
        # 既存のツリーを取得してアーカイブファイルを追加
        BASE_TREE=$(git cat-file -p "$PARENT^{tree}" | awk '{printf "%s %s %s\t%s\n", $1, $2, $3, $4}')
        NEW_ENTRY=$(printf "100644 blob %s\t%s" "$BLOB" "$ARCHIVE_NAME")
        TREE=$(printf "%s\n%s\n" "$BASE_TREE" "$NEW_ENTRY" | git mktree)
        COMMIT=$(git commit-tree "$TREE" -p "$PARENT" -m "log: $DATE $ARCHIVE_NAME アーカイブ")
    else
        TREE=$(printf "100644 blob %s\t%s\n" "$BLOB" "$ARCHIVE_NAME" | git mktree)
        COMMIT=$(git commit-tree "$TREE" -m "log: $DATE $ARCHIVE_NAME アーカイブ（初回）")
    fi

    git update-ref refs/heads/logs "$COMMIT"
    git push origin logs
    echo "✅ $ARCHIVE_NAME を logs ブランチにコミットしました（$DATE）"
fi
