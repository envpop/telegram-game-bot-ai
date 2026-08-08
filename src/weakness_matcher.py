# -*- coding: utf-8 -*-
"""
weakness_matcher.py

職責:
  1. WeaknessParser - 從 BOSS 訊息中解析目前弱點屬性(五行)
  2. TopSelector    - 根據弱點屬性,從 tops.json 的 detailed 清單中選出建議出戰陀螺

設計原則(對齊專案既有分層架構):
  - 本模組不碰 monitor / executor,只做「純判斷」,回傳建議結果
  - 要接進 world_boss_strategy.py 時,由該 strategy 呼叫這裡拿到建議陀螺後,
    自行決定要不要呼叫 executor 做切換動作(本模組不主動執行任何動作)

解析邏輯說明:
  訊息裡的顏色 emoji (🔴🟡⚪🟢🔵) 只是裝飾,不同時期/不同訊息來源可能不一致,
  所以刻意「不」依賴 emoji 顏色比對,只抓「(火|土|金|木|水)屬性」這個中文樣式,
  這樣即使遊戲改了 emoji 也不會失效。
"""

import re
from dataclasses import dataclass
from typing import List, Optional


ELEMENTS = ["火", "土", "金", "木", "水"]

# 開場公告: 🔮 弱點屬性:🔴火屬性(...)
RE_INITIAL_WEAKNESS = re.compile(r"弱點屬性[:：]\S*?([火土金木水])屬性")

# 中場轉變: 五行 🔴火屬性 → 🟡土屬性　類型 持久型 → 攻擊型　新弱點:🟢木屬性
RE_TRANSITION = re.compile(
    r"五行\s*\S*?([火土金木水])屬性\s*→\s*\S*?([火土金木水])屬性"
    r".*?類型\s*(\S+?)\s*→\s*(\S+?)[　\s]"
    r".*?新弱點[:：]\S*?([火土金木水])屬性",
    re.S,
)


@dataclass
class WeaknessState:
    current_element: str          # 目前應該打的屬性
    boss_element: Optional[str] = None   # BOSS 目前自身五行(轉變後才有)
    boss_type: Optional[str] = None      # BOSS 目前類型(轉變後才有)
    source: str = ""              # 判斷依據: "initial" 或 "transition"


class WeaknessParser:
    """純解析,不做任何 I/O 或決策"""

    @staticmethod
    def parse(message: str) -> Optional[WeaknessState]:
        m = RE_TRANSITION.search(message)
        if m:
            _, boss_element, _, boss_type, new_weakness = m.groups()
            return WeaknessState(
                current_element=new_weakness,
                boss_element=boss_element,
                boss_type=boss_type,
                source="transition",
            )

        m = RE_INITIAL_WEAKNESS.search(message)
        if m:
            return WeaknessState(current_element=m.group(1), source="initial")

        return None


class TopSelector:
    """
    根據 WeaknessState 從 tops 清單(tops.json 的 detailed 陣列)選出建議陀螺。
    """

    @staticmethod
    def recommend(tops: List[dict], weakness: WeaknessState, top_n: int = 3) -> List[dict]:
        """
        回傳依優先序排列的建議陀螺清單。

        排序邏輯:
          1. element 完全符合弱點屬性 的陀螺優先
          2. 同屬性內,依戰力 (power) 由高到低
          3. 若同屬性戰力相同,強化值 (enhancement) 高者優先

        注意:element 為 None(尚未綁定五行)的陀螺不會被選入,
        因為沒有屬性資料就無法確認是否命中弱點,寧可不選也不要猜。
        """
        candidates = [t for t in tops if t.get("element") == weakness.current_element]

        if not candidates:
            return []

        candidates.sort(
            key=lambda t: (t.get("power") or 0, t.get("enhancement") or 0),
            reverse=True,
        )
        return candidates[:top_n]

    @staticmethod
    def missing_element_warning(tops: List[dict], weakness: WeaknessState) -> Optional[str]:
        """
        若手上完全沒有符合弱點屬性的陀螺,回傳警告文字,方便印出提醒人工介入。
        """
        candidates = [t for t in tops if t.get("element") == weakness.current_element]
        if candidates:
            return None
        unbound = [t for t in tops if t.get("element") is None]
        msg = f"⚠️ 目前弱點為「{weakness.current_element}屬性」,但手上沒有此屬性的陀螺可用!"
        if unbound:
            names = "、".join(t["name"] for t in unbound)
            msg += f"\n   有 {len(unbound)} 隻陀螺尚未綁定五行,若補綁可能可以應對: {names}"
        return msg


# ---------- 範例 / 自我測試 ----------

if __name__ == "__main__":
    import json

    msg1 = "🔮 弱點屬性:🔴火屬性(帶 🔴火屬性 五行的陀螺打傷害更高)\n"
    msg2 = "五行 🔴火屬性 → 🟡土屬性　類型 持久型 → 攻擊型　新弱點:🟢木屬性\n"

    for msg in (msg1, msg2):
        state = WeaknessParser.parse(msg)
        print(f"訊息: {msg.strip()}")
        print(f"解析結果: {state}")
        print()

    with open("/mnt/user-data/uploads/tops.json", encoding="utf-8") as f:
        tops = json.load(f)["detailed"]

    for msg in (msg1, msg2):
        state = WeaknessParser.parse(msg)
        picks = TopSelector.recommend(tops, state)
        warning = TopSelector.missing_element_warning(tops, state)
        print(f"=== 依「{msg.strip()}」判斷 ===")
        if warning:
            print(warning)
        for p in picks:
            print(f"  建議出戰: {p['name']}  戰力:{p['power']}  強化:+{p.get('enhancement')}")
        print()
