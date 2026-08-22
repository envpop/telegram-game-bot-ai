"""
auto_toggle.py —— 統一管理「自動發送／自動點擊」開關的極簡狀態存取層。

以少控多：目前會自動送出動作的系統有三套——主塔戰鬥（自動點戰術按鈕）、
世界王摸王（自動送出攻擊指令）、群星計畫（自動點培育按鈕）。三套的開關
需求完全一樣（開/關、跨重啟保留、預設開啟），所以共用同一份讀寫邏輯跟
同一個狀態檔，不用三個系統各自維護一份幾乎一樣的程式碼。之後如果再新增
第四套會自動發送的系統，不用新增檔案，呼叫端用一個新的 system_key 呼叫
is_enabled()/set_enabled() 就好。

狀態存在 data/common/auto_toggles.json，格式：
    {"main_tower_battle": true, "world_boss": true, "satellite_training": true}
沒有紀錄過的 system_key（或整份檔案不存在）一律視為開啟，不用特別初始化。
"""
import json
from pathlib import Path

_STATE_FILENAME = "auto_toggles.json"

# system_key -> 顯示用中文名稱，供 print 訊息跟終端機指令共用，
# 新增系統時只要在這裡加一行，指令跟提示訊息就會自動吃到。
SYSTEM_KEYS = {
    "main_tower_battle": "主塔戰鬥",
    "world_boss": "世界王摸王",
    "satellite_training": "群星計畫（培育衛星）",
    "guard_clear": "清護衛",
    "satellite_naming": "群星計畫結業命名",
}


def _state_file_path(base_dir):
    return Path(base_dir) / "data" / "common" / _STATE_FILENAME


def _load_state(base_dir):
    path = _state_file_path(base_dir)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def is_enabled(base_dir, system_key: str) -> bool:
    return bool(_load_state(base_dir).get(system_key, True))


def set_enabled(base_dir, system_key: str, enabled: bool) -> None:
    path = _state_file_path(base_dir)
    state = _load_state(base_dir)
    state[system_key] = enabled
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def status_summary(base_dir) -> str:
    """給 /auto（不帶參數）用的查詢輸出，三套系統目前狀態一次列出來。"""
    lines = []
    for key, label in SYSTEM_KEYS.items():
        state = "✅ 開啟" if is_enabled(base_dir, key) else "🔕 關閉"
        lines.append(f"  {label}（{key}）：{state}")
    return "\n".join(lines)