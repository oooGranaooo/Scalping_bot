"""
config_editor.py
================
Meme Scanner 設定エディタ（インタラクティブ CLI）

使い方:
    bash edit_config.sh

機能:
    - MC帯パラメータをターミナルから対話的に変更
    - 変更保存時に signal_log.csv を自動アーカイブ（rotate）
    - アーカイブを logs ブランチへ自動コミット
    - Botをシャットダウンして自動再起動（.bot.pid 経由）
"""
from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

# ── パス定義 ──────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.py")
PID_FILE    = os.path.join(SCRIPT_DIR, ".bot.pid")
COMMIT_SH   = os.path.join(SCRIPT_DIR, "commit_logs.sh")

JST = timezone(timedelta(hours=9))


# ══════════════════════════════════════════════════════════════
#  現在の設定値を config.py から読み込む
# ══════════════════════════════════════════════════════════════

def _load_config() -> dict:
    """config.py を exec して現在値を返す。"""
    ns: dict = {}
    with open(CONFIG_FILE, encoding="utf-8") as f:
        exec(f.read(), ns)  # noqa: S102
    return ns


# ══════════════════════════════════════════════════════════════
#  画面表示
# ══════════════════════════════════════════════════════════════

def _fmt_mc(val: float) -> str:
    if val >= 1_000_000:
        return f"${val / 1_000_000:.0f}M"
    elif val >= 1_000:
        return f"${val / 1_000:.0f}K"
    return f"${val:.0f}"


def _show_menu(cfg: dict):
    bands = cfg["MC_BAND_PARAMS"]
    print()
    print("=== Meme Scanner 設定エディタ ===")
    print()
    print("[基本フィルター（表示のみ）]")
    print(f"  MCレンジ:    {_fmt_mc(cfg['MC_MIN'])} 〜 {_fmt_mc(cfg['MC_MAX'])}")
    print(f"  最低流動性:  {_fmt_mc(cfg['LIQ_MIN'])}")
    print(f"  通知閾値:    {cfg['NOTIFY_THRESHOLD']}点")
    print()
    print("[MC帯パラメータ]")
    for i, b in enumerate(bands, start=1):
        label = f"帯{i} ({_fmt_mc(b['mc_min'])}〜{_fmt_mc(b['mc_max'])})"
        print(
            f"  {i}. {label}:  "
            f"RSI={b['rsi_overbought']}  "
            f"SL×{b['atr_sl_mult']}  "
            f"TP×{b['atr_tp_mult']}  "
            f"Vol×{b['volume_surge_min']}"
        )
    print()


# ══════════════════════════════════════════════════════════════
#  入力ユーティリティ
# ══════════════════════════════════════════════════════════════

def _input_float(prompt: str, current: float, min_val: float | None = None, max_val: float | None = None) -> float | None:
    """現在値を表示しながら新値を入力させる。Enterのみでキャンセル（None返却）。"""
    raw = input(f"  {prompt} [現在: {current}] > ").strip()
    if not raw:
        return None
    try:
        val = float(raw)
    except ValueError:
        print("  ❌ 数値を入力してください。")
        return None
    if min_val is not None and val < min_val:
        print(f"  ❌ {min_val} 以上の値を入力してください。")
        return None
    if max_val is not None and val > max_val:
        print(f"  ❌ {max_val} 以下の値を入力してください。")
        return None
    return val


def _input_int(prompt: str, current: int, min_val: int | None = None, max_val: int | None = None) -> int | None:
    raw = input(f"  {prompt} [現在: {current}] > ").strip()
    if not raw:
        return None
    try:
        val = int(raw)
    except ValueError:
        print("  ❌ 整数を入力してください。")
        return None
    if min_val is not None and val < min_val:
        print(f"  ❌ {min_val} 以上の値を入力してください。")
        return None
    if max_val is not None and val > max_val:
        print(f"  ❌ {max_val} 以下の値を入力してください。")
        return None
    return val


# ══════════════════════════════════════════════════════════════
#  各メニュー項目の編集処理
# ══════════════════════════════════════════════════════════════

