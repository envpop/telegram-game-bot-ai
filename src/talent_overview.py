# -*- coding: utf-8 -*-
"""
talent_overview.py

解析「我的天賦」訊息 —— 這是目前唯一會在訊息裡直接寫出「五行屬性」的
查詢 shape（陀螺收藏只有類型，沒有屬性）。原始訊息每隻陀螺佔兩行，
這裡把它合併成一筆完整資料，並提供 build_unified_view() 跟陀螺收藏
快照合併，做出「戰力+類型+屬性+強化+天賦」一次查完的統一總覽。

注意：這份訊息只涵蓋「已綁定」的陀螺（熊的例子是 26/36 顆），
未綁定的陀螺不會出現在這裡，合併時要保留只在陀螺收藏裡出現、
天賦一覽沒有的那些（他們就是尚未綁定的）。
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from top_collection_snapshot import parse_top_collection, GodTop
from forge_result_parser import load_cast_catalog


RE_HEADER = re.compile(
    r'^#(\d+)\s*(⚔️出戰中\s*)?(.+?)(?:\s*\+(\d+))?\s*'
    r'(💥爆擊|🛡️護盾|🌀回歸)綁定([IVX]*)　(\S+?)・戰力\s*(\d+)$',
    re.M,
)
RE_DETAIL = re.compile(
    r'^　熟練\s*(\d+)/(\d+)・可兌換\s*(\d+)\s*次｜(.+)$',
    re.M,
)
RE_ELEMENT_STAGE = re.compile(r'五行([火土金木水])(\d+)階')
RE_RESONANCE = re.compile(r'✨共鳴[:：](.+)$')

_BIND_LABEL = {"💥爆擊": "爆擊", "🛡️護盾": "護盾", "🌀回歸": "回歸"}


@dataclass
class TalentEntry:
    index: int
    is_active: bool          # ⚔️出戰中
    name: str
    enhancement: Optional[int]
    bind_type: Optional[str]
    bind_level: Optional[str]
    type: str
    power: int
    mastery_current: int
    mastery_max: int
    exchange_count: int
    element: Optional[str]        # 五行，可能為 None（尚未點天賦時 rest 仍可能有五行；純無資料才是 None）
    element_stage: Optional[int]
    talents: list = field(default_factory=list)
    resonance: Optional[str] = None


def _parse_detail_rest(rest: str):
    """拆解 熟練/可兌換 後面那段：五行階段 + 天賦列表 + 共鳴"""
    if rest.strip() == "尚未點天賦":
        return None, None, [], None

    resonance = None
    res_m = RE_RESONANCE.search(rest)
    if res_m:
        resonance = res_m.group(1).strip()
        rest = rest[:res_m.start()].rstrip("・")

    element, stage = None, None
    elem_m = RE_ELEMENT_STAGE.search(rest)
    talents = []
    if elem_m:
        element = elem_m.group(1)
        stage = int(elem_m.group(2))
        remainder = rest[elem_m.end():].lstrip("・")
        talents = [t for t in remainder.split("・") if t]
    else:
        talents = [t for t in rest.split("・") if t]

    return element, stage, talents, resonance


def parse_talent_overview(message: str) -> list:
    headers = RE_HEADER.findall(message)
    details = RE_DETAIL.findall(message)

    if len(headers) != len(details):
        # 兩邊筆數對不上，資料可能被截斷，仍盡量配對已知的部分
        pass

    entries = []
    for h, d in zip(headers, details):
        idx, active_marker, name, enh, bind_kind, bind_lv, type_, power = h
        mastery_cur, mastery_max, exch, rest = d
        element, stage, talents, resonance = _parse_detail_rest(rest)

        entries.append(TalentEntry(
            index=int(idx),
            is_active=bool(active_marker.strip()),
            name=name,
            enhancement=int(enh) if enh else None,
            bind_type=_BIND_LABEL.get(bind_kind),
            bind_level=bind_lv or None,
            type=type_,
            power=int(power),
            mastery_current=int(mastery_cur),
            mastery_max=int(mastery_max),
            exchange_count=int(exch),
            element=element,
            element_stage=stage,
            talents=talents,
            resonance=resonance,
        ))
    return entries


# ---------- 合併：陀螺收藏（神階完整） + 天賦一覽（含五行） ----------

def build_unified_view(collection_message: str, talent_message: str,
                        cast_catalog_path: Optional[str] = None) -> list:
    """
    合併三個資料源做出完整陀螺一覽：
      1. 陀螺收藏（神階完整資料）
      2. 天賦一覽（已綁定陀螺的五行屬性）
      3. cast_tops_catalog.json（鑄造公告存下的屬性，補「未綁定」缺口，選用）
         —— schema 是 {base_name: {"element":..., "build":{...}}}，
         跟 battle_status.py 的 load_element_catalog() 是同一份格式，
         element_stage 在 build 底下，不是頂層（2026-08-14 修正過一次
         KeyError，因為改 schema 時這裡忘了同步更新）。

    只在陀螺收藏出現、天賦一覽跟 cast_tops_catalog 都沒有的（UR 且尚未綁定、
    也沒抓到鑄造訊息），屬性維持 None——這是真的沒有資料，不是程式漏掉。
    """
    collection = parse_top_collection(collection_message)
    talents = parse_talent_overview(talent_message)
    talent_by_name = {t.name: t for t in talents}
    ur_status_by_name = {u.name: u.status for u in collection.ur_status_markers}
    cast_catalog = load_cast_catalog(Path(cast_catalog_path)) if cast_catalog_path else {}

    unified = []
    seen_names = set()

    for god in collection.god_tops:
        t = talent_by_name.get(god.name)
        unified.append({
            "name": god.name,
            "status": god.status,
            "type": god.type,
            "element": t.element if t else None,
            "element_stage": t.element_stage if t else None,
            "power": god.power,
            "enhancement": god.enhancement,
            "bind_type": god.bind_type,
            "bind_level": god.bind_level,
            "mastery": f"{t.mastery_current}/{t.mastery_max}" if t else None,
            "talents": t.talents if t else [],
            "resonance": t.resonance if t else None,
            "rarity": "神",
            "source": "collection+talent" if t else "collection",
        })
        seen_names.add(god.name)

    # UR 陀螺：已綁定的（天賦一覽有資料）
    for t in talents:
        if t.name in seen_names:
            continue
        unified.append({
            "name": t.name,
            "status": ur_status_by_name.get(t.name) or ("出戰" if t.is_active else None),
            "type": t.type,
            "element": t.element,
            "element_stage": t.element_stage,
            "power": t.power,
            "enhancement": t.enhancement,
            "bind_type": t.bind_type,
            "bind_level": t.bind_level,
            "mastery": f"{t.mastery_current}/{t.mastery_max}",
            "talents": t.talents,
            "resonance": t.resonance,
            "rarity": "UR",
            "source": "talent",
        })
        seen_names.add(t.name)

    # UR 陀螺：未綁定的（陀螺收藏有，但天賦一覽沒有）—— 嘗試用 cast_tops_catalog 補屬性
    for u in collection.ur_entries:
        if u.name in seen_names:
            continue
        cast = cast_catalog.get(u.name)
        build = (cast or {}).get("build") or {}
        unified.append({
            "name": u.name,
            "status": u.status,
            "type": u.type,
            "element": cast["element"] if cast else None,
            "element_stage": build.get("element_stage"),
            "power": u.power,
            "enhancement": None,
            "bind_type": None,
            "bind_level": None,
            "mastery": None,
            "talents": [],
            "resonance": None,
            "rarity": "UR",
            "source": "collection+cast" if cast else "collection_only",
        })
        seen_names.add(u.name)

    unified.sort(key=lambda r: r["power"], reverse=True)
    return unified


def lookup(unified: list, element: Optional[str] = None, type_: Optional[str] = None) -> list:
    """快查：依五行/類型篩選陀螺一覽"""
    results = unified
    if element:
        results = [r for r in results if r["element"] == element]
    if type_:
        results = [r for r in results if r["type"] == type_]
    return results


if __name__ == "__main__":
    import sys, json
    data = json.loads(sys.stdin.read())
    unified = build_unified_view(data["collection"], data["talent"])
    for r in unified:
        elem = r["element"] or "未知"
        print(f"{r['name']}｜{r['type']}・{elem}屬性{r['element_stage'] or ''}階"
              f"｜戰力{r['power']}・+{r['enhancement']}｜{r['bind_type']}{r['bind_level'] or ''}"
              f"｜熟練{r['mastery']}｜{r['status'] or ''}")