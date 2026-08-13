"""
shapes/world_boss_status.py

處理「世界王」查詢指令的回覆／王剛降臨的公告——兩者共用同一種標頭格式,
所以這裡的 parse() 邏輯刻意跟訊息來自哪個 chat 無關(純文字 → 結構化資料)。
目前只有摸熊神社(私訊)裡的查詢回覆會實際走到這支模組,因為公告頻道走
ANNOUNCEMENT 分支(見 parsing/flow_parser.py),還沒接上 shape dispatch；
之後若要幫公告頻道也做結構化,可以直接重用這支模組,不用重寫。

原始格式(節錄自實際樣本):
    👹 今日世界王【第 7 階】:深淵級・沉鐘（防禦型・🟡土屬性）
    👁️【千面之淵】階級技能〈淵變〉:王每被打掉 20% 血就隨機置換一次五行,弱點屬性跟著重算
    🔮 弱點屬性:🟢木屬性(帶 🟢木屬性 五行的陀螺打傷害更高)
    🌗 相位 2/3【崩相】　總進度 🌫??
    🛰️ 衛星護衛 6/6 顆還在 — 王減傷 52%（王照樣打得到）｜打「護衛」看弱點、「清護衛」拆一顆

王名/相位/弱點/護衛的抓取直接重用 weakness_matcher.WeaknessParser,避免跟
weakness_matcher.py 裡已經驗證過的 regex 邏輯重複維護兩份；這支模組只額外
負責標頭那行才有的資訊(第幾階、王的類型與本體屬性)。

signature(): 判斷一段文字是不是這個 shape
parse(): 抽成結構化資料
format_for_display(): 組出精簡摘要文字
"""
import re

import weakness_matcher

# 標頭: 👹 今日世界王【第 7 階】:深淵級・沉鐘（防禦型・🟡土屬性）
RE_HEADER = re.compile(
    r"今日世界王【第\s*(?P<stage>\d+)\s*階】[:：]\s*(?P<name>[^（]+)"
    r"（(?P<type>[^・]+)・\S*?(?P<element>[火土金木水])屬性）"
)

# 王已被討伐時,查詢回覆會帶這個字樣。照 world_boss_catalog.json 的
# status_query.alive_check_pattern 假設,尚未取得實際的「已死」回覆樣本驗證過,
# 之後遇到真樣本要記得回頭確認格式是否一致。
RE_ALIVE_CHECK = re.compile(r"已被討伐 ✅")


def signature(text):
    return "今日世界王【第" in text


def parse(text):
    header = RE_HEADER.search(text)
    weakness = weakness_matcher.WeaknessParser.parse(text)

    return {
        "stage": int(header.group("stage")) if header else None,
        "boss_name": header.group("name") if header else (weakness.boss_name if weakness else None),
        "boss_type": header.group("type") if header else None,
        "boss_element": header.group("element") if header else None,
        "current_element": weakness.current_element if weakness else None,
        "phase": weakness.phase if weakness else None,
        "phase_total": weakness.phase_total if weakness else None,
        "has_guards": weakness.has_guards if weakness else None,
        "alive": not bool(RE_ALIVE_CHECK.search(text)),
    }


def format_for_display(parsed):
    lines = []
    if parsed["boss_name"]:
        stage = f"第{parsed['stage']}階 " if parsed["stage"] else ""
        type_element = ""
        if parsed["boss_type"] and parsed["boss_element"]:
            type_element = f"（{parsed['boss_type']}・{parsed['boss_element']}屬性）"
        lines.append(f"👹 {stage}{parsed['boss_name']}{type_element}")

    if not parsed["alive"]:
        lines.append("💀 已被討伐")

    if parsed["current_element"]:
        lines.append(f"🔮 弱點：{parsed['current_element']}屬性")

    if parsed["phase"] and parsed["phase_total"]:
        lines.append(f"🌗 相位 {parsed['phase']}/{parsed['phase_total']}")

    if parsed["has_guards"] is True:
        lines.append("🛰️ 護衛：還有存活")
    elif parsed["has_guards"] is False:
        lines.append("🛰️ 護衛：已清空")

    return "\n".join(lines) if lines else "(世界王狀態解析失敗)"