def _edit_band(band_idx: int, cfg: dict, changes: dict):
    b = cfg["MC_BAND_PARAMS"][band_idx]
    label = f"帯{band_idx + 1} ({_fmt_mc(b['mc_min'])}〜{_fmt_mc(b['mc_max'])})"
    print(f"  --- {label} ---")

    key_prefix = f"MC_BAND_PARAMS[{band_idx}]"
    changed = False

    val = _input_int("RSI過熱閾値（70〜99）", b["rsi_overbought"], 70, 99)
    if val is not None:
        changes.setdefault("bands", {})[f"{band_idx}.rsi_overbought"] = val
        print(f"  → rsi_overbought = {val}")
        changed = True

    val = _input_float("損切り倍率 atr_sl_mult（0.5〜10.0）", b["atr_sl_mult"], 0.5, 10.0)
    if val is not None:
        changes.setdefault("bands", {})[f"{band_idx}.atr_sl_mult"] = val
        print(f"  → atr_sl_mult = {val}")
        changed = True

    val = _input_float("利確倍率 atr_tp_mult（0.5〜20.0）", b["atr_tp_mult"], 0.5, 20.0)
    if val is not None:
        changes.setdefault("bands", {})[f"{band_idx}.atr_tp_mult"] = val
        print(f"  → atr_tp_mult = {val}")
        changed = True

    val = _input_float("出来高閾値 volume_surge_min（1.0〜20.0）", b["volume_surge_min"], 1.0, 20.0)
    if val is not None:
        changes.setdefault("bands", {})[f"{band_idx}.volume_surge_min"] = val
        print(f"  → volume_surge_min = {val}")
        changed = True

    if not changed:
        print("  （変更なし）")


# ══════════════════════════════════════════════════════════════
#  config.py への書き込み
# ══════════════════════════════════════════════════════════════

def _apply_changes(changes: dict, cfg: dict):
    """変更を config.py に書き込む。"""
    with open(CONFIG_FILE, encoding="utf-8") as f:
        src = f.read()

    # MC_BAND_PARAMS のブロック全体を再生成して置換
    if "bands" in changes:
        # 既存パラメータをベースに変更を適用
        bands = cfg["MC_BAND_PARAMS"]
        for key, val in changes["bands"].items():
            band_idx_str, param = key.split(".", 1)
            idx = int(band_idx_str)
            bands[idx][param] = val

        # ブロックを再生成（コメント行・元の書式は破棄、基本構造を維持）
        new_block = _render_band_params(bands)

        # 既存の MC_BAND_PARAMS = [ ... ] ブロックを置換
        src = re.sub(
            r"MC_BAND_PARAMS\s*=\s*\[.*?\]",
            new_block,
            src,
            flags=re.DOTALL,
        )

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(src)


def _render_band_params(bands: list[dict]) -> str:
    """MC_BAND_PARAMS リストを Python ソースとして文字列化する。"""
    band_labels = [
        "帯1: $300K〜$1M（超マイクロキャップ）",
        "帯2: $1M〜$5M（スモールキャップ）",
        "帯3: $5M〜$50M（ミッドキャップ）",
    ]
    lines = ["MC_BAND_PARAMS = ["]
    for i, b in enumerate(bands):
        label = band_labels[i] if i < len(band_labels) else f"帯{i + 1}"
        lines.append(f"    # {label}")
        lines.append("    {")
        lines.append(f"        \"mc_min\":          {int(b['mc_min'])},")
        lines.append(f"        \"mc_max\":        {int(b['mc_max'])},")
        lines.append(f"        \"rsi_overbought\":       {int(b['rsi_overbought'])},")
        lines.append(f"        \"atr_sl_mult\":         {b['atr_sl_mult']},")
        lines.append(f"        \"atr_tp_mult\":         {b['atr_tp_mult']},")
        lines.append(f"        \"volume_surge_min\":    {b['volume_surge_min']},")
        lines.append(f"        \"ohlcv_aggregate\":       {int(b['ohlcv_aggregate'])},")
        lines.append("    },")
    lines.append("]")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  Bot 再起動
