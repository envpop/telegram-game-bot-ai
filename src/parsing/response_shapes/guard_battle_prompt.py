# -*- coding: utf-8 -*-
"""
parsing/response_shapes/guard_battle_prompt.py

處理「清護衛」沒能一擊拆掉、進入按鈕戰鬥模式時，每回合要求選擇戰術的訊息。

原始格式（節錄自實際樣本）：
    🛰️【衛星護衛】護衛星・噬光（攻擊型・🔴火屬性）  R0
    🌀 你 HP 1055/1055　██████████
    👹 敵 HP 2745/2745　██████████
    ✨ 能量 0/100　🛡️護盾 264
    ──────────────
    選擇戰術:

跟 main_tower_battle_prompt.py 是同一套機制（三顆按鈕 強攻/穩守/蓄力，
action code atk/def/chg，編輯同一則訊息推進回合），HP/能量/護盾的正則式
直接 import 那支的，不重複維護兩份——差異只在標頭那一行（沒有輪迴/樓層/
神格，只有護衛名稱/類型/屬性），跟 signature() 判斷式（"【衛星護衛】"
不是 "【進階戰鬥】"，避免兩支 shape 互相誤判）。

=== 跟主塔戰鬥的關鍵差異（熊確認，會影響 strategy 的門檻，不是這支
shape 的事，但寫在這裡讓之後寫 guard_battle_strategy.py 時看得到）===
主塔戰鬥出戰的陀螺一定是熊自己選的、至少不會被剋制；護衛戰鬥可能是
「原本想剋制的護衛因為重新增生換了屬性，沒發現就打下去」，所以有機率
打的是「不利對局」——main_tower_battle_strategy.py 那組門檻常數
（CRITICAL_HP_RATIO/SHIELD_PHASE_THRESHOLD）是照「至少中立」的傷害
曲線調的，不能假設護衛戰鬥套用同一組數字一樣安全。

戰術按鈕本身不是這支模組的責任，跟 main_tower_battle_prompt.py 原則一致：
按鈕清單由 record["buttons"] 讀，這裡只管數值狀態解析。

signature(): 判斷一段文字是不是這個 shape
parse(): 抽成結構化資料
format_for_display(): 組出精簡摘要文字
"""
import re

from .main_tower_battle_prompt import RE_OWN_HP, RE_BOSS_HP, RE_ENERGY, RE_SHIELD

# 標頭：🛰️【衛星護衛】護衛星・噬光（攻擊型・🔴火屬性）  R0
RE_HEADER = re.compile(
    r"【衛星護衛】(?P<guard_name>[^（]+)"
    r"（(?P<guard_type>[^・]+)・\S*?(?P<guard_element>[火土金木水])屬性）"
    r"\s*R(?P<round>\d+)"
)


def signature(text):
    return "【衛星護衛】" in text and "選擇戰術" in text


def parse(text):
    header = RE_HEADER.search(text)
    own_hp = RE_OWN_HP.search(text)
    boss_hp = RE_BOSS_HP.search(text)
    energy = RE_ENERGY.search(text)
    shield = RE_SHIELD.search(text)

    return {
        "guard_name": header.group("guard_name") if header else None,
        "guard_type": header.group("guard_type") if header else None,
        "guard_element": header.group("guard_element") if header else None,
        "round": int(header.group("round")) if header else None,
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

    if parsed["guard_name"]:
        type_element = ""
        if parsed["guard_type"] and parsed["guard_element"]:
            type_element = f"（{parsed['guard_type']}・{parsed['guard_element']}屬性）"
        round_suffix = f" R{parsed['round']}" if parsed["round"] is not None else ""
        lines.append(f"🛰️ {parsed['guard_name']}{type_element}{round_suffix}")

    if parsed["own_hp"] is not None:
        lines.append(f"🌀 你 HP {parsed['own_hp']}/{parsed['own_hp_max']}")
    if parsed["boss_hp"] is not None:
        lines.append(f"👹 敵 HP {parsed['boss_hp']}/{parsed['boss_hp_max']}")
    if parsed["energy"] is not None:
        shield_part = f"　🛡️護盾 {parsed['shield']}" if parsed["shield"] is not None else ""
        lines.append(f"✨ 能量 {parsed['energy']}/{parsed['energy_max']}{shield_part}")

    return "\n".join(lines) if lines else "(護衛戰鬥狀態解析失敗)"


if __name__ == "__main__":
    sample = """🛰️【衛星護衛】護衛星・噬光（攻擊型・🔴火屬性）  R2
🌀 你 HP 678/1055　██████░░░░
👹 敵 HP 1630/2745　██████░░░░
✨ 能量 36/100
──────────────
🛡️ 穩守！架住攻勢並蓄一層護盾
🛰️ 衛星援護 -187
▶ 你造成 10💥💥爆擊 ＋追打 91
🛡️ 護盾擋下 164
◀ 敵造成 450.79999999999995💥（剋制）
──────────────
選擇戰術:"""

    print("signature() =", signature(sample))
    parsed = parse(sample)
    print(parsed)
    print()
    print(format_for_display(parsed))