"""
shapes/main_tower_battle_prompt.py

處理主塔「進階戰鬥」(36 階以上，boss_catalog_main_tower.json 標記 advanced:true
的樓層) 每一回合要求選擇戰術的訊息。

原始格式（節錄自實際樣本，第 2 輪迴・第 100 階）：
    ⚔️【進階戰鬥】🌌第 2 輪迴・第 100 階・摸摸熊・原初真神（攻擊型・🔴火屬性）  R0
    👑 神格:再生・神威首擊・神體庇護・神怒　🌌神威 Lv4(你的傷害 ×0.63)
    🌀 你 HP 895/895　██████████
    👹 敵 HP 1455/1455　██████████
    ✨ 能量 0/100　🛡️護盾 224
    ──────────────
    選擇戰術:

這則訊息本身確認了一件事：遊戲畫面上「輪迴數」跟「樓層數」是分開顯示的欄位，
樓層永遠是 1~100 內的數字，不會像 boss_catalog_main_tower.json 原本假設的
那樣累加到 101 以上。main_tower_advisor.get_boss_for_floor() 因此已經改成
直接用訊息裡的「第 X 階」查表，不再做 mod 換算。

神格效果（再生／神威首擊／神體庇護／神怒…）是王身上隨機出現的技能，順序跟
組合不可控，也沒有辦法針對特定技能調整戰術，所以這裡只把它們解析出來存放，
不納入 main_tower_battle_strategy 的決策依據。

戰術按鈕本身不是這支模組的責任——按鈕清單(record["buttons"]) 有幾顆、
data 代碼是什麼，由 main_tower_battle_strategy.py 直接讀 record 判斷。
一開始固定 3 顆(強攻/穩守/蓄力)，能量滿了之後會多一顆必殺技，這支 shape
只管把「當下數值狀態」解析出來，按鈕變化跟這裡的 parse() 輸出無關。

signature(): 判斷一段文字是不是這個 shape
parse(): 抽成結構化資料
format_for_display(): 組出精簡摘要文字
"""
import re

# 標頭：⚔️【進階戰鬥】🌌第 2 輪迴・第 100 階・摸摸熊・原初真神（攻擊型・🔴火屬性）  R0
RE_HEADER = re.compile(
    r"進階戰鬥】🌌第\s*(?P<loop>\d+)\s*輪迴・第\s*(?P<floor>\d+)\s*階・"
    r"(?P<boss_name>[^（]+)（(?P<boss_type>[^・]+)・\S*?(?P<boss_element>[火土金木水])屬性）"
    r"\s*R(?P<round>\d+)"
)

# 神格行：👑 神格:再生・神威首擊・神體庇護・神怒　🌌神威 Lv4(你的傷害 ×0.63)
RE_GODHOOD = re.compile(
    r"神格:(?P<effects>[^\s　]+)　🌌神威\s*Lv(?P<godhood_lv>\d+)"
    r"\(你的傷害\s*×(?P<dmg_mult>[\d.]+)\)"
)

RE_OWN_HP = re.compile(r"你\s*HP\s*(?P<own_hp>\d+)/(?P<own_hp_max>\d+)")
RE_BOSS_HP = re.compile(r"敵\s*HP\s*(?P<boss_hp>\d+)/(?P<boss_hp_max>\d+)")

# 能量跟護盾故意拆成兩條獨立 regex，不綁在同一個 match 裡：護盾這個欄位
# 不是每回合都會顯示（例如沒有護盾時整段「🛡️護盾 N」直接不出現），如果
# 寫成同一個 pattern，護盾缺席會連能量都抓不到，能量是每回合決策都要用的
# 關鍵數值，不能因為護盾有沒有顯示而受影響。
RE_ENERGY = re.compile(r"能量\s*(?P<energy>\d+)/(?P<energy_max>\d+)")
RE_SHIELD = re.compile(r"🛡️護盾\s*(?P<shield>\d+)")


def signature(text):
    return "【進階戰鬥】" in text and "選擇戰術" in text


def parse(text):
    header = RE_HEADER.search(text)
    godhood = RE_GODHOOD.search(text)
    own_hp = RE_OWN_HP.search(text)
    boss_hp = RE_BOSS_HP.search(text)
    energy = RE_ENERGY.search(text)
    shield = RE_SHIELD.search(text)

    effects = []
    if godhood:
        effects = [e for e in godhood.group("effects").split("・") if e]

    return {
        "loop": int(header.group("loop")) if header else None,
        "floor": int(header.group("floor")) if header else None,
        "boss_name": header.group("boss_name") if header else None,
        "boss_type": header.group("boss_type") if header else None,
        "boss_element": header.group("boss_element") if header else None,
        "round": int(header.group("round")) if header else None,
        "godhood_effects": effects,
        "godhood_level": int(godhood.group("godhood_lv")) if godhood else None,
        "damage_multiplier": float(godhood.group("dmg_mult")) if godhood else None,
        "own_hp": int(own_hp.group("own_hp")) if own_hp else None,
        "own_hp_max": int(own_hp.group("own_hp_max")) if own_hp else None,
        "boss_hp": int(boss_hp.group("boss_hp")) if boss_hp else None,
        "boss_hp_max": int(boss_hp.group("boss_hp_max")) if boss_hp else None,
        "energy": int(energy.group("energy")) if energy else None,
        "energy_max": int(energy.group("energy_max")) if energy else None,
        "shield": int(shield.group("shield")) if shield else None,
    }


def format_for_display(parsed):
    lines = []

    if parsed["boss_name"]:
        loop_floor = ""
        if parsed["loop"] and parsed["floor"]:
            loop_floor = f"第{parsed['loop']}輪迴・第{parsed['floor']}階 "
        type_element = ""
        if parsed["boss_type"] and parsed["boss_element"]:
            type_element = f"（{parsed['boss_type']}・{parsed['boss_element']}屬性）"
        round_suffix = f" R{parsed['round']}" if parsed["round"] is not None else ""
        lines.append(f"⚔️ {loop_floor}{parsed['boss_name']}{type_element}{round_suffix}")

    if parsed["godhood_effects"]:
        lines.append(f"👑 神格：{'・'.join(parsed['godhood_effects'])}")
        if parsed["godhood_level"] is not None and parsed["damage_multiplier"] is not None:
            lines.append(f"🌌 神威 Lv{parsed['godhood_level']}（你的傷害 ×{parsed['damage_multiplier']}）")

    if parsed["own_hp"] is not None:
        lines.append(f"🌀 你 HP {parsed['own_hp']}/{parsed['own_hp_max']}")
    if parsed["boss_hp"] is not None:
        lines.append(f"👹 敵 HP {parsed['boss_hp']}/{parsed['boss_hp_max']}")
    if parsed["energy"] is not None:
        shield_part = f"　🛡️護盾 {parsed['shield']}" if parsed["shield"] is not None else ""
        lines.append(f"✨ 能量 {parsed['energy']}/{parsed['energy_max']}{shield_part}")

    return "\n".join(lines) if lines else "(主塔戰鬥狀態解析失敗)"