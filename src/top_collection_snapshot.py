# -*- coding: utf-8 -*-
"""
top_collection_snapshot.py

解析「陀螺收藏」訊息 —— 這是遊戲內建的收藏總覽快照，跟 tops.json（既有的
完整 roster 資料源）是兩份不同用途的東西：
  - tops.json：權威資料源，給 TopSelector / main_tower_advisor 做戰鬥推薦用
  - 這份快照：確認自身戰力總覽用，神階完整保留，UR 階只算數量
    （熊：「UR以下（其實目前都是神/UR）只算數量就夠了」）

例外：UR 階若帶 ⭐(出戰) 或 🌗(副陀螺) 狀態標記，仍會被記錄名稱+狀態，
因為這是「現在戰鬥用誰」的關鍵狀態，不是收藏細節。
"""

import re
from dataclasses import dataclass, field
from typing import Optional


RE_HEADER = re.compile(r"共\s*(\d+)\s*顆")

RE_LINE = re.compile(
    r'^(\d+)\.\s*([⭐🌗]?)[✦]+[🔱👑]?\s*(.+?)'
    r'(?:\s*\+(\d+))?'
    r'(?:(💥爆擊|🛡️護盾|🌀回歸)綁定([IVX]*))?'
    r'｜(神|UR)・(\S+?)・戰力\s*(\d+)\s*$',
    re.M,
)


@dataclass
class GodTop:
    index: int
    status: Optional[str]   # "出戰" / "副陀螺" / None
    name: str
    enhancement: Optional[int]
    bind_type: Optional[str]    # "爆擊" / "護盾" / "回歸" / None
    bind_level: Optional[str]   # "IV" / "III" ...
    type: str
    power: int


@dataclass
class URLightEntry:
    """UR 的輕量記錄：只留名字/類型/戰力/狀態，不留天賦等完整細節。
    存在的目的是讓後續（例如 cast_tops_catalog）可以用名字反查屬性，
    不代表要在畫面上展示 UR 的完整細節——顯示層仍然只需要 ur_count / ur_by_type。"""
    index: int
    status: Optional[str]
    name: str
    type: str
    power: int


@dataclass
class TopCollectionSnapshot:
    total: int
    god_tops: list            # List[GodTop] 完整保留
    ur_count: int
    ur_by_type: dict          # {"攻擊型": n, ...}
    ur_entries: list          # List[URLightEntry] 全部 UR（名字保留，供內部反查用）
    ur_status_markers: list   # List[URLightEntry]，ur_entries 裡帶狀態標記的子集


_STATUS_MAP = {"⭐": "出戰", "🌗": "副陀螺"}


def parse_top_collection(message: str) -> Optional[TopCollectionSnapshot]:
    header_m = RE_HEADER.search(message)
    if not header_m:
        return None
    total = int(header_m.group(1))

    god_tops = []
    ur_by_type = {}
    ur_entries = []
    ur_count = 0

    for m in RE_LINE.finditer(message):
        idx, status_icon, name, enh, bind_kind, bind_lv, rarity, type_, power = m.groups()
        status = _STATUS_MAP.get(status_icon) if status_icon else None
        power = int(power)

        if rarity == "神":
            bind_type = bind_kind.replace("💥", "").replace("🛡️", "").replace("🌀", "") if bind_kind else None
            god_tops.append(GodTop(
                index=int(idx),
                status=status,
                name=name,
                enhancement=int(enh) if enh else None,
                bind_type=bind_type,
                bind_level=bind_lv,
                type=type_,
                power=power,
            ))
        else:  # UR
            ur_count += 1
            ur_by_type[type_] = ur_by_type.get(type_, 0) + 1
            ur_entries.append(URLightEntry(
                index=int(idx), status=status, name=name, type=type_, power=power,
            ))

    ur_status_markers = [u for u in ur_entries if u.status]

    return TopCollectionSnapshot(
        total=total,
        god_tops=god_tops,
        ur_count=ur_count,
        ur_by_type=ur_by_type,
        ur_entries=ur_entries,
        ur_status_markers=ur_status_markers,
    )


if __name__ == "__main__":
    import sys
    sample = sys.stdin.read()
    snap = parse_top_collection(sample)
    print(f"總數 {snap.total}｜神階 {len(snap.god_tops)} 顆(完整)｜UR {snap.ur_count} 顆(僅計數)")
    print("UR 分布：", snap.ur_by_type)
    if snap.ur_status_markers:
        print("UR 狀態標記：")
        for u in snap.ur_status_markers:
            print(f"  {u.name}（{u.status}・{u.type}・戰力{u.power}）")