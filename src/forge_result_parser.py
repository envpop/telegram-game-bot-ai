# -*- coding: utf-8 -*-
"""
forge_result_parser.py

解析「鑄造完成」訊息。這是陀螺收藏/天賦一覽都補不到的最後一塊拼圖：
自訂命名（emoji/簡單詞）的陀螺，屬性資料只在鑄造當下的公告訊息裡出現過，
之後的收藏/天賦清單都不會再重複顯示。

用法：每次抓到「鑄造完成」訊息就呼叫 parse_forge_result()，
存進 cast_tops_catalog（依名稱 key），之後 talent_overview.build_unified_view()
遇到查無屬性的陀螺，可以 fallback 查這份 catalog 補上。
"""

import re
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


RE_FORGE = re.compile(
    r'你設計的「(.+?)」出爐'
    r'.*?稀有度[:：](\S+?)\s*([✦]+)（(.+?)）'
    r'.*?類型[:：](\S+?)\s+天生五行[:：]\S*?([火土金木水])\(\s*(\d+)\s*階\)'
    r'.*?數值[:：]攻\s*(\d+)／防\s*(\d+)／耐\s*(\d+)\s+戰力\s*(\d+)',
    re.S,
)


@dataclass
class ForgeResult:
    name: str
    rarity: str          # SSR / SR / R ...（鑄造系統自己的細分等級，跟收藏清單顯示的 神/UR 是不同體系）
    stars: int
    tier_label: str       # 「高階檔」等
    type: str
    element: str
    element_stage: int
    atk: int
    defense: int
    endurance: int
    power: int


def parse_forge_result(message: str) -> Optional[ForgeResult]:
    m = RE_FORGE.search(message)
    if not m:
        return None
    (name, rarity, stars, tier_label, type_, element, stage,
     atk, defense, endurance, power) = m.groups()
    return ForgeResult(
        name=name,
        rarity=rarity,
        stars=len(stars),
        tier_label=tier_label,
        type=type_,
        element=element,
        element_stage=int(stage),
        atk=int(atk),
        defense=int(defense),
        endurance=int(endurance),
        power=int(power),
    )


# ---------- 累積型 catalog：每次鑄造訊息進來就補一筆 ----------
#
# 存檔格式對齊 battle_status.py 的 load_element_catalog() 期待的
# cast_tops_catalog.json 格式：{base_name: {"element":..., "build":{...}}}
# 鑄造陀螺的 base_name 就是 name 本身（已用 tops.json 驗證：🍄/🥀/🏎/🦑
# 等鑄造項目的 base_name 欄位皆與 name 相同）。

def _to_catalog_entry(result: ForgeResult) -> dict:
    return {
        "element": result.element,
        "build": {
            "type": result.type,
            "element_stage": result.element_stage,
            "power": result.power,
            "atk": result.atk,
            "defense": result.defense,
            "endurance": result.endurance,
            "rarity": result.rarity,
            "stars": result.stars,
            "tier_label": result.tier_label,
        },
    }


def load_cast_catalog(path: Path) -> dict:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cast_catalog(catalog: dict, path: Path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)


def add_cast_entry(message: str, catalog_path: Path) -> Optional[ForgeResult]:
    """解析一則鑄造完成訊息，寫進 cast_tops_catalog.json（依名稱累加/覆蓋）。"""
    result = parse_forge_result(message)
    if result is None:
        return None
    catalog = load_cast_catalog(catalog_path)
    catalog[result.name] = _to_catalog_entry(result)
    save_cast_catalog(catalog, catalog_path)
    return result


# 舊名字保留為別名，避免其他還沒改完的呼叫端直接炸掉；
# 新程式碼一律用上面的 load_cast_catalog / save_cast_catalog / add_cast_entry。
load_forge_catalog = load_cast_catalog
save_forge_catalog = save_cast_catalog
add_forge_result = add_cast_entry


if __name__ == "__main__":
    sample = """⚒️✨ 鑄造完成！你設計的「一刀」出爐！
──────────────
稀有度:SSR ✦✦✦（高階檔）
類型:防禦型　天生五行:🟢木(1 階)
數值:攻 40／防 56／耐 46　戰力 148
──────────────
打「我的陀螺」看收藏,「出戰 編號」派它上場 🦊"""

    r = parse_forge_result(sample)
    print(r)