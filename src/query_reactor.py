# -*- coding: utf-8 -*-
"""
query_reactor.py

職責：
  接收「查詢類指令的 bot 回覆訊息」（陀螺戰績 / 世界王 / 護衛...），
  判斷這則訊息屬於哪種查詢 shape，抽出「當下查詢對象」的關鍵資訊，
  並且只針對這次查到的對象給出建議 —— 不猜測、不維護「目前戰鬥目標」
  這種持久狀態，因為系統不知道使用者接下來真正要打誰。

設計原則：
  - 純判斷 + 建議組裝，不碰 monitor / executor
  - 每個 shape 各自獨立解析，互不影響（對齊 parsing/ 既有分層）
  - 三種 shape 目前處理程度不同：
      陀螺戰績 -> 能反查主塔目前樓層 -> 可給出戰建議
      世界王   -> 弱點屬性已由訊息直接告知（沿用 weakness_matcher.py）-> 可給建議
      護衛     -> 純狀態通知（王已被討伐），沒有可建議的戰鬥目標，回傳 None
"""

import re
from dataclasses import dataclass
from typing import Optional

from main_tower_advisor import (
    load_json,
    BOSS_CATALOG_PATH,
    RULES_PATH,
    get_boss_for_floor,
    recommend_tops,
)
from weakness_matcher import WeaknessParser, TopSelector
from battle_status import resolve_element_any
from parsing.response_shapes import guard_status as guard_status_shape


def resolve_roster(roster: list, catalog: Optional[dict] = None) -> list:
    """
    推薦前的必經步驟：把 roster 每一顆的 element 換成 resolve_element_any()
    算出來的真實值（top-level -> binding fallback -> cast_tops_catalog fallback）。
    不呼叫這個直接把原始 roster 丟給 recommend_tops / TopSelector.recommend，
    會漏掉需要 fallback 才查得到屬性的陀螺（例如未點天賦但有 binding 資料的，
    或鑄造陀螺查 cast_tops_catalog 才有屬性的）。
    """
    resolved = []
    for top in roster:
        t = dict(top)
        t["element"] = resolve_element_any(top, catalog)
        resolved.append(t)
    return resolved


# ---------- Shape: 陀螺戰績 ----------

RE_TOP_RECORD_FLOOR = re.compile(r"目前關卡[:：]第\s*(\d+)\s*階・(\S+?)\s*[🌟💠⭐]")
RE_TOP_RECORD_BEST = re.compile(r"最高通關[:：]第\s*(\d+)\s*階")
RE_TOP_RECORD_STREAK = re.compile(r"連勝[:：](\d+)")
# 出戰：曜金神熊・摸摸太白GO（神・平衡型・戰力 535） —— 抓「現在使用的陀螺」，
# 建議段落至少要能對照現在出戰的是誰，不只是丟一串候選名單。
RE_TOP_RECORD_ACTIVE = re.compile(
    r"出戰[:：](.+?)（(\S+?)・(\S+?)・戰力\s*(\d+)）"
)


@dataclass
class TopRecordState:
    current_floor: int
    current_boss_name: str
    best_floor: Optional[int] = None
    streak: Optional[int] = None
    active_name: Optional[str] = None
    active_rarity: Optional[str] = None
    active_type: Optional[str] = None
    active_power: Optional[int] = None


def parse_top_record(message: str) -> Optional[TopRecordState]:
    m = RE_TOP_RECORD_FLOOR.search(message)
    if not m:
        return None
    floor, boss_name = int(m.group(1)), m.group(2)

    best_m = RE_TOP_RECORD_BEST.search(message)
    streak_m = RE_TOP_RECORD_STREAK.search(message)
    active_m = RE_TOP_RECORD_ACTIVE.search(message)

    return TopRecordState(
        current_floor=floor,
        current_boss_name=boss_name,
        best_floor=int(best_m.group(1)) if best_m else None,
        streak=int(streak_m.group(1)) if streak_m else None,
        active_name=active_m.group(1) if active_m else None,
        active_rarity=active_m.group(2) if active_m else None,
        active_type=active_m.group(3) if active_m else None,
        active_power=int(active_m.group(4)) if active_m else None,
    )


