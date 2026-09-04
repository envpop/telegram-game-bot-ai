# -*- coding: utf-8 -*-
"""
shapes/furnace_watch.py

處理「觀火」動作的個人回覆(私訊/摸熊神社)。

只驗證過 2 則實際樣本(一般進度 1 則、燒穿覺醒 1 則),覆蓋了「有沒有燒穿」
這個最主要的分支,但還沒看過:
  - 爐溫已經是 100/100、但這次操作沒有燒穿(例如燒穿可能帶隨機成分,不是
    100% 必中)的樣子
  - 花費碎片數是否固定 5 片,還是可能不同
這些之後有新樣本再回頭補。

signature(): 判斷是不是「觀火」回覆
parse(): 抽成結構化資料
format_for_display(): 組出摘要文字
"""
import re

import furnace_matcher

RE_COST = re.compile(r"你付了\s*(?P<cost>\d+)\s*片碎片")
RE_COLOR_COMPARE = re.compile(
    r"爐內火光是\s*\S?【(?P<current>[^】]+)】,這個時辰要的是\s*\S?【(?P<target>[^】]+)】"
)
RE_TEMP = re.compile(r"爐溫\s*\+(?P<gain>\d+)\s*→\s*(?P<cur>\d+)/(?P<max>\d+)")

# 【靈性顫動】目前看過兩種觸發措辭(2026-09-03 用實際樣本確認):
#   🔥🔥 爐溫燒穿了——爐子自己醒了!【靈性顫動】開啟!      —— 爐溫累積到 100/100
#   🔥🔥 火候正對——爐火猛地一顫,【靈性顫動】開啟!        —— 觀火時顏色直接猜中,沒有爐溫那行
# 兩者都有共同的「【靈性顫動】開啟」，用這句當判斷依據，比列舉觸發措辭本身更穩，
# 之後如果再冒出第三種措辭也不用改這裡。
RE_BREAKTHROUGH = re.compile(r"【靈性顫動】開啟")
_TRIGGER_METHOD_LABELS = {
    "爐溫燒穿了": "temperature",
    "火候正對": "color_match",
}


def signature(text):
    return "你付了" in text and "湊近世界的大熔爐" in text


def _trigger_method(text):
    for phrase, label in _TRIGGER_METHOD_LABELS.items():
        if phrase in text:
            return label
    return None


def parse(text):
    cost = RE_COST.search(text)
    color = RE_COLOR_COMPARE.search(text)
    temp = RE_TEMP.search(text)
    breakthrough = bool(RE_BREAKTHROUGH.search(text))
    burst = furnace_matcher.FurnaceParser.parse_burst(text) if breakthrough else None

    return {
        "shard_cost": int(cost.group("cost")) if cost else None,
        "current_color": color.group("current") if color else None,
        "target_color": color.group("target") if color else None,
        "temp_gain": int(temp.group("gain")) if temp else None,
        "temp_current": int(temp.group("cur")) if temp else None,
        "temp_max": int(temp.group("max")) if temp else None,
        "breakthrough": breakthrough,
        "trigger_method": _trigger_method(text) if breakthrough else None,
        "burst_color": burst.color if burst else None,
        "burst_name": burst.burst_name if burst else None,
        "eye_pending": burst.eye_pending if burst else False,
    }


def format_for_display(parsed):
    lines = []

    if parsed["shard_cost"] is not None:
        lines.append(f"🔥 觀火：花費 {parsed['shard_cost']} 片碎片")

    if parsed["current_color"] and parsed["target_color"]:
        lines.append(f"🎨 爐內火光【{parsed['current_color']}】／這個時辰要的是【{parsed['target_color']}】")

    if parsed["temp_current"] is not None:
        lines.append(f"♨️ 爐溫 +{parsed['temp_gain']} → {parsed['temp_current']}/{parsed['temp_max']}")

    if parsed["breakthrough"]:
        method_note = {
            "temperature": "（爐溫燒穿）",
            "color_match": "（顏色猜中，瞬間觸發）",
        }.get(parsed["trigger_method"], "")
        lines.append(f"🔥🔥 【靈性顫動】開啟{method_note}！")
        if parsed["burst_color"]:
            lines.append(f"　　這次的爆射會是【{parsed['burst_color']}・{parsed['burst_name']}】")
        if parsed["eye_pending"]:
            lines.append("👁️ 你被記為【爐火之眼】——等世界王倒下才會發放獎勵")

    return "\n".join(lines) if lines else "(觀火訊息解析失敗)"