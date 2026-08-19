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

=== 2026-08-14 修正 v2：防禦優先＋護盾門檻切換進攻 ===
v1（HP 比例三段式）在兩場實戰都輸了——王的單回合爆發傷害太誇張（常見
280~340），HP 從滿血掉到危險區間往往只要 2 回合，等偵測到「生死交關」
才強攻已經來不及。熊的新想法：一開始就以防禦為主，把護盾堆起來當緩衝，
護盾堆到 600 以上再轉成蓄力→強攻的進攻節奏，而不是被動等血量掉到低點
才反應。

規則優先序（由上而下）：
    1. 能量已滿且必殺技按鈕存在 → 一定打必殺（不管防禦/進攻哪個階段）
    2. HP 比例 < CRITICAL_HP_RATIO（生死交關）→ 強攻，靠吸血拚一口氣
       —— 這條是全階段通用的最後防線，不管護盾有沒有到 600，保命優先。
       這是我自己補的假設（熊沒明講要不要保留），先照這樣寫，之後熊
       如果覺得護盾夠厚時不需要這條保命線，直接跟我說要拿掉。
    3. 護盾 < SHIELD_PHASE_THRESHOLD（防禦階段）→ 穩守，堆疊護盾層數
    4. 護盾 >= SHIELD_PHASE_THRESHOLD（進攻階段）→ 蓄力衝能量；能量接近
       上限時改強攻打傷害，不用再蓄

=== 已知限制／TODO ===
- 護盾沒有顯示的回合（parsed 出來 shield 是 None）一律當作 0 處理，
  也就是預設還在防禦階段——這是合理假設，因為目前看到的樣本都是護盾
  為 0（或還沒展開）時，訊息裡乾脆不顯示這一行。
- SHIELD_PHASE_THRESHOLD=600、CRITICAL_HP_RATIO=0.15 都是這次拍腦袋的
  起始值，不是遊戲內建數字，熊實戰觀察覺得不準直接改常數即可。
- 這套邏輯是照攻擊型＋吸血技能寫的，防禦型/持久型陀螺不一定適用，
  之後熊拿別的類型打主塔要再討論調整。
- 必殺技按鈕的比對方式是「文字裡含『必殺』」，不是照 action code 比對
  （目前只有一筆樣本：🔘 [2,2] ✨ GO SHOOT 必殺!，還不確定 data 代碼
  的命名規則）。之後如果拿到更多必殺技樣本、確認代碼規則，可以比照
  _find_button_by_action_code 改成代碼比對，更穩定。

=== 自動點擊開關 ===
是否自動送出點擊由 auto_toggle.py 統一管理（system_key="main_tower_battle"），
跟世界王、群星計畫共用同一套開關機制，不在這支檔案裡維護。開關本身只
影響「要不要呼叫 decide_action 並點擊」，不影響訊息解析／顯示——關掉
自動點擊時，戰鬥狀態一樣會被解析出來顯示在畫面上，只是不會自動按按鈕，
交給熊自己手動選。由 action_dispatcher.py 在呼叫 decide_action 之前檢查。
"""

# HP 比例低於這個值，判定「生死交關」——不管防禦/進攻哪個階段，優先強攻，
# 靠吸血拚一口氣，因為攻擊型陀螺穩守減傷有限，硬守也擋不住。
CRITICAL_HP_RATIO = 0.15

# 護盾達到這個值之前優先穩守堆疊（防禦階段）；達到之後轉蓄力→強攻的
# 進攻節奏（進攻階段）。
SHIELD_PHASE_THRESHOLD = 600

# 進攻階段裡，能量接近這個值時改成強攻，等下一輪直接補滿更划算，
# 不用再蓄力
CHARGE_ENERGY_CAP = 80

_ULTIMATE_BUTTON_TEXT_HINT = "必殺"


def decide_action(structured, buttons,
                   critical_hp_ratio=CRITICAL_HP_RATIO,
                   shield_phase_threshold=SHIELD_PHASE_THRESHOLD):
    """核心決策函式。

    structured: response_shapes/main_tower_battle_prompt.py 的 parse() 輸出
                （或格式相容的 guard_battle_prompt.py 輸出——欄位子集一致：
                own_hp/own_hp_max/energy/energy_max/shield）
    buttons: monitor 記錄的按鈕清單（extract_buttons() 的輸出格式）
    critical_hp_ratio / shield_phase_threshold: 門檻常數，預設是 mtb 的數字。
        2026-08-17 開放成參數：護衛戰鬥（guard_battle_strategy.py）可能是
        「原本鎖定的護衛因重新增生換了屬性，沒發現就打下去」的不利對局，
        跟 mtb 出戰陀螺保證至少中立的假設不同，不能沿用同一組數字，
        改成參數讓呼叫端自己決定要用哪組——決策邏輯只有一份，門檻各自調。

    回傳 {"data": ..., "button_text": ..., "reason": ...} 或 None
    （關鍵數值解析不完整或找不到對應按鈕，交給人工介入，不亂點）。
    """
    if not buttons or structured is None:
        return None

    own_hp = structured.get("own_hp")
    own_hp_max = structured.get("own_hp_max")
    energy = structured.get("energy")
    energy_max = structured.get("energy_max")
    # 護盾沒顯示的回合視為 0（還在防禦階段起點），見檔頭 TODO 說明
    shield = structured.get("shield") or 0

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

    if hp_ratio < critical_hp_ratio:
        matched = _find_button_by_action_code(buttons, "atk")
        if matched:
            return {
                "data": matched["data"],
                "button_text": matched["text"],
                "reason": f"HP 生死交關（{own_hp}/{own_hp_max}，{hp_ratio:.0%}），"
                          f"不管護盾階段，強攻靠吸血拚一口氣保命",
            }

    if shield < shield_phase_threshold:
        matched = _find_button_by_action_code(buttons, "def")
        if matched:
            return {
                "data": matched["data"],
                "button_text": matched["text"],
                "reason": f"護盾尚未達門檻（{shield}/{shield_phase_threshold}），"
                          f"防禦階段，穩守堆疊護盾層數",
            }
    else:
        energy_ceiling = min(energy_max, CHARGE_ENERGY_CAP) if energy_max else CHARGE_ENERGY_CAP
        if energy < energy_ceiling:
            matched = _find_button_by_action_code(buttons, "chg")
            if matched:
                return {
                    "data": matched["data"],
                    "button_text": matched["text"],
                    "reason": f"護盾已達門檻（{shield}），進攻階段，蓄力衝能量",
                }
        else:
            matched = _find_button_by_action_code(buttons, "atk")
            if matched:
                return {
                    "data": matched["data"],
                    "button_text": matched["text"],
                    "reason": f"護盾已達門檻（{shield}）且能量接近上限，進攻階段，直接強攻打傷害",
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