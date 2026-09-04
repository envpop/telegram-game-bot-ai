# -*- coding: utf-8 -*-
"""
shapes/furnace_feed_blocked.py

處理「爐子還沒進入【靈性顫動】,投爐動作被擋下」的回覆。跟
furnace_watch_blocked.py 是對稱的另一半:
  - furnace_watch_blocked：已經在顫動中,卻還想觀火 → 系統說去投爐
  - furnace_feed_blocked ：還沒進入顫動,卻想投爐         → 系統說時機未到

只驗證過 1 則實際樣本。
"""


def signature(text):
    return "爐火沉寂,現在投什麼都沒反應" in text or "爐火沉寂，現在投什麼都沒反應" in text


def parse(text):
    return {}


def format_for_display(parsed):
    return "🔥 爐火沉寂，還沒到可以投爐的時候——先「觀火」把爐溫推到 100 才能投爐"
