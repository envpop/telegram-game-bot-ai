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
  - 預設：維持原本的簡單排法
        分類｜名稱×數量、名稱×數量
        名稱 目前/總數
  - 只有 TABLE_ITEMS 裡的物資（爆石、盾石、歸石）改用階數表格：
    同一種石頭的 1～4 階放同一行、欄位上下對齊，缺的階數顯示 EMPTY_CELL，
    一眼看出哪種石頭的哪個階數缺。
    同一個分類裡不在 TABLE_ITEMS 的物品（例如二階兌換券）接在表格下面，
    用原本的「名稱×數量」寫法。
        💎 靈魂石
                         1階 │ 2階 │   3階 │ 4階
          爆石         3,031 │ 814 │ 1,805 │   1
          盾石         2,875 │ 738 │ 2,282 │   6
          歸石         3,199 │ 852 │ 2,769 │  －
          二階兌換券×602

【階數怎麼判斷】名稱結尾的數字視為階數（「爆石3」→ 爆石 3 階）；沒有結尾數字視為 1 階。
  之後如果有別的物資也要排成階數表格，把它的基底名稱加進 TABLE_ITEMS 即可。

【已知限制】
  - 背包訊息只列出數量 > 0 的物品，所以 EMPTY_CELL 代表「背包裡沒有」，不是 0。
  - 對齊用 east_asian_width 估算字寬（全形=2），等寬字型的終端機才會完全對齊；
    emoji 只出現在分類標題行，不參與欄位對齊。
"""

import re
import unicodedata

from backpack_watcher import is_backpack_message, parse_backpack


# 這些基底名稱的物資（含各階數）改用階數表格顯示；其他物品照原本排法
TABLE_ITEMS = ("爆石", "盾石", "歸石")

# 名稱結尾數字 = 階數
_TIER_SUFFIX = re.compile(r"^(.+?)(\d+)$")

EMPTY_CELL = "－"
CELL_SEP = " │ "
INDENT = "  "


def signature(text):
    return is_backpack_message(text)


def parse(text):
    return parse_backpack(text)


# ── 階數表格用的小工具 ───────────────────────────────────

def _display_width(s):
    """終端機顯示寬度：全形/寬字元算 2，其餘算 1。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _pad_right(s, width):
    return s + " " * max(0, width - _display_width(s))


def _pad_left(s, width):
    return " " * max(0, width - _display_width(s)) + s


def _split_tier(name):
    """物品全名 → (基底名稱, 階數)。"""
    m = _TIER_SUFFIX.match(name)
    if m:
        return m.group(1), int(m.group(2))
    return name, 1


def _format_tier_table(category, items):
    """
    要排成階數表格的物品清單 → 表格的若干行（含分類標題行）。
    物資的先後順序維持遊戲原本的出現順序，階數由小到大。
    """
    families = {}
    for item in items:
        base, tier = _split_tier(item["name"])
        tier_map = families.setdefault(base, {})
        tier_map[tier] = tier_map.get(tier, 0) + item["count"]

    tiers = sorted({t for tier_map in families.values() for t in tier_map})
    name_w = max(_display_width(base) for base in families)

    # 每個階數欄的寬度 = 該欄最寬的數字（表頭「N階」也算）
    col_w = {}
    for t in tiers:
        cells = [f"{tier_map[t]:,}" for tier_map in families.values() if t in tier_map]
        col_w[t] = max([_display_width(f"{t}階")] + [_display_width(c) for c in cells])

    lines = [category]
    header = CELL_SEP.join(_pad_left(f"{t}階", col_w[t]) for t in tiers)
    lines.append(INDENT + " " * name_w + "   " + header)

    for base, tier_map in families.items():
        cells = [
            _pad_left(f"{tier_map[t]:,}" if t in tier_map else EMPTY_CELL, col_w[t])
            for t in tiers
        ]
        lines.append(INDENT + _pad_right(base, name_w) + "   " + CELL_SEP.join(cells))

    return lines


# ── 顯示 ─────────────────────────────────────────────

def format_for_display(parsed):
    lines = ["🎒 背包"]

    # 分類依遊戲原本出現的順序
    by_category = {}
    for item in parsed.get("items", []):
        by_category.setdefault(item["category"], []).append(item)

    for category, items in by_category.items():
        tabled, rest = [], []
        for item in items:
            (tabled if _split_tier(item["name"])[0] in TABLE_ITEMS else rest).append(item)

        if not tabled:
            item_strs = [f"{i['name']}×{i['count']}" for i in items]
            lines.append(f"{category}｜{'、'.join(item_strs)}")
            continue

        lines.extend(_format_tier_table(category, tabled))
        if rest:
            lines.append(INDENT + "、".join(f"{i['name']}×{i['count']}" for i in rest))

    fragments = parsed.get("fragments", [])
    if fragments:
        lines.append("── 碎片 ──")
        for f in fragments:
            lines.append(f"{f['name']} {f['current']}/{f['total']}")

    return "\n".join(lines)