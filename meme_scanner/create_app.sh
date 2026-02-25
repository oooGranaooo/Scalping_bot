#!/bin/bash
# ================================================================
#  create_app.sh
#  meme_scanner.app（macOS .app バンドル）を生成する
# ================================================================
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
APP_PATH="$DIR/meme_scanner.app"

echo "=============================="
echo "  meme_scanner.app を生成中"
echo "=============================="

# 既存を削除してクリーンに作り直す
rm -rf "$APP_PATH"

# バンドルディレクトリ構造を作成
mkdir -p "$APP_PATH/Contents/MacOS"
mkdir -p "$APP_PATH/Contents/Resources"

# ── Info.plist ────────────────────────────────────────────────
cat > "$APP_PATH/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>meme_scanner</string>
    <key>CFBundleDisplayName</key>
    <string>Meme Scanner Bot</string>
    <key>CFBundleExecutable</key>
    <string>meme_scanner</string>
    <key>CFBundleIdentifier</key>
    <string>com.local.meme-scanner</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
</dict>
</plist>
PLIST
echo "[OK] Info.plist"

# ── 実行ファイルを Python で生成（シェルのクォート問題を回避） ──
python3 - "$DIR" "$APP_PATH" << 'PYEOF'
import sys, os, stat

project_dir = sys.argv[1]
app_path    = sys.argv[2]
exec_path   = os.path.join(app_path, "Contents", "MacOS", "meme_scanner")
start_sh    = os.path.join(project_dir, "start.sh")

# AppleScript のヒアドキュメントを含むシェルスクリプトを生成
# $START_SH はシェル変数として実行時に参照される（Python展開なし）
content = f"""#!/bin/bash
# meme_scanner.app 実行ファイル
# ダブルクリックすると Terminal を開いて start.sh を起動する

START_SH="{start_sh}"

if [ ! -f "$START_SH" ]; then
    osascript -e 'display alert "起動エラー" message "start.sh が見つかりません。\\nプロジェクトを移動した場合は create_app.sh を再実行してください。" as critical'
    exit 1
fi

# Terminal を開いて start.sh を実行する
osascript - "$START_SH" << 'APPLESCRIPT'
on run argv
    set startSh to item 1 of argv
    tell application "Terminal"
        activate
        do script "bash " & quoted form of startSh
    end tell
end run
APPLESCRIPT
"""

with open(exec_path, "w") as f:
    f.write(content)

os.chmod(exec_path,
    stat.S_IRWXU |          # rwx for owner
    stat.S_IRGRP | stat.S_IXGRP |  # r-x for group
    stat.S_IROTH | stat.S_IXOTH    # r-x for others
)
print(f"[OK] 実行ファイル: {exec_path}")
PYEOF

# ── アイコン生成（Python + sips で .icns を作る） ──────────────
python3 - "$APP_PATH" << 'PYEOF'
import sys, os, subprocess, tempfile, shutil

app_path     = sys.argv[1]
iconset_dir  = os.path.join(app_path, "Contents", "Resources", "AppIcon.iconset")
icns_out     = os.path.join(app_path, "Contents", "Resources", "AppIcon.icns")

os.makedirs(iconset_dir, exist_ok=True)

# Python の Pillow が使えなくてもよいように sips + Python で PNG を生成
# SVG 的な内容をシェルで作るのは難しいので、
# まず 1024×1024 の PNG を sips でリサイズして各サイズを作る
# ここでは簡易的に tiff → icns 経路を使う

# iconutil が使えるか確認
if not shutil.which("iconutil"):
    print("[SKIP] iconutil が見つかりません。デフォルトアイコンを使用します。")
    sys.exit(0)

# Python で最小限の PNG バイナリを生成（緑背景に白文字）
try:
    import struct, zlib

    def make_png(size):
        """指定サイズの PNG を bytes で返す（緑背景 + 白テキスト省略版）"""
        w = h = size
        # 各行: フィルタバイト(0) + RGBA×w
        bg   = (30, 30, 30, 255)   # ダーク背景
        rows = []
        for y in range(h):
            row = b"\x00"  # filter byte
            for x in range(w):
                cx, cy = x - w // 2, y - h // 2
                r = min(w, h) * 0.42
                ri = min(w, h) * 0.35
                dist = (cx**2 + cy**2) ** 0.5
                # 外縁の円（緑）
                if ri < dist <= r:
                    row += bytes([0, 200, 80, 255])
                # 内側（ダーク）
                elif dist <= ri:
                    row += bytes([20, 20, 20, 255])
                else:
                    row += bytes(bg)
            rows.append(row)

        raw    = b"".join(rows)
        def chunk(tag, data):
            c = struct.pack(">I", len(data)) + tag + data
            return c + struct.pack(">I", zlib.crc32(c[4:]) & 0xFFFFFFFF)

        png  = b"\x89PNG\r\n\x1a\n"
        ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        idat = chunk(b"IDAT", zlib.compress(raw))
        iend = chunk(b"IEND", b"")
        return png + ihdr + idat + iend

    sizes = [16, 32, 64, 128, 256, 512, 1024]
    for s in sizes:
        fname = f"icon_{s}x{s}.png"
        with open(os.path.join(iconset_dir, fname), "wb") as f:
            f.write(make_png(s))
        # @2x（Retina）
        if s <= 512:
            fname2x = f"icon_{s}x{s}@2x.png"
            with open(os.path.join(iconset_dir, fname2x), "wb") as f:
                f.write(make_png(s * 2))

    subprocess.run(
        ["iconutil", "-c", "icns", iconset_dir, "-o", icns_out],
        check=True
    )
    shutil.rmtree(iconset_dir)
    print(f"[OK] アイコン: {icns_out}")

except Exception as e:
    print(f"[SKIP] アイコン生成をスキップ: {e}")
    shutil.rmtree(iconset_dir, ignore_errors=True)
PYEOF

# アイコンを Info.plist に登録（icns が存在する場合のみ）
if [ -f "$APP_PATH/Contents/Resources/AppIcon.icns" ]; then
    python3 - "$APP_PATH" << 'PYEOF'
import sys, re

app_path  = sys.argv[1]
plist     = app_path + "/Contents/Info.plist"

with open(plist, "r") as f:
    content = f.read()

# CFBundleIconFile を追記（すでになければ）
if "CFBundleIconFile" not in content:
    insert = "    <key>CFBundleIconFile</key>\n    <string>AppIcon</string>\n"
    content = content.replace("</dict>", insert + "</dict>")
    with open(plist, "w") as f:
        f.write(content)
    print("[OK] アイコンを Info.plist に登録")
PYEOF
fi

# macOS に .app として認識させる
touch "$APP_PATH"

echo ""
echo "=============================="
echo "  ✅ 作成完了！"
echo "=============================="
echo ""
echo "  $APP_PATH"
echo ""
echo "使い方:"
echo "  1. Finder で meme_scanner.app をダブルクリック"
echo "     → Terminal が開きログをリアルタイムで確認できます"
echo ""
echo "  ※ 初回起動時に「開発元を確認できない」と表示された場合:"
echo "     右クリック → 開く → 開く"
echo "     または: xattr -cr \"$APP_PATH\""
echo ""
