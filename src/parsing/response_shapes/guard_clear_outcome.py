# -*- coding: utf-8 -*-
"""
parsing/response_shapes/guard_clear_outcome.py

「清護衛」動作送出後的結果訊息——不是查詢（那是 guard_status.py 管的
「還被 N/M 顆環繞」訊息），這裡管的是「打完之後發生了什麼事」，有兩種
完全不同的來源格式，但語意上都是同一件事的結果，統一在這裡判斷：

    variant="instant_kill"    一擊拆掉，沒有進入戰鬥模式
        🎯 一擊拆除!
        ──────────────
        （陀螺）的（屬性）・（類型）同時剋住了 （護衛）（屬性・類型）。
        它連轉都沒轉起來就碎了。
        ──────────────
        （拆除訊息，含全服加成說明）
        💰 +10 點...
        🛰️ 還剩 N/8 顆——打「護衛」看下一顆的弱點。
            —— 或者，如果這是最後一顆：
        💥 護衛全數清空!全服接下來 10 分鐘傷害 +15%。

    variant="battle_victory"  沒一擊拆掉，進入按鈕戰鬥模式，這是戰鬥
                               結束（勝利）那則收尾訊息（沒有按鈕，
                               跟還在進行中的回合訊息用「有沒有按鈕」
                               區分，battle round 本身不歸這個 shape管，
                               那是另一支 guard_battle_prompt.py 的事）
        🛰️【衛星護衛】（護衛名）（類型・屬性）
        ──────────────
        （戰鬥過程...）
        ──────────────
        🏆 勝利!殘血 X／Y
        （拆除訊息...）
        🛰️ 護衛還剩 N/8 顆 — ...

兩種格式最後都會告知「還剩 N/M 顆」或「全數清空」，這裡統一抽出
remaining/total/cleared_all，讓上層的清護衛迴圈策略不用管訊息實際
是哪個來源、格式細節長怎樣，只要讀這三個欄位就能決定要不要繼續。

目前沒有「戰敗」樣本（battle_victory 的對應失敗情況），不猜格式——
如果戰鬥失敗訊息長相跟「🏆 勝利」不同，這個 shape 目前 signature()
不會比對到，會被當一般伺服器訊息（fallback 顯示原文），不會誤判成
清護衛結果。等有實際樣本再補。
"""

import re

_INSTANT_KILL_HEADER = "🎯 一擊拆除!"
_BATTLE_VICTORY_MARK = "🏆 勝利!"
_BATTLE_HEADER_PREFIX = "🛰️【衛星護衛】"

RE_REMAINING = re.compile(r"還剩\s*(\d+)/(\d+)\s*顆")
RE_CLEARED_ALL = re.compile(r"護衛全數清空")

# 一擊拆除訊息裡「（陀螺）的（屬性）・（類型）同時剋住了 （護衛名）（屬性・類型）」
RE_INSTANT_KILL_MATCHUP = re.compile(
    r"(.+?)\s*的\s*(\S+屬性)・(\S+型)\s*同時剋住了\s*(\S+?)\s*[（(](\S+屬性)・(\S+型)[）)]"
)

# 拆除的是哪一種護衛：「⚡ 拆除【充能衛星】——...」/「🛡️ 拆除【破盾衛星】——...」
RE_REMOVED_TYPE = re.compile(r"拆除【(.+?)】")


def signature(text: str) -> bool:
    text = text or ""
    if text.startswith(_INSTANT_KILL_HEADER):
        return True
    if text.startswith(_BATTLE_HEADER_PREFIX) and _BATTLE_VICTORY_MARK in text:
        return True
    return False


def parse(text: str) -> dict:
    variant = "instant_kill" if text.startswith(_INSTANT_KILL_HEADER) else "battle_victory"

    cleared_all = bool(RE_CLEARED_ALL.search(text))
    remaining, total = None, None
    m = RE_REMAINING.search(text)
    if m:
        remaining, total = int(m.group(1)), int(m.group(2))
    elif cleared_all:
        remaining = 0

    removed_type = None
    m2 = RE_REMOVED_TYPE.search(text)
    if m2:
        removed_type = m2.group(1)

    result = {
        "variant": variant,
        "raw_text": text,
        "remaining": remaining,
        "total": total,
        "cleared_all": cleared_all,
        "removed_type": removed_type,
    }

    if variant == "instant_kill":
        mm = RE_INSTANT_KILL_MATCHUP.search(text)
        if mm:
            result["matchup"] = {
                "top_name": mm.group(1).strip(),
                "top_element": mm.group(2),
                "top_type": mm.group(3),
                "guard_name": mm.group(4),
                "guard_element": mm.group(5),
                "guard_type": mm.group(6),
            }

    return result


def format_for_display(parsed: dict) -> str:
    return parsed["raw_text"]


if __name__ == "__main__":
    sample_continue = """🎯 一擊拆除!
──────────────
龍淵・千重浪・蒼海旋王・不滅GO +15 的 🟡土屬性・持久型 同時剋住了 護衛星・旋滅（🔵水屬性・防禦型）。
它連轉都沒轉起來就碎了。
──────────────
📡 拆除【哨衛星】——不給王減傷,但每十分鐘幫王回血——放著不管,大家都在白打（全服接下來 3 刀）
💰 +10 點　👑王核碎晶 +1
🛰️ 還剩 2/8 顆——打「護衛」看下一顆的弱點。"""

    sample_cleared = """🎯 一擊拆除!
──────────────
☆聖氣盾・極・天熊・滅卻牙 +15 的 🔴火屬性・防禦型 同時剋住了 護衛星・裂星（⚪金屬性・攻擊型）。
它連轉都沒轉起來就碎了。
──────────────
⚡ 拆除【充能衛星】——清掉→全服接下來數刀傷害提升（全服接下來 3 刀）
💰 +10 點　👑王核碎晶 +3（清場加碼!）
💥 護衛全數清空!全服接下來 10 分鐘傷害 +15%。"""

    sample_battle_victory = """🛰️【衛星護衛】護衛星・噬光（攻擊型・🔴火屬性）
──────────────
🛡️ 穩守！架住攻勢並蓄一層護盾
🛰️ 衛星援護 -319
🛰️ 衛星援護 -179
🛰️ 衛星援護 -409
──────────────
🏆 勝利!殘血 306／1055
🛡️ 拆除【破盾衛星】——清掉→王破防,全服接下來數刀傷害大幅提升(全服接下來 3 刀)
⚡ 破陣:殘血 29% → 對王的下一刀 ×1.22
💰 +10 點　👑王核碎晶 +1
🛰️ 護衛還剩 3/8 顆 — 王的減傷少了一截,可以接著拆。"""

    for label, sample in [("一擊拆除-還有剩", sample_continue),
                           ("一擊拆除-全清", sample_cleared),
                           ("戰鬥勝利", sample_battle_victory)]:
        print(f"=== {label} ===")
        print("signature() =", signature(sample))
        import json
        print(json.dumps(parse(sample), ensure_ascii=False, indent=2))
        print()