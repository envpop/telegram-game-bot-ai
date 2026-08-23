# -*- coding: utf-8 -*-
"""
triggers/sakura_strategy.py —— 「櫻花綻放的圓舞曲」自動連刷。

跟其他 announcement 策略（world_boss_strategy）介面一致：
    load_catalog(base_dir) -> dict
    decide_action(text, catalog, base_dir, account_id) -> {"mode": ...}
掛在 main.py 的 announcement_strategies 清單裡，公告頻道任何人施放櫻花
（不限熊自己，全員共用同一個 5 分鐘窗口）都會觸發。

=== 設計方式：排程 + 全域暫停，不讀回合結果 ===
櫻花窗口的效果是「接下來 5 分鐘『挑戰』無冷卻」，玩法是觸發當下就決定
「打哪種塔、多快打」，然後一口氣排定一長串重複指令，中間每一關的回合
結果（打了誰、掉了什麼）完全不影響後續要不要繼續打——跟主塔戰鬥那種
「要看戰況才能決定下一步」的觸發完全不同性質。所以不需要新增 shape
去解析每一關的回合結果，回合結果訊息維持現狀「尚未分類」即可。

排程用 scheduler.py 既有的 ScheduledJob（repeat + interval），不是自己
另外兜一套機制——這樣跟 /sched 系統保持一致，而且天生可取消：真的遇到
lag 或其他異常，直接 /sched list 找到 job_id、/sched cancel 就能緊急
停止，比一次性發完不能回頭的做法保險（熊 2026-08-22 指定）。

觸發當下同時會透過 runtime_state.set_until() 設一個 5 分鐘的全域暫停
窗口——這 5 分鐘內遇到護衛／世界王這類會主動送指令的觸發模組，很容易
跟這裡排定的連刷指令互相干擾（熊 2026-08-22 反映會造成錯誤），所以窗口
期間 action_dispatcher.py 的 dispatch() 跟 _handle_announcement() 都會
整批跳過自動判斷。profile_sync 不受影響（純資料同步，沒有主動送指令的
風險）。

窗口結束的公告（「櫻花窗口結束,挑戰恢復冷卻」）不用特別處理——排定的
指令數量本來就是照 5 分鐘窗口反推出來的，暫停窗口跟連刷指令是同時設定、
同時到期，多送幾次頂多在視窗剛結束那幾秒撲空（正常冷卻擋下，不會出錯）。

=== 模式設定：指令種類 × 速度，2×3 共 6 種情境 ===
指令種類：連續活動塔／連續進階活動塔（熊 2026-08-22 提供，都是遊戲既有
的「一次自動打完 8 關」指令，不是這次新發明的）。
速度：快／中／慢，熊 2026-08-22 給的實測基準是 5 分鐘窗口內：
    慢速 ≈ 80 次、中速 ≈ 140 次、快速 ≈ 200 次
兩個維度獨立設定，落地存在 data/common/sakura_mode.json，用
set_mode()／load_mode() 讀寫，觸發當下讀「目前設定」決定要送幾次、
多快送——不用每次觸發都重新決定，熊可以事先設好，之後每次有人放
櫻花就照設定跑。
"""
import json
import time
from pathlib import Path

from triggers import runtime_state

SYSTEM_KEY = "sakura_auto_challenge"

TRIGGER_PATTERN = "施放了「櫻花綻放的圓舞曲」"

WINDOW_SECONDS = 300  # 5 分鐘

# 熊 2026-08-22 提供的實測基準：5 分鐘窗口大概能塞進去的指令數。
# suspend_triggers：這個速度期間要不要暫停其他自動觸發（見 decide_action
# 說明）。慢速間隔夠鬆（≈3.75 秒一次），熊確認跟護衛／世界王同時運作
# 沒差，不用暫停；中速／快速間隔太密，容易互相干擾，還是要暫停。
SPEED_PRESETS = {
    "slow": {"label": "慢速", "count": 80, "suspend_triggers": False},
    "medium": {"label": "中速", "count": 140, "suspend_triggers": False},
    "fast": {"label": "快速", "count": 200, "suspend_triggers": True},
}