# ---------- Shape: 護衛（清護衛戰況／已散去）----------
#
# 2026-08-17 收斂：解析邏輯不在這裡重複一份，直接用
# response_shapes/guard_status.py 的 signature()/parse()——那邊的正則式
# 涵蓋範圍更完整（多存了組成/互相加護/重編組倒數等欄位），這裡只取用
# 建議需要的部分（boss_name/remaining/total/reduction_pct/next_target）。
# 之後護衛文字格式改版，只要改 guard_status.py 一個地方，這裡不用跟著動。


def recommend_for_guard_target(next_target: dict, roster: list, top_n: int = 3) -> list:
    """
    next_target 是 guard_status.parse() 回傳的 dict（satellite_type/weak_element/weak_type），
    弱點屬性/類型是訊息直接寫明的目標值（不是反查剋制表），
    直接比對 top 的 element/type 是否等於弱點值即可。
    評分：兩項都符合 -> 2（一擊拆掉），符合一項 -> 1（打得輕鬆），都不符合 -> 0
    """
    weak_element = next_target["weak_element"]
    weak_type = next_target["weak_type"]
    scored = []
    for top in roster:
        score = 0
        reasons = []
        if top.get("element") == weak_element:
            score += 1
            reasons.append(f"屬性符合（{weak_element}）")
        if top.get("type") == weak_type:
            score += 1
            reasons.append(f"類型符合（{weak_type}）")
        scored.append({**top, "_score": score, "_reasons": reasons})
    scored.sort(key=lambda t: (t["_score"], t.get("power", 0)), reverse=True)
    return scored[:top_n]


# ---------- 建議行的共用格式 ----------
#
# 熊要求：建議最多 2 顆，每行重點是「編號/類型/屬性/戰力」——出戰是用編號下指令的
# （「出戰 編號」），名字不能拿來下指令，所以編號一定要放在最前面，不能省略。
RECOMMEND_TOP_N = 2


def _format_pick_line(p, extra=None):
    """extra: 可選的附加說明（例如剋制原因、一擊拆掉標籤），接在戰力後面。"""
    index = p.get("index")
    top_type = p.get("type") or "？"
    element = p.get("element") or "？"
    power = p.get("power")
    line = f"  #{index} {p.get('name', '')}｜{top_type}・{element}・戰力{power}"
    if extra:
        line += f"・{extra}"
    return line


# ---------- 統一入口 ----------

def handle_query_reply(message: str, roster: list) -> Optional[str]:
    """
    依訊息內容判斷 shape 並回傳建議文字；無法辨識或無建議目標時回傳 None。
    roster: tops.json 的 "detailed" 陣列
    """

    # 世界王：弱點屬性已由訊息直接寫明，沿用既有 weakness_matcher
    weakness = WeaknessParser.parse(message)
    if weakness:
        picks = TopSelector.recommend(roster, weakness)[:RECOMMEND_TOP_N]
        warning = TopSelector.missing_element_warning(roster, weakness)
        lines = [f"🔮 世界王弱點屬性：{weakness.current_element}屬性"]
        if warning:
            lines.append(warning)
        else:
            for p in picks:
                lines.append(_format_pick_line(p))
        return "\n".join(lines)

    # 陀螺戰績：反查主塔目前樓層的王，套用五行+類型雙重剋制建議
    top_record = parse_top_record(message)
    if top_record:
        catalog = load_json(BOSS_CATALOG_PATH)
        rules = load_json(RULES_PATH)
        boss = get_boss_for_floor(top_record.current_floor, catalog)
        picks = recommend_tops(boss, roster, rules, top_n=RECOMMEND_TOP_N)
        lines = [
            f"📊 主塔第 {top_record.current_floor} 階：{boss['name']}"
            f"（{boss['type']}・{boss.get('element') or '無屬性資料'}）"
        ]
        if top_record.active_name:
            lines.append(
                f"目前出戰：{top_record.active_name}"
                f"（{top_record.active_rarity}・{top_record.active_type}・戰力{top_record.active_power}）"
            )
        if not picks or picks[0]["_score"] == 0:
            lines.append("目前手上沒有明顯剋制的陀螺，以戰力最高者出戰。")
        for p in picks:
            reason = "、".join(p["_reasons"]) if p["_reasons"] else "無特別剋制"
            lines.append(_format_pick_line(p, extra=reason))
        return "\n".join(lines)

    # 護衛（清護衛戰況 / 已散去）：改用 guard_status.py shape 的 parse()，
    # 不再自己重複一份 regex。散去通知純狀態，沒有可建議的戰鬥對象。
    if guard_status_shape.signature(message):
        parsed_guard = guard_status_shape.parse(message)

        if parsed_guard["type"] == "dispersed":
            return None

        if parsed_guard["type"] == "active":
            lines = [
                f"🛡️ 「{parsed_guard['boss_name']}」剩 {parsed_guard['remaining']}/{parsed_guard['total']} 顆衛星護衛"
                f"（王減傷 {parsed_guard['reduction_pct']}%）"
            ]
            next_target = parsed_guard.get("next_target")
            if next_target:
                lines.append(
                    f"🎯 下一顆：{next_target['satellite_type']}"
                    f"　弱點：{next_target['weak_element']}屬性／{next_target['weak_type']}"
                )
                picks = recommend_for_guard_target(next_target, roster, top_n=RECOMMEND_TOP_N)
                if not picks or picks[0]["_score"] == 0:
                    lines.append("目前手上沒有符合弱點的陀螺，以戰力最高者出戰。")
                for p in picks:
                    reason = "、".join(p["_reasons"]) if p["_reasons"] else "無符合項"
                    tag = "一擊拆掉" if p["_score"] == 2 else ("打得輕鬆" if p["_score"] == 1 else "普通")
                    lines.append(_format_pick_line(p, extra=f"{reason}・{tag}"))
            return "\n".join(lines)

        return None  # type == "unknown"：signature() 已先擋過，理論上不會走到這，防禦性 fallback

    return None


