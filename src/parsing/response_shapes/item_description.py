# -*- coding: utf-8 -*-
"""
response_shapes/item_description.py —— 「道具說明」shape

跟其他 response_shapes 檔案一樣的三函式介面。解析邏輯不重寫，直接複用
backpack_watcher.py 既有的 parse_item_description()。

parse_item_description() 本身兼具「判斷是不是這個格式」跟「解析」兩種
功能（不符合格式回傳 None），signature() 直接複用它的判斷結果——等於
呼叫兩次 parse_item_description()（一次在 signature()，一次在
parse()），但這函式只是 regex 比對、沒有 I/O，重複呼叫的成本可忽略，
不特地加快取。
"""

from backpack_watcher import parse_item_description


def signature(text):
    return parse_item_description(text) is not None


def parse(text):
    return parse_item_description(text)


def format_for_display(parsed):
    return (f"📖 {parsed['display_name']}（簡稱：{parsed['short_name']}）\n"
            f"{parsed['description']}")
