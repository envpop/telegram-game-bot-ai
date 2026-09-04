# -*- coding: utf-8 -*-
"""
shapes/furnace_overview.py

處理「大熔爐」查詢指令的回覆。跟 world_boss_status.py 在世界王系統裡的
角色類似:提供爐火系統目前的完整狀態,而不是只回一行摘要。

只驗證過 2 則實際樣本(顫動進行中 1 則、爐火沉靜(未顫動) 1 則)。已確認:
未顫動時「爐能」照樣顯示,但「🔥🔥【靈性顫動】中」那段換成「♨️ 爐溫:X/100
點狀進度條」+「爐火沉靜」flavor text,且「🎨 這次的火候」「👁️ 爐火之眼」
兩行會整段消失(不是漏解析)。還沒看過爐火之眼從沒人登記過的畫面跟已登記
但改成「已知是哪隻王」的版本長怎樣,之後補到新樣本再回頭驗證。

火候日曆(fire_calendar)跟七色爆射對照表(color_effects)這兩塊拆出來
特別完整處理,是因為之後做「第二階段傷害最大化」判斷時，會需要知道
「現在/下個時辰是什麼顏色、對應什麼效果」來決定要不要卡時間觀火/投爐——
這是本模組存在的主要理由，不只是好看的顯示而已。

signature(): 判斷是不是「大熔爐」查詢回覆
parse(): 抽成結構化資料
format_for_display(): 組出摘要文字(七色對照表這種穩定不太變動的參考資料,
    顯示時只列現在跟下一色,完整對照表都收在 parsed 裡供其他地方取用)
"""
import re

RE_ENERGY = re.compile(r"爐能[:：]\s*(?P<current>\d+)%\s*/\s*(?P<threshold>\d+)%")
RE_TEMP = re.compile(r"爐溫[:：]\s*(?P<cur>\d+)/(?P<max>\d+)")
RE_AWAKENED = re.compile(r"【靈性顫動】中!到\s*(?P<until>\d{2}:\d{2})\s*為止")
RE_CURRENT_BURST = re.compile(r"這次的火候是【(?P<color>[^・]+)・(?P<name>[^】]+)】(?:[:：](?P<effect>[^\n]+))?")
RE_EYE = re.compile(r"爐火之眼[:：]\s*(?P<name>\S+?)\(爆射後,待世界王倒下才領得到重謝\)")
RE_CALENDAR_ENTRY = re.compile(r"(?:▶\s*現在|(?P<time>\d{2}:\d{2}))\s*\S?【(?P<color>[^】]+)】(?P<name>[^\s\u3000\n]+)")
RE_COLOR_TABLE_ENTRY = re.compile(r"[🔴🟠🟡🟢🔵🌀🟣](?P<name>[^(（【】\n]+)[\(（](?P<effect>[^)）]+)[\)）]")


def signature(text):
    return text.startswith("🔥 世界的大熔爐")


def _extract_calendar(text):
    entries = []
    for line in text.splitlines():
        m = RE_CALENDAR_ENTRY.search(line)
        if m:
            entries.append({
                "is_current": "▶" in line,
                "time": m.group("time"),
                "color": m.group("color"),
                "name": m.group("name"),
            })
    return entries


def _extract_color_table(text):
    section = text.split("七色爆射:", 1)
    if len(section) != 2:
        return {}
    return {m[0]: m[1] for m in RE_COLOR_TABLE_ENTRY.findall(section[1])}


def parse(text):
    energy_m = RE_ENERGY.search(text)
    temp_m = RE_TEMP.search(text)
    awakened_m = RE_AWAKENED.search(text)
    burst_m = RE_CURRENT_BURST.search(text)
    eye_m = RE_EYE.search(text)

    return {
        "energy_current_pct": int(energy_m.group("current")) if energy_m else None,
        "energy_threshold_pct": int(energy_m.group("threshold")) if energy_m else None,
        "temp_current": int(temp_m.group("cur")) if temp_m else None,
        "temp_max": int(temp_m.group("max")) if temp_m else None,
        "awakened": bool(awakened_m),
        "awakened_until": awakened_m.group("until") if awakened_m else None,
        "current_burst_color": burst_m.group("color") if burst_m else None,
        "current_burst_name": burst_m.group("name") if burst_m else None,
        "current_burst_effect": burst_m.group("effect").strip() if (burst_m and burst_m.group("effect")) else None,
        "eye_registrant": eye_m.group("name") if eye_m else None,
        "fire_calendar": _extract_calendar(text),
        "color_effects": _extract_color_table(text),
    }


def format_for_display(parsed):
    lines = []

    if parsed["energy_current_pct"] is not None:
        lines.append(f"🔥 爐能：{parsed['energy_current_pct']}% / {parsed['energy_threshold_pct']}%（爆射門檻）")

    if parsed["awakened"]:
        lines.append(f"🔥🔥 【靈性顫動】中！到 {parsed['awakened_until']} 為止")
        if parsed["current_burst_color"]:
            effect = f"：{parsed['current_burst_effect']}" if parsed["current_burst_effect"] else ""
            lines.append(f"🎨 這次的火候是【{parsed['current_burst_color']}・{parsed['current_burst_name']}】{effect}")
        if parsed["eye_registrant"]:
            lines.append(f"👁️ 爐火之眼：{parsed['eye_registrant']}（待世界王倒下才領得到重謝）")
    elif parsed["temp_current"] is not None:
        lines.append(f"♨️ 爐溫：{parsed['temp_current']}/{parsed['temp_max']}（爐火沉靜）")

    if parsed["fire_calendar"]:
        cal_text = "　".join(
            f"{'▶現在' if e['is_current'] else e['time']}{e['color']}"
            for e in parsed["fire_calendar"]
        )
        lines.append(f"🗓️ 火候日曆：{cal_text}")

    return "\n".join(lines) if lines else "(大熔爐查詢解析失敗)"
