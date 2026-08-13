"""
shapes/market_quote.py

處理「行情 [商品]」指令的回應——星環市集(投資類)單一商品走勢。

跟商契/市集不一樣的地方：這則訊息如果你持有這個商品，會額外夾帶
「🫵 你持有 X 份商契，均價 Y，帳面 Z 點」這行，直接給出這個商品的
個人持倉資訊，格式跟商契的持倉列一致。

「行情」指令實際上會發兩則訊息：這裡處理的文字那則；緊接著的圖片
那則不處理——圖表內容(現價/本盤/今日)跟這則文字完全重複，而且
DOWNLOAD_MEDIA_ENABLED 預設關閉，圖片根本沒有下載，沒有東西可以
額外解析，交給既有的「有圖片」fallback 顯示就夠了。

signature(): 判斷一段文字是不是「行情」形狀
parse(): 抽成結構化資料
format_for_display(): 原文本身已經很精簡，維持原樣重組
"""
import re

HEADER_LINE = re.compile(r"^(?P<emoji>\S+)【(?P<name>[^】]+)】(?P<full_name>\S+)$")
QUOTE_LINE = re.compile(
    r"^現價\s*(?P<price>[\d.]+)\s*本盤\s*(?P<round_pct>[+-]?[\d.]+)%\s*今日\s*(?P<day_pct>[+-]?[\d.]+)%$"
)
HOLDING_LINE = re.compile(
    r"你持有\s*(?P<shares>\d+)\s*份商契[,，]均價\s*(?P<cost>[\d.]+)[,，]帳面\s*(?P<pnl>[+-]?\d+)\s*點"
)


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

    price = round_pct = day_pct = None
    reward_hint = None
    holding = None

    for line in lines[1:]:
        m = QUOTE_LINE.match(line)
        if m:
            price = float(m.group("price"))
            round_pct = float(m.group("round_pct"))
            day_pct = float(m.group("day_pct"))
            continue
        m = HOLDING_LINE.search(line)
        if m:
            holding = {
                "shares": int(m.group("shares")),
                "cost": float(m.group("cost")),
                "pnl": int(m.group("pnl")),
            }
            continue
        if line.startswith("🎁"):
            reward_hint = line

    return {
        "name": name,
        "full_name": full_name,
        "price": price,
        "round_pct": round_pct,
        "day_pct": day_pct,
        "reward_hint": reward_hint,
        "holding": holding,
    }


def format_for_display(parsed):
    lines = [f"【{parsed['name']}】{parsed['full_name']}"]
    if parsed["price"] is not None:
        lines.append(
            f"現價 {parsed['price']}　本盤 {parsed['round_pct']:+.1f}%　今日 {parsed['day_pct']:+.1f}%"
        )
    if parsed.get("reward_hint"):
        lines.append(parsed["reward_hint"])
    h = parsed.get("holding")
    if h:
        lines.append(f"🫵 你持有 {h['shares']} 份商契，均價 {h['cost']}，帳面 {h['pnl']:+} 點")
    return "\n".join(lines)