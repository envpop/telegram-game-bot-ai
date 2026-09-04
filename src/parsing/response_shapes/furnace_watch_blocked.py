# -*- coding: utf-8 -*-
"""
shapes/furnace_watch_blocked.py

處理「爐子已經在【靈性顫動】中,觀火動作被擋下」的回覆。這種情況下玩家
根本沒有付出碎片(跟 furnace_watch.py 的正常觀火是完全不同的訊息,不能
共用 signature),遊戲直接建議去投爐,所以獨立一個極簡單的 shape。

只驗證過 1 則實際樣本。
"""
import re

RE_BLOCKED_UNTIL = re.compile(r"爐子已經在【靈性顫動】中\(到\s*(?P<until>\d{2}:\d{2})\)")


def signature(text):
    return "爐子已經在【靈性顫動】中" in text


def parse(text):
    m = RE_BLOCKED_UNTIL.search(text)
    return {
        "awakened_until": m.group("until") if m else None,
    }


def format_for_display(parsed):
    if parsed["awakened_until"]:
        return f"🔥🔥 爐子已在【靈性顫動】中，到 {parsed['awakened_until']} 為止——該去投爐，不用再觀火"
    return "🔥🔥 爐子已在【靈性顫動】中——該去投爐，不用再觀火"
