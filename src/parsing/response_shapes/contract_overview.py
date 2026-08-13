"""
shapes/contract_overview.py

處理「契約所」指令的回應——星環契約所（道具指數選擇權），跟「星環
市集」是不同的經濟體系：市集交易的是股票型商品，契約所交易的是道具
指數的 24 小時方向性契約（付契約金鎖現價，24 小時後看漲跌賺價差）。
結構上很像市集（本盤/今日 % 同一套格式），但多了「契約金」跟「每契約
換算幾個道具」，資料不該混進 market_prices.jsonl。

signature(): 判斷一段文字是不是「契約所」形狀
parse(): 抽成結構化資料
format_for_display(): 一行一個商品，原文本身格式已經清楚，不用大改
"""
import re

HEADER_PREFIX = "📜 星環契約所"

ITEM_PATTERN = re.compile(
    r"(?P<idx>\d+)\.\s*(?P<emoji>\S+)【(?P<name>[^】]+)】(?P<full_name>[^\(]+?)"
    r"\(每契約\s*(?P<unit_per_contract>\d+)\s*個\)\s*\n"
    r"\s*(?P<arrow>[🔻🔺─])\s*(?P<price>[\d.]+)\s*本盤\s*(?P<round_pct>[+-]?[\d.]+)%\s*"
    r"今日\s*(?P<day_pct>[+-]?[\d.]+)%\s*契約金\s*(?P<premium>\d+)\s*點"
)
DAILY_QUOTA_PATTERN = re.compile(
    r"今日已訂\s*(?P<used>\d+)/(?P<limit>\d+)\s*張[,，]持倉\s*(?P<holding_contracts>\d+)\s*張"
)
POINTS_PATTERN = re.compile(r"點數\s*(?P<points>\d+)")


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
            "unit_per_contract": int(m.group("unit_per_contract")),
            "price": float(m.group("price")),
            "round_pct": float(m.group("round_pct")),
            "day_pct": float(m.group("day_pct")),
            "premium": int(m.group("premium")),
        })

    quota = None
    m = DAILY_QUOTA_PATTERN.search(text)
    if m:
        quota = {
            "used": int(m.group("used")),
            "limit": int(m.group("limit")),
            "holding_contracts": int(m.group("holding_contracts")),
        }

    points = None
    m = POINTS_PATTERN.search(text)
    if m:
        points = int(m.group("points"))

    return {"items": items, "quota": quota, "points": points}


def format_for_display(parsed):
    lines = ["📜 星環契約所", "──────────────"]
    for it in parsed["items"]:
        arrow = "🔻" if it["round_pct"] < 0 else ("🔺" if it["round_pct"] > 0 else "─")
        lines.append(
            f"{it['index']}. {it['emoji']}【{it['name']}】{it['full_name']}"
            f"(每契約 {it['unit_per_contract']} 個)　"
            f"{arrow} {it['price']}　本盤 {it['round_pct']:+.1f}%　"
            f"今日 {it['day_pct']:+.1f}%　契約金 {it['premium']} 點"
        )
    lines.append("──────────────")
    q = parsed.get("quota")
    if q:
        lines.append(f"🫵 今日已訂 {q['used']}/{q['limit']} 張，持倉 {q['holding_contracts']} 張")
    if parsed.get("points") is not None:
        lines.append(f"💰 點數 {parsed['points']}")
    return "\n".join(lines)
