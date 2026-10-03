# -*- coding: utf-8 -*-
"""
response_shapes/backpack.py —— 「背包」shape

跟其他 response_shapes 檔案一樣的三函式介面：
    signature(text) -> bool
    parse(text) -> dict
    format_for_display(parsed) -> str

解析邏輯不重寫，直接複用 backpack_watcher.py 既有的
is_backpack_message() / parse_backpack()——這裡只是薄薄的介面轉接。
parse() 的輸出結構完全沒動，profile_sync_strategy.py 的存檔路徑不受影響。

存檔（save_inventory_snapshot 等）不在這層做，呼叫端
（profile_sync_strategy.py）才是負責「要不要存檔」的地方。

【顯示設計】
目標：讓人一眼看出「哪種物資的哪個階數缺」。做法：
  1. 同一種物資的各階數放在同一行（爆石 / 爆石2 / 爆石3 / 爆石4 → 一行四格）。
  2. 同分類內，階數欄位上下對齊；該階數背包裡沒有就顯示 EMPTY_CELL。
  3. 數字加千分位、靠右對齊，位數差異不會干擾比較。

【階數怎麼判斷】
  - 名稱結尾的數字視為階數：「爆石3」→ (爆石, 3)；沒有結尾數字視為 1 階。
  - 名稱不照這規則的物資（例如 福袋／進階福袋／四階福袋／特級福袋）
    請填進 TIER_ALIASES：{"進階福袋": ("福袋", 2)}。沒填的就各自獨立一行，
    不會壞，只是不會併成同一行。
  - 「10連」「30連」這類是不同面額、不是階數，不需要特別處理，各自一行。

【已知限制】
  - 背包訊息只列出數量 > 0 的物品，所以「缺」＝該格沒出現，不是 0。
  - 對齊用 east_asian_width 估算字寬（全形=2），等寬字型的終端機才會完全對齊；
    emoji 只出現在分類標題行，不參與欄位對齊。
  - _display_width / _pad 若之後有別的顯示模組也要用，再抽到 format_utils.py
    （目前只有這裡用，先不抽）。
"""

import re
import unicodedata

from backpack_watcher import is_backpack_message, parse_backpack


# 名稱結尾數字 = 階數
_TIER_SUFFIX = re.compile(r"^(.+?)(\d+)$")

# 不符合「結尾數字」規則的階數對照：{"物品全名": ("基底名稱", 階數)}
# 福袋系列的順序請依遊戲實際階數填寫，例如：
#   "進階福袋": ("福袋", 2), "四階福袋": ("福袋", 4), "特級福袋": ("福袋", 5)
TIER_ALIASES = {}

EMPTY_CELL = "－"
CELL_SEP = " │ "
INDENT = "  "


def signature(text):
    return is_backpack_message(text)


def parse(text):
    return parse_backpack(text)


# ── 顯示用小工具 ─────────────────────────────────────────

def _display_width(s):
    """終端機顯示寬度：全形/寬字元算 2，其餘算 1。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _pad_right(s, width):
    return s + " " * max(0, width - _display_width(s))


def _pad_left(s, width):
    return " " * max(0, width - _display_width(s)) + s


def _split_tier(name):
    """物品全名 → (基底名稱, 階數)。"""
    if name in TIER_ALIASES:
        return TIER_ALIASES[name]
    m = _TIER_SUFFIX.match(name)
    if m:
        return m.group(1), int(m.group(2))
    return name, 1


def _group_items(items):
    """
    items → {分類: {基底名稱: {階數: 數量}}}
    分類與物資的先後順序維持遊戲原本的出現順序（dict 保序）；
    階數在輸出時才排序。
    """
    grouped = {}
    for item in items:
        base, tier = _split_tier(item["name"])
        families = grouped.setdefault(item["category"], {})
        tiers = families.setdefault(base, {})
        tiers[tier] = tiers.get(tier, 0) + item["count"]
    return grouped


def _format_category(category, families):
    """一個分類 → 若干行文字（標題行 + 可選階數表頭 + 每種物資一行）。"""
    tiers = sorted({t for tier_map in families.values() for t in tier_map})
    name_w = max(_display_width(base) for base in families)

    # 每個階數欄的寬度 = 該欄最寬的數字（表頭「N階」也算）
    col_w = {}
    for t in tiers:
        cells = [f"{tier_map[t]:,}" for tier_map in families.values() if t in tier_map]
        col_w[t] = max([_display_width(f"{t}階")] + [_display_width(c) for c in cells])

    lines = [category]

    # 只有出現過 2 階以上才需要表頭；全部都是 1 階的分類不加，保持乾淨
    if len(tiers) > 1 or tiers[0] != 1:
        header = CELL_SEP.join(_pad_left(f"{t}階", col_w[t]) for t in tiers)
        lines.append(INDENT + " " * name_w + "   " + header)

    for base, tier_map in families.items():
        cells = [
            _pad_left(f"{tier_map[t]:,}" if t in tier_map else EMPTY_CELL, col_w[t])
            for t in tiers
        ]
        lines.append(INDENT + _pad_right(base, name_w) + "   " + CELL_SEP.join(cells))

    return lines


def format_for_display(parsed):
    lines = ["🎒 背包"]

    for category, families in _group_items(parsed.get("items", [])).items():
        lines.extend(_format_category(category, families))

    fragments = parsed.get("fragments", [])
    if fragments:
        lines.append("── 碎片 ──")
        name_w = max(_display_width(f["name"]) for f in fragments)
        for f in fragments:
            lines.append(
                INDENT + _pad_right(f["name"], name_w) + "   " + f"{f['current']}/{f['total']}"
            )

    return "\n".join(lines)