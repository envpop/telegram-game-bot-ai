"""
shapes/world_boss_battle_report.py

處理每次「討伐」指令後,摸熊神社回覆的戰報——跟 world_boss_status.py 是不同
shape:這則訊息沒有「今日世界王【第 X 階】」那行標頭,王名/類型/屬性改用
「⚔️【共鬥討伐】世界王 ○○（類型・屬性）」這種格式;護衛存活數也少了斜線
(「衛星護衛 6 顆還在」,不是「6/6 顆還在」),所以獨立成一支模組,不共用
world_boss_status 的標頭 regex。

原始格式(節錄自實際樣本):
    ⚔️【共鬥討伐】世界王 深淵級・沉鐘（防禦型・🟡土屬性）
    🔮 弱點屬性:🟢木屬性(帶 🟢木屬性 五行的陀螺打傷害更高)
    ──────────────
    R1 猛烈對撞：你 -0💥／深淵級・沉鐘 -190
    ──────────────
    你這次造成 771 傷害
    🛰️ 衛星護衛 6 顆還在:王減傷 52%（打「護衛」看誰最好拆）
    今日 2/16 次,本王累積 5056 傷害

刻意「以少控多」:中間逐刀明細(R1/R3 那幾行連段技、暴擊、群體壓制等)不解析,
只抓跟「下一步該怎麼打」有關的欄位(弱點、護衛)跟結果數字(傷害、次數)。

signature(): 判斷一段文字是不是這個 shape
parse(): 抽成結構化資料
format_for_display(): 組出精簡摘要文字
"""
import re

import weakness_matcher

# 標頭: ⚔️【共鬥討伐】世界王 深淵級・沉鐘（防禦型・🟡土屬性）
RE_HEADER = re.compile(
    r"⚔️【共鬥討伐】世界王\s*(?P<name>[^（]+)"
    r"（(?P<type>[^・]+)・\S*?(?P<element>[火土金木水])屬性）"
)

RE_DAMAGE_DEALT = re.compile(r"你這次造成\s*([\d,]+)\s*傷害")

# 這裡的護衛行沒有斜線(跟公告/查詢那種「X/Y 顆還在」不同格式),
# 所以獨立一條 regex,不重用 weakness_matcher.RE_GUARDS_ALIVE。
RE_GUARDS_NO_SLASH = re.compile(r"衛星護衛\s*(\d+)\s*顆還在")

RE_DAILY_COUNT = re.compile(r"今日\s*(\d+)/(\d+)\s*次")
RE_ACCUMULATED = re.compile(r"本王累積\s*([\d,]+)\s*傷害")


def signature(text):
    return text.strip().startswith("⚔️【共鬥討伐】世界王")


def parse(text):
    header = RE_HEADER.search(text)
    weakness = weakness_matcher.WeaknessParser.parse(text)
    guards_m = RE_GUARDS_NO_SLASH.search(text)
    damage_m = RE_DAMAGE_DEALT.search(text)
    daily_m = RE_DAILY_COUNT.search(text)
    accum_m = RE_ACCUMULATED.search(text)

    return {
        "boss_name": header.group("name") if header else None,
        "boss_type": header.group("type") if header else None,
        "boss_element": header.group("element") if header else None,
        "current_element": weakness.current_element if weakness else None,
        "damage_dealt": int(damage_m.group(1).replace(",", "")) if damage_m else None,
        "has_guards": (int(guards_m.group(1)) > 0) if guards_m else None,
        "guards_remaining": int(guards_m.group(1)) if guards_m else None,
        "daily_count": int(daily_m.group(1)) if daily_m else None,
        "daily_limit": int(daily_m.group(2)) if daily_m else None,
        "accumulated_damage": int(accum_m.group(1).replace(",", "")) if accum_m else None,
    }


def format_for_display(parsed):
    lines = []
    if parsed["boss_name"]:
        type_element = ""
        if parsed["boss_type"] and parsed["boss_element"]:
            type_element = f"（{parsed['boss_type']}・{parsed['boss_element']}屬性）"
        lines.append(f"⚔️ {parsed['boss_name']}{type_element}")

    if parsed["current_element"]:
        lines.append(f"🔮 弱點：{parsed['current_element']}屬性")

    if parsed["damage_dealt"] is not None:
        lines.append(f"💥 這次造成 {parsed['damage_dealt']} 傷害")

    if parsed["has_guards"] is True:
        lines.append(f"🛰️ 護衛：還有 {parsed['guards_remaining']} 顆")
    elif parsed["has_guards"] is False:
        lines.append("🛰️ 護衛：已清空")

    if parsed["daily_count"] is not None and parsed["daily_limit"] is not None:
        lines.append(f"🫵 今日 {parsed['daily_count']}/{parsed['daily_limit']} 次")

    if parsed["accumulated_damage"] is not None:
        lines.append(f"📊 本王累積 {parsed['accumulated_damage']} 傷害")

    return "\n".join(lines) if lines else "(世界王戰報解析失敗)"
