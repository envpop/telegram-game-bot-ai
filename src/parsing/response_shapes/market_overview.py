"""
shapes/market_overview.py

處理「市集」指令的回應——星環市集(投資類)全市場行情，涵蓋所有商品，
不像商契只列出你持有的部位。這是補齊全市場價格資料的主要來源。

signature(): 判斷一段文字是不是「市集」形狀
parse(): 抽成結構化資料
format_for_display(): 原文本身格式已經很清楚（本盤/今日都直接列出），
    不需要大改，重新組裝只是確保跟結構化資料一致
"""
import re

HEADER_PREFIX = "🏮 星環市集"

ITEM_PATTERN = re.compile(
    r"(?P<idx>\d+)\.\s*(?P<emoji>\S+)【(?P<name>[^】]+)】(?P<full_name>\S+)\s*\n"
    r"\s*(?P<arrow>[🔻🔺─])\s*(?P<price>[\d.]+)\s*本盤\s*(?P<round_pct>[+-]?[\d.]+)%\s*"
    r"今日\s*(?P<day_pct>[+-]?[\d.]+)%"
)
NEWS_PATTERN = re.compile(r"🗞️\s*(?P<news>.+)")
POINTS_PATTERN = re.compile(r"你的點數[:：]\s*(?P<points>\d+)")


def signature(text):
    return text.strip().startswith(HEADER_PREFIX)


def parse(text):
    items = []
    for m in ITEM_PATTERN.finditer(text):
        items.append({
            "index": int(m.group("idx")),
            "emoji": m.group("emoji"),
            "name": m.group("name"),
            "full_name": m.group("full_name"),
            "price": float(m.group("price")),
            "round_pct": float(m.group("round_pct")),
            "day_pct": float(m.group("day_pct")),
        })

    news = None
    m = NEWS_PATTERN.search(text)
    if m:
        news = m.group("news").strip()

    points = None
    m = POINTS_PATTERN.search(text)
    if m:
        points = int(m.group("points"))

    return {"items": items, "news": news, "points": points}


def format_for_display(parsed):
    """基礎版本，一行一個商品。strategy 層(market_tracking_strategy)通常
    會用這份結構化資料重新組一份帶個人盈虧的加強版蓋掉這裡的輸出；
    這裡只是 parser 自己單獨測試、或還沒接 strategy 時的基礎顯示。"""
    lines = ["🏮 星環市集", "──────────────"]
    for it in parsed["items"]:
        arrow = "🔻" if it["round_pct"] < 0 else ("🔺" if it["round_pct"] > 0 else "─")
        lines.append(
            f"{it['index']}. {it['emoji']}【{it['name']}】{it['full_name']}　"
            f"{arrow} {it['price']}　本盤 {it['round_pct']:+.1f}%　今日 {it['day_pct']:+.1f}%"
        )
    lines.append("──────────────")
    if parsed.get("news"):
        lines.append(f"🗞️ {parsed['news']}")
    if parsed.get("points") is not None:
        lines.append(f"💰 點數 {parsed['points']}")
    return "\n".join(lines)