# -*- coding: utf-8 -*-
"""
shapes/furnace_awakening_announcement.py

處理「爐溫燒穿→爐子自己醒了」這件事的廣播公告(出現在公告頻道
「摸摸熊戰鬥陀螺」，跟個人觀火回覆是兩則不同的訊息)。這則帶有明確的
效果描述(跟個人觀火回覆的簡短版不同)跟 20 分鐘投爐窗口的提醒,這兩點
是這支模組存在的主要理由。

只驗證過 1 則實際樣本,「效果描述」目前是整段原始文字保留,理由跟
furnace_feed.py 的 effect_lines 一樣——色系效果差異目前只看過橙焰一種,
硬拆數值容易在新色系出現時整組壞掉。

signature(): 判斷是不是覺醒公告
parse(): 抽成結構化資料
format_for_display(): 組出摘要文字
"""
import re

import furnace_matcher

# 【靈性顫動】目前看過兩種觸發措辭：
#   🔥🔥 @envpop 把爐溫燒穿了,世界的大熔爐開始【靈性顫動】!      —— 爐溫累積到 100/100 燒穿
#   🔥🔥 @envpop 一眼看穿火候,世界的大熔爐開始【靈性顫動】!      —— 觀火時顏色直接猜中,瞬間觸發
# 2026-09-02 只驗證過這兩種，也只各驗證過 1 則，之後如果再冒出第三種措辭，
# 記得回頭把 RE_TRIGGERED_BY 的 alternation 補進去。
RE_TRIGGERED_BY = re.compile(r"🔥🔥\s*(?P<name>\S+?)\s*(?P<method>把爐溫燒穿了|一眼看穿火候)")
RE_WINDOW = re.compile(r"接下來\s*(?P<minutes>\d+)\s*分鐘")

_TRIGGER_METHOD_LABELS = {
    "把爐溫燒穿了": "temperature",
    "一眼看穿火候": "color_match",
}


def signature(text):
    return "【靈性顫動】" in text and bool(RE_TRIGGERED_BY.search(text))


def parse(text):
    triggered_by = RE_TRIGGERED_BY.search(text)
    window = RE_WINDOW.search(text)
    burst = furnace_matcher.FurnaceParser.parse_burst(text)

    return {
        "triggered_by": triggered_by.group("name") if triggered_by else None,
        "trigger_method": _TRIGGER_METHOD_LABELS.get(triggered_by.group("method")) if triggered_by else None,
        "burst_color": burst.color if burst else None,
        "burst_name": burst.burst_name if burst else None,
        "effect_text": burst.inline_effect if burst else None,
        "feed_window_minutes": int(window.group("minutes")) if window else None,
    }


def format_for_display(parsed):
    lines = []

    trigger = f"由 {parsed['triggered_by']} " if parsed["triggered_by"] else ""
    method_note = {
        "temperature": "（爐溫燒穿）",
        "color_match": "（觀火時顏色猜中，瞬間觸發）",
    }.get(parsed["trigger_method"], "")
    lines.append(f"🔥🔥 大熔爐{trigger}燒穿了{method_note}，開始【靈性顫動】！")

    if parsed["burst_color"]:
        lines.append(f"🔥 這次是【{parsed['burst_color']}・{parsed['burst_name']}】")

    if parsed["effect_text"]:
        lines.append(f"✨ 效果：{parsed['effect_text']}")

    if parsed["feed_window_minutes"]:
        lines.append(f"⏳ 接下來 {parsed['feed_window_minutes']} 分鐘，投爐都算數")

    return "\n".join(lines) if lines else "(覺醒公告解析失敗)"