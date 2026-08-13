"""
shapes/market_contract.py

處理「商契」指令的回應——星環市集(投資類)常用指令 shape。

signature(): 判斷一段文字是不是「商契」形狀
parse(): 抽成結構化資料
format_for_display(): 保留原文大部分內容，只在每筆持倉行尾附加漲跌標示

這個檔案保持無狀態，不碰檔案 I/O、不記得任何跨訊息的東西——
時間序列存檔跟跨指令行情脈動，是 market_tracking_strategy.py 的責任。
"""
import re

HOLDING_LINE = re.compile(
    r"^(?P<emoji>\S+)【(?P<name>[^】]+)】(?P<shares>\d+)\s*份\s*"
    r"均價\s*(?P<cost>[\d.]+)\s*→\s*現價\s*(?P<price>[\d.]+)\s*"
    r"帳面\s*(?P<pnl>[+-]?\d+)"
)
SUMMARY_LINE = re.compile(
    r"市值\s*(?P<market_value>\d+)｜成本\s*(?P<cost_total>\d+)｜"
    r"帳面\s*(?P<pnl_total>[+-]?\d+)｜已實現\s*(?P<realized>[+-]?\d+)"
)
POINTS_LINE = re.compile(r"點數\s*(?P<points>\d+)")


def signature(text):
    return text.strip().startswith("💼 我的商契")


def parse(text):
    holdings = []
    summary = {}
    for line in text.split("\n"):
        line = line.strip()
        m = HOLDING_LINE.match(line)
        if m:
            cost = float(m.group("cost"))
            price = float(m.group("price"))
            pct = (price - cost) / cost * 100 if cost else 0.0
            holdings.append({
                "emoji": m.group("emoji"),
                "name": m.group("name"),
                "shares": int(m.group("shares")),
                "cost": cost,
                "price": price,
                "pnl": int(m.group("pnl")),
                "pct": round(pct, 2),
            })
            continue
        m = SUMMARY_LINE.search(line)
        if m:
            summary.update({
                "market_value": int(m.group("market_value")),
                "cost_total": int(m.group("cost_total")),
                "pnl_total": int(m.group("pnl_total")),
                "realized": int(m.group("realized")),
            })
        m = POINTS_LINE.search(line)
        if m:
            summary["points"] = int(m.group("points"))
    return {"holdings": holdings, "summary": summary}


def _trend_marker(pct):
    if pct > 0:
        return "▲"
    if pct < 0:
        return "▼"
    return "─"


def format_for_display(parsed):
    """保留原文大部分內容（均價/現價/帳面都還在），只在行尾多附一段
    漲跌標示，不是重寫整行——資訊量比原文多一點點，不是取代。"""
    lines = ["💼 我的商契", "──────────────"]
    for h in parsed["holdings"]:
        marker = _trend_marker(h["pct"])
        sign = "+" if h["pct"] >= 0 else ""
        original = (
            f"{h['emoji']}【{h['name']}】{h['shares']} 份　"
            f"均價 {h['cost']} → 現價 {h['price']}　帳面 {h['pnl']:+}"
        )
        lines.append(f"{original}　{marker} {sign}{h['pct']}%")
    lines.append("──────────────")
    s = parsed["summary"]
    if s:
        lines.append(
            f"市值 {s.get('market_value', 0)}｜成本 {s.get('cost_total', 0)}｜"
            f"帳面 {s.get('pnl_total', 0):+}｜已實現 {s.get('realized', 0):+}"
        )
        lines.append(f"💰 點數 {s.get('points', 0)}")
    return "\n".join(lines)