# 指令種類：key 是設定檔／終端機指令用的簡短代號，value 是實際要送出的
# 遊戲指令文字（打「連續活動塔」／「連續進階活動塔」就是遊戲既有指令）。
COMMANDS = {
    "tower": "連續活動塔",
    "advanced_tower": "連續進階活動塔",
}

_MODE_FILENAME = "sakura_mode.json"
_DEFAULT_MODE = {"command": "advanced_tower", "speed": "medium"}


def _mode_path(base_dir) -> Path:
    return Path(base_dir) / "data" / "common" / _MODE_FILENAME


def load_mode(base_dir) -> dict:
    """讀目前設定的指令種類＋速度，檔案不存在或內容壞掉都回傳預設值，
    不會噴例外——這是自動觸發路徑，讀檔失敗不該讓整個 dispatch 掛掉。"""
    path = _mode_path(base_dir)
    if not path.exists():
        return dict(_DEFAULT_MODE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return dict(_DEFAULT_MODE)
    if data.get("command") not in COMMANDS or data.get("speed") not in SPEED_PRESETS:
        return dict(_DEFAULT_MODE)
    return data


def set_mode(base_dir, command_key: str, speed_key: str) -> None:
    """設定指令種類＋速度。command_key 是 COMMANDS 的 key（tower／advanced_tower），
    speed_key 是 SPEED_PRESETS 的 key（slow／medium／fast）。傳錯值直接噴
    ValueError，讓終端機指令那層可以接住印出用法錯誤，不要在這裡吞掉。"""
    if command_key not in COMMANDS:
        raise ValueError(f"未知的指令種類「{command_key}」，可用：{', '.join(COMMANDS)}")
    if speed_key not in SPEED_PRESETS:
        raise ValueError(f"未知的速度「{speed_key}」，可用：{', '.join(SPEED_PRESETS)}")

    path = _mode_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"command": command_key, "speed": speed_key}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def describe_mode(mode: dict) -> str:
    """把設定轉成人看得懂的一行字，終端機指令跟觸發時的 log 共用。"""
    command_text = COMMANDS[mode["command"]]
    preset = SPEED_PRESETS[mode["speed"]]
    count = preset["count"]
    interval = WINDOW_SECONDS / count
    return f"{command_text}／{preset['label']}（{count} 次、間隔 {interval:.2f} 秒）"


def load_catalog(base_dir):
    """跟其他 announcement 模組介面一致（load_catalog + decide_action 兩段式），
    這裡的 catalog 就是目前設定的模式，不是真的「圖鑑」。"""
    return load_mode(base_dir)


def decide_action(text, catalog, base_dir, account_id):
    if TRIGGER_PATTERN not in text:
        return {"mode": None}

    preset = SPEED_PRESETS[catalog["speed"]]

    # 窗口期間全員共用（不限熊自己施放），中速／快速指令間隔太密，很容易
    # 跟護衛／世界王這類會主動送出指令的觸發模組互相干擾（熊 2026-08-22
    # 反映會造成錯誤）；慢速間隔夠鬆，熊確認不用暫停，讓其他 trigger
    # 照常運作。用 runtime_state 設一個全域暫停窗口，跟這次觸發同時生效
    # ——action_dispatcher.py 的 dispatch() 跟 _handle_announcement() 都
    # 會在窗口內略過自動判斷，profile_sync 不受影響（純資料同步，沒有
    # 主動送指令的風險）。
    if preset["suspend_triggers"]:
        runtime_state.set_until("suspend_triggers", None, time.time() + WINDOW_SECONDS)

    command_text = COMMANDS[catalog["command"]]
    count = preset["count"]
    interval = WINDOW_SECONDS / count
    suspend_note = "期間暫停其他自動觸發" if preset["suspend_triggers"] else "其他自動觸發照常運作"

    return {
        "mode": "scheduled",
        "command": command_text,
        "repeat": count,
        "interval": (interval, interval),
        "delay_seconds": 0.0,
        "chat_id": None,
        "reason": f"櫻花窗口開啟，自動連刷：{describe_mode(catalog)}（{suspend_note}）",
    }