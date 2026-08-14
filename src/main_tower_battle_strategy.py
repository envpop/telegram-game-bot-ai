"""
main_tower_battle_strategy.py —— 主塔戰鬥（進階戰鬥）即時戰術決策層

只負責一件事：看到一則「選擇戰術」訊息（帶按鈕），決定要點哪一顆按鈕。
不負責送出動作（那是 executor.click_button 的事），也不負責解析訊息文字
（那是 parsing/response_shapes/main_tower_battle_prompt.py 的事）——這裡
只吃已經解析好的 structured dict + 當下按鈕清單。

=== 決策原則（熊確認過）===
王身上的神格效果（再生／神威首擊／神體庇護／神怒…）是隨機出現、玩家無法
控制、也沒有辦法針對特定技能調整戰術，所以完全不納入這裡的決策，只看
自己這邊當下的即時狀態：HP／能量／護盾。

規則優先序（由上而下，符合就採用，不再往下比對）：
    1. 能量已滿且必殺技按鈕存在 → 打必殺，不浪費滿能量
    2. 自己 HP 比例過低 → 穩守求生，優先權比繼續進攻高
    3. HP 還算安全、能量還沒到臨界值 → 蓄力，趁安全累積能量
    4. 其餘情況 → 強攻（預設）

=== 已知限制／TODO ===
- 護盾（shield）目前只解析出絕對數字，還沒真的參與決策。護盾上限跟
  出戰陀螺類型有關，目前沒有「各類型護盾上限」對照資料，算不出比例。
  等熊之後補這份資料，可以在規則 2 加入「護盾夠厚可以先撐著，不用急著
  穩守」這種調整，不用改函式邏輯，只要多讀一份 catalog。
- 下面幾個門檻常數是第一版的起始猜測值，不是遊戲內建的正式數字。之後
  熊實戰觀察覺得不準，直接改常數就好；如果連判斷邏輯的形狀都要換
  （例如改成參考傷害輸出效率而不是單純血量比例），才需要動函式本身。
- 必殺技按鈕的比對方式是「文字裡含『必殺』」，不是照 action code 比對
  （目前只有一筆樣本：🔘 [2,2] ✨ GO SHOOT 必殺!，還不確定 data 代碼
  的命名規則）。之後如果拿到更多必殺技樣本、確認代碼規則，可以比照
  _find_button_by_action_code 改成代碼比對，更穩定。
"""

# HP 比例低於這個值，優先穩守求生（活下去比繼續進攻重要）
LOW_HP_RATIO = 0.3

# HP 比例高於這個值才考慮蓄力（血太少不該還想著攢技能）
CHARGE_HP_RATIO = 0.5

# 能量接近這個值時改成強攻，等下一輪直接補滿更划算，不用再蓄力
CHARGE_ENERGY_CAP = 80

_ULTIMATE_BUTTON_TEXT_HINT = "必殺"


def decide_action(structured, buttons):
    """核心決策函式。

    structured: response_shapes/main_tower_battle_prompt.py 的 parse() 輸出
    buttons: monitor 記錄的按鈕清單（extract_buttons() 的輸出格式）

    回傳 {"data": ..., "button_text": ..., "reason": ...} 或 None
    （關鍵數值解析不完整或找不到對應按鈕，交給人工介入，不亂點）。
    """
    if not buttons or structured is None:
        return None

    own_hp = structured.get("own_hp")
    own_hp_max = structured.get("own_hp_max")
    energy = structured.get("energy")
    energy_max = structured.get("energy_max")

    if own_hp is None or not own_hp_max or energy is None:
        return None

    ultimate_button = _find_button_by_text_contains(buttons, _ULTIMATE_BUTTON_TEXT_HINT)

    if energy_max and energy >= energy_max and ultimate_button:
        return {
            "data": ultimate_button["data"],
            "button_text": ultimate_button["text"],
            "reason": f"能量已滿（{energy}/{energy_max}），打出必殺技",
        }

    hp_ratio = own_hp / own_hp_max

    if hp_ratio <= LOW_HP_RATIO:
        matched = _find_button_by_action_code(buttons, "def")
        if matched:
            return {
                "data": matched["data"],
                "button_text": matched["text"],
                "reason": f"HP 過低（{own_hp}/{own_hp_max}，{hp_ratio:.0%}），優先穩守求生",
            }

    energy_ceiling = min(energy_max, CHARGE_ENERGY_CAP) if energy_max else CHARGE_ENERGY_CAP
    if hp_ratio >= CHARGE_HP_RATIO and energy < energy_ceiling:
        matched = _find_button_by_action_code(buttons, "chg")
        if matched:
            return {
                "data": matched["data"],
                "button_text": matched["text"],
                "reason": f"HP 安全（{hp_ratio:.0%}）且能量未滿（{energy}/{energy_max}），趁機蓄力",
            }

    matched = _find_button_by_action_code(buttons, "atk")
    if matched:
        return {
            "data": matched["data"],
            "button_text": matched["text"],
            "reason": "預設強攻",
        }

    return None


def _find_button_by_action_code(buttons, action_code):
    """catalog／程式裡存的是乾淨代碼（例如 "atk"），實際按鈕 data 會帶
    sender_id 前綴（例如 "ab:190739112:atk"），用「以 :action_code 結尾」
    比對，跟 satellite_training_strategy._find_button_by_data 同樣做法。
    """
    suffix = ":" + action_code
    for b in buttons:
        data = b.get("data") or ""
        if data == action_code or data.endswith(suffix):
            return b
    return None


def _find_button_by_text_contains(buttons, text_hint):
    for b in buttons:
        if text_hint in (b.get("text") or ""):
            return b
    return None
