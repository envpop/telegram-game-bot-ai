# -*- coding: utf-8 -*-
"""
response_shapes/satellite_catalog.py —— 「衛星圖鑑」shape

跟其他 response_shapes 檔案一樣的三函式介面：
    signature(text) -> bool
    parse(text) -> dict
    format_for_display(parsed) -> str

解析邏輯不重寫，直接複用 inventory_parsers.py 既有的
is_satellite_catalog_message() / parse_satellite_catalog()——
那邊已經處理過三行一組、TG 分則截斷偵測等細節，這裡只負責
「比對 signature」跟「組出重點顯示文字」。

顯示邏輯（主力/特殊金技/其餘三段）獨立放在 satellite_catalog_display.py，
不寫在這個檔案裡，因為那份格式化邏輯本身沒有比對/路由的責任，
拆開才符合 response_shapes 檔案「只管這一個 shape 該怎麼比對跟怎麼顯示」
的分工——實際重點顯示的細節都在 satellite_catalog_display 裡維護。

注意：這裡的 parse() 回傳的 dict 就是 inventory_parsers.parse_satellite_catalog()
的原始結構化結果（total_count/declared_count/is_complete/equip_limit/satellites），
存檔（save_satellites_snapshot）不在這層做——呼叫端（例如 query_reactor）
才是負責「解析完之後要不要存檔、存去哪個帳號」的地方。
"""

from inventory_parsers import is_satellite_catalog_message, parse_satellite_catalog
from satellite_catalog_display import format_satellite_catalog


def signature(text):
    return is_satellite_catalog_message(text)


def parse(text):
    return parse_satellite_catalog(text)


def format_for_display(parsed):
    return format_satellite_catalog(parsed)