if __name__ == "__main__":
    import json

    with open("/mnt/user-data/uploads/tops.json", encoding="utf-8") as f:
        raw_roster = json.load(f)["detailed"]
    with open("cast_tops_catalog.json", encoding="utf-8") as f:
        cast_catalog = json.load(f)

    from battle_status import load_element_catalog
    catalog = load_element_catalog(special_catalog={}, cast_catalog=cast_catalog)

    # 正確用法：先 resolve_roster 套用完整 fallback 鏈，再丟給 handle_query_reply
    # 少了這一步，binding fallback / cast_tops_catalog fallback 才查得到屬性的
    # 陀螺（例如未點天賦但有binding資料的、或鑄造陀螺）不會被考慮進推薦。
    roster = resolve_roster(raw_roster, catalog)

    samples = [
        ("陀螺戰績", "📊 @envpop 的陀螺戰績\n──────────────\n目前關卡：第 100 階・摸摸熊・原初真神 🌟神位\n最高通關：第 100 階　連勝：39\n出戰：崩嶽神熊・摸摸撼地GO（神・攻擊型・戰力 611）\n收藏：36 顆"),
        ("世界王", "👹 今日世界王【第 3 階】:陸棚級・玄鱗（防禦型・⚪金屬性）\n🪸【礁牙】階級技能〈礁岩壁壘〉:單擊上限收緊到 10%,但弱點屬性加成拉到 ×1.8\n🔮 弱點屬性:🔴火屬性(帶 🔴火屬性 五行的陀螺打傷害更高)"),
        ("護衛", "🛰️ 王已被討伐——衛星護衛失去了環繞的對象,散去了。"),
        ("清護衛", "🛰️ 「深淵級・沉鐘」還被 6/6 顆【衛星護衛】環繞\n🛡️ 王目前減傷 52%（每拆一顆就少一截；王本體隨時打得到）\n組成:🔗鎖鏈×1 ⚡充能×2 🛡️破盾×2 📡哨衛×1\n🔗 互相加護:🔴火屬性、🔵水屬性 各有兩顆以上,它們的減傷會互相加成——先拆落單的\n🎯 下一顆:⚡充能衛星・⚪金屬性・攻擊型　弱點:🔴火屬性／防禦型\n　（清掉→全服接下來數刀傷害提升）\n⚔️ 打「清護衛」拆它。五行與類型都剋它 → 一擊拆掉,不用打;剋一項就打得很輕鬆。\n🔄 4 分鐘後重新編組(弱點重洗)　⏳ 64 分鐘後自行散去"),
    ]

    for label, msg in samples:
        print(f"=== 查詢「{label}」===")
        result = handle_query_reply(msg, roster)
        print(result if result else "（無可建議的戰鬥對象）")
        print()