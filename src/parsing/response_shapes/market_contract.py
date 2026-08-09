"""
shapes/market_contract.py

處理「商契」指令的回應——星環市集(投資類)第一個常用指令 shape。

原始格式:
    💼 我的商契
    ──────────────
    🃏【錢莊】60000 份　均價 30.8 → 現價 44.72　帳面 +836510
    ...
    ──────────────
    市值 66567700｜成本 54583010｜帳面 +11984690｜已實現 +63828151
    💰 點數 10304704

signature(): 判斷一段文字是不是「商契」形狀,給 dispatcher 用
parse(): 抽成結構化資料
format_for_display(): 組出帶漲跌標示的顯示文字
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
    lines = ["💼 我的商契", "──────────────"]
    for h in parsed["holdings"]:
        marker = _trend_marker(h["pct"])
        sign = "+" if h["pct"] >= 0 else ""
        lines.append(
            f"{h['emoji']}【{h['name']}】{h['shares']} 份　"
            f"{marker} {sign}{h['pct']}%　帳面 {h['pnl']:+}"
        )
    lines.append("──────────────")
    s = parsed["summary"]
    if s:
        lines.append(
            f"市值 {s.get('market_value', 0)}｜成本 {s.get('cost_total', 0)}｜"
            f"帳面 {s.get('pnl_total', 0):+}｜已實現 {s.get('realized', 0):+}"
        )
        lines.append(f"💰 點數 {s.get('points', 0)}")
    return "\n".join(lines)