# ══════════════════════════════════════════════════════════════

def _restart_bot():
    """PID ファイルで動いている Bot を停止して再起動する。"""
    # Bot の停止
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            print(f"  Bot (PID={pid}) を停止しています...")
            os.kill(pid, signal.SIGTERM)
            # 最大 10 秒待つ
            for _ in range(20):
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)  # プロセスが生きているか確認
                except ProcessLookupError:
                    break
            else:
                print("  ⚠️  Botの停止を確認できませんでした。手動で確認してください。")
                return
        except (ValueError, ProcessLookupError, PermissionError) as e:
            print(f"  ⚠️  Bot停止に失敗: {e}")
    else:
        print("  .bot.pid が見つかりません。Botは起動していないか、手動で起動してください。")

    # Bot の再起動（新しいターミナルウィンドウで起動）
    bot_script  = os.path.join(SCRIPT_DIR, "bot.py")
    venv_python = os.path.join(SCRIPT_DIR, ".venv", "bin", "python3")
    python_exe  = venv_python if os.path.exists(venv_python) else sys.executable

    print("  Botを新しいターミナルで再起動しています...")
    cmd = f"cd {shlex.quote(SCRIPT_DIR)} && {shlex.quote(python_exe)} {shlex.quote(bot_script)}"
    apple_script = f'tell application "Terminal" to do script "{cmd}"'
    subprocess.Popen(["osascript", "-e", apple_script])
    time.sleep(2)
    if os.path.exists(PID_FILE):
        print("  ✅ Botを新しいターミナルで再起動しました。")
    else:
        print("  ⚠️  Botの起動を確認できませんでした。新しいターミナルウィンドウを確認してください。")


# ══════════════════════════════════════════════════════════════
#  メインループ
# ══════════════════════════════════════════════════════════════

def main():
    changes: dict = {}

    while True:
        cfg = _load_config()
        _show_menu(cfg)

        raw = input("番号を選択 (Enterで終了・保存): ").strip()

        if not raw:
            # 保存フロー
            if not changes:
                print("変更がありません。終了します。")
                break

            print()
            print("以下の変更を保存します:")
            if "bands" in changes:
                for key, val in changes["bands"].items():
                    idx_str, param = key.split(".", 1)
                    old_val = cfg["MC_BAND_PARAMS"][int(idx_str)][param]
                    print(f"  帯{int(idx_str) + 1} {param}: {old_val} → {val}")

            confirm = input("保存しますか？ (y/N): ").strip().lower()
            if confirm != "y":
                print("キャンセルしました。")
                break

            # config.py 書き換え
            _apply_changes(changes, cfg)
            print("✅ 設定を保存しました")

            # signal_log.csv をアーカイブ
            # tracker を直接 import して rotate_log を呼ぶ
            sys.path.insert(0, SCRIPT_DIR)
            import tracker  # noqa: PLC0415
            archived = tracker.rotate_log()

            if archived:
                archive_name = os.path.basename(archived)
                print(f"📊 新しいログファイル: signal_log.csv (旧: {archive_name})")

                # アーカイブを logs ブランチへコミット
                try:
                    result = subprocess.run(
                        ["bash", COMMIT_SH, archive_name],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        cwd=SCRIPT_DIR,
                    )
                    if result.returncode == 0:
                        print(f"  {result.stdout.strip()}")
                    else:
                        print(f"  ⚠️  コミット失敗: {result.stderr.strip()}")
                except Exception as e:
                    print(f"  ⚠️  コミットエラー: {e}")
            else:
                print("📊 新しい signal_log.csv を作成しました（旧ファイルなし）")

            # Bot 再起動
            _restart_bot()
            break

        # 番号選択
        try:
            choice = int(raw)
        except ValueError:
            print("❌ 番号を入力してください。")
            continue

        if choice == 1:
            _edit_band(0, cfg, changes)
        elif choice == 2:
            _edit_band(1, cfg, changes)
        elif choice == 3:
            _edit_band(2, cfg, changes)
        else:
            print("❌ 有効な番号を選択してください（1〜3）。")


if __name__ == "__main__":
    main()
