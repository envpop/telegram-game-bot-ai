# -*- coding: utf-8 -*-
"""
battle_status_cache.py

被動附加式的出戰狀態快取——不主動發送查詢，只在你原本就會打的指令
（陀螺戰績／副陀螺／衛星圖鑑／編隊）觸發、被 shape module 解析到的時候，
把該次查到的那一塊（主陀螺／副陀螺／衛星／編隊）寫進持久化快取，
並在該次回覆下方附加一行「目前已知的完整出戰狀態」。

因為每個指令一次只回一塊資訊，這裡的用途是把「上次查到的」跟
「這次新查到的」合併顯示，不是每次都要重新查全部——符合被動附加、
不主動觸發查詢的設計。

存放路徑建議跟 world_boss_progress.json 同一層（data/{accountID}/），
用你既有的 __file__ 往上找 base_dir 的方式解析，不要寫死路徑。

用法（在既有 shape module 的 parse() / format_for_display() 裡接）：

    from battle_status_cache import load_cache, save_cache, update_main, append_status_footer

    # 陀螺戰績 shape 的 parse() 解析出主陀螺 top dict 之後：
    cache = load_cache(cache_path)
    update_main(cache, main_top, catalog)
    save_cache(cache_path, cache)

    # format_for_display() 組完原本的顯示文字 display_text 之後：
    cache = load_cache(cache_path)
    return append_status_footer(display_text, cache)
"""

import json
import time
from pathlib import Path
from typing import Optional

from battle_status import top_status_data, format_status_line

_CACHE_KEYS = ("main", "sub", "satellite", "formation")


def load_cache(path: Path) -> dict:
    """讀取快取，檔案不存在或壞掉就回傳空快取（不炸掉）。"""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {k: None for k in _CACHE_KEYS}


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def update_main(cache: dict, top: dict, catalog: Optional[dict] = None) -> dict:
    """陀螺戰績 shape 解析到主陀螺時呼叫。"""
    entry = top_status_data(top, catalog)
    entry["updated_at"] = time.time()
    cache["main"] = entry
    return cache


def update_sub(cache: dict, top: dict, catalog: Optional[dict] = None) -> dict:
    """副陀螺 shape 解析到副陀螺時呼叫。"""
    entry = top_status_data(top, catalog)
    entry["updated_at"] = time.time()
    cache["sub"] = entry
    return cache


def clear_sub(cache: dict) -> dict:
    """副陀螺查詢回覆「卸下」狀態時呼叫，清掉快取裡的副陀螺。"""
    cache["sub"] = None
    return cache


def update_satellite(cache: dict, name: str) -> dict:
    """衛星圖鑑 shape 抓到 ⚔️出戰中 名字時呼叫。"""
    cache["satellite"] = {"name": name, "updated_at": time.time()}
    return cache


def update_formation(cache: dict, tops: list, catalog: Optional[dict] = None) -> dict:
    """編隊（無參數查詢）shape 解析到三隻陀螺時呼叫。"""
    cache["formation"] = [top_status_data(t, catalog) for t in tops]
    return cache


def append_status_footer(display_text: str, cache: dict) -> str:
    """
    把快取目前已知的完整出戰狀態附加在原本的顯示文字下面。
    快取全空（例如第一次用，什麼都還沒查過）就原樣回傳，不加空段落。
    """
    data = {
        "main": cache.get("main"),
        "sub": cache.get("sub"),
        "satellite": (cache.get("satellite") or {}).get("name"),
        "formation": cache.get("formation") or [],
    }
    line = format_status_line(data)
    if not line:
        return display_text
    return f"{display_text}\n\n[出戰狀態]\n{line}"


if __name__ == "__main__":
    # 快速自我測試：模擬「先查陀螺戰績，再查副陀螺」兩次觸發的疊加效果
    tmp_path = Path("/tmp/battle_status_cache_test.json")
    cache = load_cache(tmp_path)

    main_top = {
        "name": "☆聖氣盾・極・天熊・滅卻牙",
        "base_name": "極・天熊・滅卻牙",
        "type": "防禦型",
        "element": "火",
    }
    update_main(cache, main_top)
    save_cache(tmp_path, cache)
    print("=== 查完陀螺戰績後 ===")
    print(append_status_footer("📊 陀螺戰績...(原本顯示內容)", cache))

    cache = load_cache(tmp_path)  # 模擬下一次指令重新讀快取
    sub_top = {"name": "焚天神熊・摸摸赤焱GO", "base_name": "焚天神熊・摸摸赤焱GO",
               "type": "攻擊型", "element": "火"}
    update_sub(cache, sub_top)
    save_cache(tmp_path, cache)
    print()
    print("=== 再查完副陀螺後（主陀螺記憶還在）===")
    print(append_status_footer("🌗 副陀螺...(原本顯示內容)", cache))

    tmp_path.unlink()