# -*- coding: utf-8 -*-
"""
response_shapes/backpack.py —— 「背包」shape

跟其他 response_shapes 檔案一樣的三函式介面：
    signature(text) -> bool
    parse(text) -> dict
    format_for_display(parsed) -> str

解析邏輯不重寫，直接複用 backpack_watcher.py 既有的
is_backpack_message() / parse_backpack()——跟 satellite_catalog.py
對 inventory_parsers.py 的關係一樣，這裡只是薄薄的介面轉接，
不用把解析函式挖出來搬家。

顯示格式維持簡單、不特別分類凸顯（熊確認背包資料本身已經簡單易懂，
不需要像 satellite_catalog_display.py 那樣拆一支獨立的顯示格式檔）——
format_for_display() 直接照 parse_backpack() 的結構重新排一次，
內容跟原始訊息基本等價。

注意：存檔（save_inventory_snapshot 等）不在這層做，呼叫端
（profile_sync_strategy.py）才是負責「要不要存檔」的地方，跟
satellite_catalog.py 的分工原則一致。
"""

from backpack_watcher import is_backpack_message, parse_backpack


def signature(text):
    return is_backpack_message(text)


def parse(text):
    return parse_backpack(text)


def format_for_display(parsed):
    lines = ["🎒 背包"]

    by_category = {}
    for item in parsed.get("items", []):
        by_category.setdefault(item["category"], []).append(f"{item['name']}×{item['count']}")
    for category, item_strs in by_category.items():
        lines.append(f"{category}｜{'、'.join(item_strs)}")

    fragments = parsed.get("fragments", [])
    if fragments:
        lines.append("── 碎片 ──")
        for f in fragments:
            lines.append(f"{f['name']} {f['current']}/{f['total']}")

    return "\n".join(lines)
