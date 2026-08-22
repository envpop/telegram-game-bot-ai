# -*- coding: utf-8 -*-
"""
response_shapes/satellite_training_complete.py —— 「群星計畫結業」shape

catalog 裡的 main_menu 是每回合都會出現、有按鈕的選單；這裡是完全不同的
畫面——沒有按鈕，是這次培育 session 真正的最後一則訊息，要求打字回覆
「結業 名字」才能完成。

三行必定會出現的錨點格式（熊 2026-08-22 提供的真實樣本，跨多個 session
都一致）：
    ──────────────
    衛星數值：攻 X／防 Y／耐 Z
    習得技能：技能1、技能2、...
    ──────────────
    打「結業 你想取的名字」命名並完成你的衛星陀螺！
"""
import re

_ANCHOR = "打「結業 你想取的名字」命名並完成你的衛星陀螺！"
_STATS_PATTERN = re.compile(r"攻\s*(\d+)／防\s*(\d+)／耐\s*(\d+)")
_SKILLS_LINE_PATTERN = re.compile(r"習得技能：(.+)")


def signature(text):
    return _ANCHOR in text


def parse(text):
    stats_match = _STATS_PATTERN.search(text)
    stats = None
    if stats_match:
        stats = {
            "attack": int(stats_match.group(1)),
            "defense": int(stats_match.group(2)),
            "stamina": int(stats_match.group(3)),
        }

    skills = []
    skills_match = _SKILLS_LINE_PATTERN.search(text)
    if skills_match:
        skills = [s.strip() for s in skills_match.group(1).split("、") if s.strip()]

    return {"stats": stats, "skills": skills}


def format_for_display(parsed):
    stats = parsed.get("stats")
    skills = parsed.get("skills", [])
    stats_str = (
        f"攻{stats['attack']}／防{stats['defense']}／耐{stats['stamina']}"
        if stats else "數值解析失敗"
    )
    return f"🌌 群星計畫結業（{stats_str}，習得：{'、'.join(skills) or '無'}）"
