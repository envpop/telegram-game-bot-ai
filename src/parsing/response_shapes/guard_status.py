# -*- coding: utf-8 -*-
"""
parsing/response_shapes/guard_status.py

「衛星護衛」相關訊息的 shape module，涵蓋兩種狀態：
    active     清護衛戰況（王還被 N/M 顆衛星護衛環繞，帶下一顆弱點資訊）
    dispersed  護衛已散去（王已被討伐，純狀態通知，沒有可建議的戰鬥目標）

解析邏輯搬自 query_reactor.py 的 parse_guard_status() / is_guard_dispersed_notice()，
搬過來的同時順便把 query_reactor 原本沒抓的欄位（組成／互相加護／重新編組倒數／
自行散去倒數）一併加進 structured——這些之前只存在原文裡，沒有結構化，之後如果
要做「倒數到了自動重打清護衛」之類的自動化，直接讀這裡的欄位即可，不用重新
regex 原文。

顯示不重組文字：遊戲原文本身格式已經很完整（emoji、分行都做好了），
format_for_display() 直接回傳原文，跟 top_record.py 同樣做法。建議
（出戰哪幾顆打下一顆護衛）不在這裡加——那是 query_advisor_strategy.py
的事，它直接讀訊息原文呼叫 query_reactor.handle_query_reply()，不依賴
這裡的 structured，這個 shape 存在的價值是「讓這則訊息被正式辨識、
structured 欄位可被其他消費者重複使用」，不是顯示格式的改變。
"""

import re

RE_GUARD_DISPERSED = re.compile(r"衛星護衛失去了環繞的對象")

RE_GUARD_HEADER = re.compile(r"「(.+?)」還被\s*(\d+)/(\d+)\s*顆【衛星護衛】環繞")
RE_GUARD_REDUCTION = re.compile(r"王目前減傷\s*(\d+)%")
RE_GUARD_NEXT_TARGET = re.compile(
    r"下一顆[:：](\S+?)・\S*?([火土金木水])屬性・(\S+?)[　\s]*弱點[:：]\S*?([火土金木水])屬性／(\S+?)[　\s\n（]"
)
RE_COMPOSITION = re.compile(r"組成[:：]\s*(.+)")
RE_MUTUAL_BUFF = re.compile(r"互相加護[:：]\s*(.+?)\s*各有兩顆以上")
RE_RESHUFFLE_MIN = re.compile(r"(\d+)\s*分鐘後重新編組")
RE_DISPERSE_MIN = re.compile(r"(\d+)\s*分鐘後自行散去")


def signature(text: str) -> bool:
    text = text or ""
    return bool(RE_GUARD_DISPERSED.search(text) or RE_GUARD_HEADER.search(text))


def parse(text: str) -> dict:
    text = text or ""

    if RE_GUARD_DISPERSED.search(text):
        return {"type": "dispersed", "raw_text": text}

    header = RE_GUARD_HEADER.search(text)
    if not header:
        # 理論上不會發生：signature() 已經先擋過，這裡是防禦性 fallback。
        return {"type": "unknown", "raw_text": text}

    boss_name, remaining, total = header.group(1), int(header.group(2)), int(header.group(3))

    reduction_m = RE_GUARD_REDUCTION.search(text)
    composition_m = RE_COMPOSITION.search(text)
    mutual_buff_m = RE_MUTUAL_BUFF.search(text)
    reshuffle_m = RE_RESHUFFLE_MIN.search(text)
    disperse_m = RE_DISPERSE_MIN.search(text)

    next_target = None
    target_m = RE_GUARD_NEXT_TARGET.search(text)
    if target_m:
        satellite_type, _element, _type, weak_element, weak_type = target_m.groups()
        next_target = {
            "satellite_type": satellite_type,
            "weak_element": weak_element,
            "weak_type": weak_type,
        }

    return {
        "type": "active",
        "raw_text": text,
        "boss_name": boss_name,
        "remaining": remaining,
        "total": total,
        "reduction_pct": int(reduction_m.group(1)) if reduction_m else 0,
        "composition": composition_m.group(1).strip() if composition_m else None,
        "mutual_buff_elements": mutual_buff_m.group(1).strip() if mutual_buff_m else None,
        "reshuffle_min": int(reshuffle_m.group(1)) if reshuffle_m else None,
        "disperse_min": int(disperse_m.group(1)) if disperse_m else None,
        "next_target": next_target,
    }


def format_for_display(parsed: dict) -> str:
    return parsed["raw_text"]


if __name__ == "__main__":
    import json

    active_sample = """🛰️ 「斜坡級・燭龍」還被 5/5 顆【衛星護衛】環繞
🛡️ 王目前減傷 36%（每拆一顆就少一截；王本體隨時打得到）
組成:🔗鎖鏈×2 ⚡充能×1 🛡️破盾×1 📡哨衛×1
🔗 互相加護:🔵水屬性 各有兩顆以上,它們的減傷會互相加成——先拆落單的
🎯 下一顆:🔗鎖鏈衛星・🟡土屬性・攻擊型　弱點:🟢木屬性／防禦型
　（清掉→全服接下來數刀無視王的傷害遞減(下潛/水壓)——高階最有感）
⚔️ 打「清護衛」拆它。五行與類型都剋它 → 一擊拆掉,不用打;剋一項就打得很輕鬆。
🔄 20 分鐘後重新編組(弱點重洗)　⏳ 110 分鐘後自行散去"""

    dispersed_sample = "🛰️ 王已被討伐——衛星護衛失去了環繞的對象,散去了。"

    for label, sample in [("清護衛戰況", active_sample), ("護衛已散去", dispersed_sample)]:
        print(f"=== {label} ===")
        print("signature() =", signature(sample))
        structured = parse(sample)
        print(json.dumps(structured, ensure_ascii=False, indent=2))
        print()