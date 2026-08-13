"""
shapes/trade_confirmation.py

處理「入資」「撤資」指令的交易確認回應——這是即時、權威的交易結果，
比商契/市集/行情的「查詢當下快照」更準，適合用來即時更新持倉資料，
不用等下一次剛好查商契才發現股數/均價變了。

兩種 action 的第二行結構不一樣，不能套同一個欄位結構：

    入資（買進，均價會變）：
        ✅ 入資 🛰️群星通訊 ×1000 份 @ 64.76(含市集規費共 -65085 點)
        持有 101000 份,均價 67.3。剩餘點數 4183674

    撤資（賣出，均價不受影響——均價只在買進時變動，賣出只是減少股數）：
        ✅ 撤資 🌊深淵漁業 ×1000 份 @ 77.44(扣市集規費入帳 +77051 點)
        本筆損益 +34765 點,剩 9000 份。點數 4262177

撤資沒有「均價」欄位（賣出不影響均價，不需要重複告訴你），改成「本筆
損益」（這筆賣出的已實現盈虧）。括號裡的文字兩種 action 也不一樣
（「含市集規費共」vs「扣市集規費入帳」），規則只認裡面的正負數字，
不比對前面那段中文措辭，避免措辭變化就抓不到。

item_label 是「emoji+全名」黏在一起的原始字串（例如
"🛰️群星通訊"），沒有可靠的分界符號可以把 emoji 跟全名拆開。要對應
回 market_snapshot.json 用的短稱 key，交給 strategy 層用「全名→短稱」
對照表查，這裡不負責拆解或對應，維持 parser 無狀態。
"""
import re

HEADER_PATTERN = re.compile(
    r"^✅\s*(?P<action>入資|撤資)\s*(?P<item_label>.+?)\s*×(?P<traded_shares>\d+)\s*份\s*@\s*"
    r"(?P<trade_price>[\d.]+)\([^)]*?(?P<net_change>[+-]\d+)\s*點\)"
)
# 入資結果行：持有 X 份,均價 Y。剩餘點數 Z
BUY_RESULT_PATTERN = re.compile(
    r"持有\s*(?P<shares_after>\d+)\s*份[,，]均價\s*(?P<avg_cost_after>[\d.]+)。剩餘點數\s*(?P<remaining_points>\d+)"
)
# 撤資結果行：本筆損益 X 點,剩 Y 份。點數 Z
SELL_RESULT_PATTERN = re.compile(
    r"本筆損益\s*(?P<realized_pnl>[+-]?\d+)\s*點[,，]剩\s*(?P<shares_after>\d+)\s*份。點數\s*(?P<remaining_points>\d+)"
)


def signature(text):
    return bool(HEADER_PATTERN.match(text.strip()))


def parse(text):
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    header = HEADER_PATTERN.match(lines[0])
    if not header:
        return None

    action = header.group("action")
    result_line = lines[1] if len(lines) > 1 else ""

    shares_after = avg_cost_after = realized_pnl = remaining_points = None

    if action == "入資":
        m = BUY_RESULT_PATTERN.search(result_line)
        if m:
            shares_after = int(m.group("shares_after"))
            avg_cost_after = float(m.group("avg_cost_after"))
            remaining_points = int(m.group("remaining_points"))
    elif action == "撤資":
        m = SELL_RESULT_PATTERN.search(result_line)
        if m:
            realized_pnl = int(m.group("realized_pnl"))
            shares_after = int(m.group("shares_after"))
            remaining_points = int(m.group("remaining_points"))

    return {
        "action": action,
        "item_label": header.group("item_label"),  # emoji+全名，未拆解
        "traded_shares": int(header.group("traded_shares")),
        "trade_price": float(header.group("trade_price")),
        "net_change": int(header.group("net_change")),
        "shares_after": shares_after,
        "avg_cost_after": avg_cost_after,   # 只有入資才有；撤資均價不變，不會給
        "realized_pnl": realized_pnl,       # 只有撤資才有；入資不會有已實現損益
        "remaining_points": remaining_points,
    }


def format_for_display(parsed):
    lines = [
        f"✅ {parsed['action']} {parsed['item_label']} ×{parsed['traded_shares']} 份 @ "
        f"{parsed['trade_price']}({parsed['net_change']:+} 點)"
    ]
    if parsed["action"] == "入資" and parsed.get("shares_after") is not None:
        lines.append(
            f"持有 {parsed['shares_after']} 份，均價 {parsed['avg_cost_after']}。"
            f"剩餘點數 {parsed['remaining_points']}"
        )
    elif parsed["action"] == "撤資" and parsed.get("shares_after") is not None:
        lines.append(
            f"本筆損益 {parsed['realized_pnl']:+} 點，剩 {parsed['shares_after']} 份。"
            f"點數 {parsed['remaining_points']}"
        )
    return "\n".join(lines)