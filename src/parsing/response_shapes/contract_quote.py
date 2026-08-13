"""
shapes/contract_quote.py

處理「契約行情 [商品]」指令的回應——契約所單一道具指數的走勢查詢，
跟「行情」是不同的經濟體系（見 contract_overview.py 的說明）。
一樣會附帶一張 6 小時走勢圖（緊接著的圖片訊息），跟「行情」共用同一套
chart_worker 像素解析（圖表格式已確認很像），但資料要記到契約所自己
的價格檔案，不要跟股票商品混在一起。

signature(): 判斷一段文字是不是「契約行情」形狀（用開頭的 📜 跟
    「指數(每契約」這個組合跟一般的 market_quote 區分開，market_quote
    的第一行沒有 📜 前綴）
parse(): 抽成結構化資料
format_for_display(): 原文本身已經很精簡，維持原樣重組
"""
import re

HEADER_LINE = re.compile(
    r"^📜\s*(?P<emoji>\S+)【(?P<name>[^】]+)】(?P<full_name>[^\s]+?)\s*指數"
    r"\(每契約\s*(?P<unit_per_contract>\d+)\s*個\)$"
)
QUOTE_LINE = re.compile(
    r"^現價\s*(?P<price>[\d.]+)\s*本盤\s*(?P<round_pct>[+-]?[\d.]+)%\s*今日\s*(?P<day_pct>[+-]?[\d.]+)%$"
)
PREMIUM_LINE = re.compile(r"契約金\s*(?P<premium>\d+)\s*點")


def signature(text):
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    if len(lines) < 2:
        return False
    return bool(HEADER_LINE.match(lines[0])) and lines[1].startswith("現價")


def parse(text):
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    header = HEADER_LINE.match(lines[0])
    name = header.group("name") if header else None
    full_name = header.group("full_name") if header else None
    unit_per_contract = int(header.group("unit_per_contract")) if header else None

    price = round_pct = day_pct = premium = None
    reward_hint = None

    for line in lines[1:]:
        m = QUOTE_LINE.match(line)
        if m:
            price = float(m.group("price"))
            round_pct = float(m.group("round_pct"))
            day_pct = float(m.group("day_pct"))
            continue
        m = PREMIUM_LINE.search(line)
        if m:
            premium = int(m.group("premium"))
        if line.startswith("🎁"):
            reward_hint = line

    return {
        "name": name,
        "full_name": full_name,
        "unit_per_contract": unit_per_contract,
        "price": price,
        "round_pct": round_pct,
        "day_pct": day_pct,
        "premium": premium,
        "reward_hint": reward_hint,
    }


def format_for_display(parsed):
    lines = [f"📜【{parsed['name']}】{parsed['full_name']} 指數(每契約 {parsed['unit_per_contract']} 個)"]
    if parsed["price"] is not None:
        lines.append(
            f"現價 {parsed['price']}　本盤 {parsed['round_pct']:+.1f}%　今日 {parsed['day_pct']:+.1f}%"
        )
    if parsed.get("premium") is not None:
        lines.append(f"契約金 {parsed['premium']} 點")
    if parsed.get("reward_hint"):
        lines.append(parsed["reward_hint"])
    return "\n".join(lines)
