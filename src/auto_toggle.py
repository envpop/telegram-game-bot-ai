"""
auto_toggle.py —— 統一管理「自動發送／自動點擊」開關的極簡狀態存取層，
同時是所有 system_key 字串常數的唯一來源。

以少控多：目前會自動送出動作的系統有三套——主塔戰鬥（自動點戰術按鈕）、
世界王摸王（自動送出攻擊指令）、群星計畫（自動點培育按鈕）。三套的開關
需求完全一樣（開/關、跨重啟保留、預設開啟），所以共用同一份讀寫邏輯跟
同一個狀態檔，不用三個系統各自維護一份幾乎一樣的程式碼。之後如果再新增
第四套會自動發送的系統，不用新增檔案，呼叫端用一個新的 system_key 呼叫
is_enabled()/set_enabled() 就好。

=== system_key 常數集中在這裡 ===
2026-08-22 發現：同一個 system_key 字串（例如 "satellite_naming"）本來
分別寫死在三個地方——各 trigger 模組自己的 SYSTEM_KEY 常數、這裡的
SYSTEM_KEYS 字典、main.py 的 _AUTO_ALIASES 字典，新增一套系統要記得
同步改三處，容易漏掉其中一個造成不一致（main.py 就漏過一次）。現在
字串常數只在這裡定義一次，其他地方一律 import 這裡的常數，不再各自
打一次字面字串：
    triggers/xxx_strategy.py:  SYSTEM_KEY = auto_toggle.SATELLITE_NAMING
    main.py 的 _AUTO_ALIASES：value 一律引用這裡的常數
新增一套系統時，只要在這裡加一個常數＋SYSTEM_KEYS 的一行，其他地方
import 就好，不會再有三處分別打字串、容易打錯或漏改的問題。

狀態存在 data/common/auto_toggles.json，格式：
    {"main_tower_battle": true, "world_boss": true, "satellite_training": true}
沒有紀錄過的 system_key（或整份檔案不存在）一律視為開啟，不用特別初始化。
"""
import json

from data_store import common_dir

_STATE_FILENAME = "auto_toggles.json"

# system_key 常數——其他模組 import 這些常數使用，不要直接打字面字串。
MAIN_TOWER_BATTLE = "main_tower_battle"
WORLD_BOSS = "world_boss"
SATELLITE_TRAINING = "satellite_training"
GUARD_CLEAR = "guard_clear"
SATELLITE_NAMING = "satellite_naming"
SAKURA_AUTO_CHALLENGE = "sakura_auto_challenge"

# system_key -> 顯示用中文名稱，供 print 訊息跟終端機指令共用，
# 新增系統時只要在這裡（連同上面的常數）加一行，指令跟提示訊息就會自動吃到。
SYSTEM_KEYS = {
    MAIN_TOWER_BATTLE: "主塔戰鬥",
    WORLD_BOSS: "世界王開關 切換模式用/wbmode",
    SATELLITE_TRAINING: "群星計畫（培育衛星）",
    GUARD_CLEAR: "清護衛",
    SATELLITE_NAMING: "群星計畫結業命名",
    SAKURA_AUTO_CHALLENGE: "櫻花窗口自動連刷 切換模式用/sakura",
}


def _state_file_path(base_dir):
    return common_dir(base_dir) / _STATE_FILENAME


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

# ============================================================
# 終端機指令：/auto
# ============================================================
# 統一介面：async def handle_command(text, base_dir, account_id) -> None
# （跟 executor.py 的 handle_delay_command 等同一套介面，main.py 用登記表
# 統一呼叫，不用各自處理，2026-08-22 熊指出 SYSTEM_KEYS 都搬進來了、
# 這個指令的邏輯也該搬過來才有整體感）。

# 短別名 -> 完整 system_key。完整 key 本身一律也能當自己的別名（見
# _all_aliases()），不用在這裡重複列一次。
SHORT_ALIASES = {
    "mtb": MAIN_TOWER_BATTLE,
    "main_tower": MAIN_TOWER_BATTLE,
    "wb": WORLD_BOSS,
    "sat": SATELLITE_TRAINING,
    "satellite": SATELLITE_TRAINING,
    "gc": GUARD_CLEAR,
    "guard": GUARD_CLEAR,
    "satname": SATELLITE_NAMING,
    "sat_name": SATELLITE_NAMING,
    "sakura": SAKURA_AUTO_CHALLENGE,
}

_USAGE = ("[錯誤] /auto 用法：\n"
          "  /auto                    查看三套系統目前開關狀態\n"
          "  /auto <system> on|off    開啟/關閉指定系統\n"
          "  <system>：mtb（主塔戰鬥）／wb（世界王）／sat（群星計畫）／"
          "gc（清除守衛）／satname（群星計畫結業命名）／sakura（櫻花窗口自動連刷）")


def _all_aliases():
    # 完整 key 一律可以當自己的別名（例如 /auto satellite_naming on），
    # 從 SYSTEM_KEYS 自動產生，不用每個系統都手動列一次 "xxx": "xxx"。
    return {**{key: key for key in SYSTEM_KEYS}, **SHORT_ALIASES}


async def handle_command(text, base_dir, account_id):
    """統一開關：主塔戰鬥／世界王／群星計畫等，所有會自動送出動作的系統
    共用同一個指令。"""
    parts = text.split()
    if len(parts) == 1:
        print("[開關狀態]\n" + status_summary(base_dir))
        return
    if len(parts) == 3 and parts[2] in ("on", "off"):
        system_key = _all_aliases().get(parts[1])
        if system_key is None:
            print(_USAGE)
            return
        enabled = parts[2] == "on"
        set_enabled(base_dir, system_key, enabled)
        label = SYSTEM_KEYS[system_key]
        state = "✅ 開啟" if enabled else "🔕 關閉"
        print(f"[開關] {label}：{state}")
        return
    print(_USAGE)