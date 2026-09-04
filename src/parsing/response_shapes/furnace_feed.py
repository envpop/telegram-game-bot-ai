# -*- coding: utf-8 -*-
"""
shapes/furnace_feed.py

處理「投爐」動作的個人回覆(私訊/摸熊神社)。

只驗證過 2 則實際樣本(一般貢獻 1 則、引爆 1 則)。引爆那則的「效果描述」
(討伐次數補滿/福袋加成之類)目前用「抓標頭跟👁️之間的所有行」這種通用
方式處理,沒有針對每種爆射色系分別寫死欄位——目前只看過橙焰/綠焰兩種
色系,效果內容差異很大(一個是討伐上限、一個是福袋加成倍率),硬要拆成
結構化欄位反而容易在遇到第三種色系時整組壞掉,所以先用「原始文字行」
的方式保留,之後color/效果的組合看多了、如果真的需要精準判斷數值(例如
第二階段傷害最大化要用到討伐上限的實際數字),再回頭視需要另外抽欄位。

signature(): 判斷是不是「投爐」回覆
parse(): 抽成結構化資料
format_for_display(): 組出摘要文字
"""
import re

import furnace_matcher

RE_FEED = re.compile(r"你投入\s*✨?通用碎片\s*[×x](?P<count>\d+),爐能\s*\+(?P<gain>\d+)%")
RE_FEED_TOTAL = re.compile(r"爐能來到\s*(?P<total>\d+)%")
RE_EXPLODED = re.compile(r"爐子……爐子炸了")
RE_BURST_HEADER_LINE = re.compile(r"(?:這次的爆射會是|這一爆是|這次是)【[^】]+】")


def signature(text):
    return "你投入" in text and "爐能" in text


def _extract_effect_lines(text):
    """引爆時,標頭行跟「👁️」(爐火之眼登記)那行之間的所有非空行,
    當作「這次爆射附帶的效果描述」原始文字保留,不做結構化拆解
    (理由見檔頭說明)。"""
    lines = text.splitlines()
    collecting = False
    effect_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if RE_BURST_HEADER_LINE.search(stripped):
            collecting = True
            continue
        if collecting:
            if stripped.startswith("👁️") or stripped.startswith("⚠️") or stripped.startswith("🧔"):
                break
            effect_lines.append(stripped)
    return effect_lines


def parse(text):
    feed = RE_FEED.search(text)
    total = RE_FEED_TOTAL.search(text)
    exploded = bool(RE_EXPLODED.search(text))
    burst = furnace_matcher.FurnaceParser.parse_burst(text) if exploded else None

    return {
        "shard_count": int(feed.group("count")) if feed else None,
        "energy_gain_pct": int(feed.group("gain")) if feed else None,
        "energy_total_pct": int(total.group("total")) if total else None,
        "exploded": exploded,
        "burst_color": burst.color if burst else None,
        "burst_name": burst.burst_name if burst else None,
        "cascade_count": burst.cascade_count if burst else None,
        "ignitor_name": burst.ignitor_name if burst else None,
        "eye_registered_name": burst.eye_registered_name if burst else None,
        "eye_registered_boss": burst.eye_registered_boss if burst else None,
        "effect_lines": _extract_effect_lines(text) if exploded else [],
    }


def format_for_display(parsed):
    lines = []

    if parsed["shard_count"] is not None:
        lines.append(f"🔥 投爐：投入通用碎片 ×{parsed['shard_count']}，爐能 +{parsed['energy_gain_pct']}%")

    if not parsed["exploded"] and parsed["energy_total_pct"] is not None:
        lines.append(f"　　爐能來到 {parsed['energy_total_pct']}%")

    if parsed["exploded"]:
        lines.append("💥 爐子炸了！熔爐大爆射")
        if parsed["cascade_count"]:
            lines.append(f"　　這段時間共爆 {parsed['cascade_count']} 次")
        if parsed["ignitor_name"]:
            lines.append(f"　　引爆者：{parsed['ignitor_name']}")
        if parsed["burst_color"]:
            lines.append(f"🔥 這一爆是【{parsed['burst_color']}・{parsed['burst_name']}】")
        for effect in parsed["effect_lines"]:
            lines.append(f"　　✨ {effect}")
        if parsed["eye_registered_name"]:
            lines.append(
                f"👁️ 【爐火之眼】{parsed['eye_registered_name']} 的報酬已登記"
                f"——「{parsed['eye_registered_boss']}」倒下時發放"
            )

    return "\n".join(lines) if lines else "(投爐訊息解析失敗)"