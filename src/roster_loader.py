# -*- coding: utf-8 -*-
"""
roster_loader.py

共用的 roster 載入邏輯——原本這段（讀 tops.json、套用 cast/special
catalog、resolve_roster 補完 element）分別寫在 query_advisor_strategy.py
跟 inventory_display_strategy.py 裡各一份，這次 guard_clear_strategy.py
需要第三次用到，抽成共用函式，不再複製第三份。

之後有空可以回頭把前兩個檔案也改成呼叫這裡，不強制現在做（現在改風險
是要重新測那兩個已經跑穩的功能，不划算——等下次真的要動那兩支檔案時
順手換掉就好）。
"""

import json
from pathlib import Path

from query_reactor import resolve_roster
from battle_status import load_element_catalog


def load_roster(base_dir, account_id):
    """回傳這個帳號目前的 roster（已套用 element 解析），找不到 tops.json
    就回傳空 list，不噴錯——呼叫端應該把空 list 當「還沒有資料，先不動作」
    處理，不是異常狀況。"""
    base = Path(base_dir)
    account_dir = base / "data" / str(account_id)
    common_dir = base / "data" / "common"

    tops_path = account_dir / "tops.json"
    if not tops_path.exists():
        return []

    with tops_path.open(encoding="utf-8") as f:
        raw_roster = json.load(f)["detailed"]

    cast_catalog = {}
    cast_path = account_dir / "cast_tops_catalog.json"
    if cast_path.exists():
        with cast_path.open(encoding="utf-8") as f:
            cast_catalog = json.load(f)

    special_catalog = {}
    special_path = common_dir / "special_tops_catalog.json"
    if special_path.exists():
        with special_path.open(encoding="utf-8") as f:
            special_catalog = json.load(f)

    catalog = load_element_catalog(special_catalog, cast_catalog)
    return resolve_roster(raw_roster, catalog)