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

from battle_status import resolve_element_any


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

# 王名(出現公告): 👹 今日世界王【第 5 階】:深海級・幽壑（攻擊型・🟢木屬性）
#              👹 今日世界王:潮痕級・黑瞳（攻擊型・🟡土屬性）
#   —— 每天重生的第一隻王(潮痕級/base tier)沒有「【第 X 階】」這個階數標記，
#   2026-09-02 用實際訊息樣本確認過，階數括號要設成可有可無，不然 base tier
#   王的名字整個抓不到。
# 跟 world_boss_catalog.json 的 boss_spawn.name_pattern 是同一種格式，
# 這裡獨立寫一份 regex 是因為 weakness_matcher 是純解析模組，不依賴 catalog 檔案；
# 如果之後兩邊的格式分岔了，要記得一起改。
RE_BOSS_NAME_SPAWN = re.compile(r"今日世界王(?:【第\s*\d+\s*階】)?[:：]\s*([^（]+)（")

# 王的類型(攻擊型/防禦型/持久型/平衡型)，同一行(標頭)的另一個欄位。
# 2026-09-03 補上：這個欄位在 dataclass 裡本來就有(boss_type)，但
# "initial"(剛出現)這個分支之前沒有抓，導致一般查詢時 boss_type 永遠是
# None，類型剋制判斷用不到——現在補齊，跟 RE_BOSS_NAME_SPAWN 共用同一種
# 「階數括號可有可無」的容錯。
RE_BOSS_TYPE_SPAWN = re.compile(r"今日世界王(?:【第\s*\d+\s*階】)?[:：]\s*[^（]+（(?P<type>[^・]+)・")

# 王名(相位轉變公告): 🌗💥「深海級・幽壑」的形體崩解重組……
# 同樣對應 world_boss_catalog.json 的 phase_transition.name_pattern。
RE_BOSS_NAME_TRANSITION = re.compile(r"「([^」]+)」的形體崩解重組")

# 相位: 🌗 相位 1/3【本體】 或 進入【崩相】(相位 2/3)——兩種訊息共用同一種寫法
RE_PHASE = re.compile(r"相位\s*(\d+)\s*/\s*(\d+)")

# 護衛存活數: 🛰️ 衛星護衛 5/5 顆還在
# 只在出現公告裡看得到，相位轉變公告目前沒有這行，抓不到就維持 None，
# 不代表護衛消失了——護衛清空要靠 is_guards_cleared() 另一種訊息判斷。
RE_GUARDS_ALIVE = re.compile(r"衛星護衛\s*(\d+)\s*/\s*(\d+)\s*顆還在")

# 護衛清空公告(獨立一則訊息，不含王名/弱點，例如：
# 「🛰️💥 Gene_433721 擊碎最後一顆 護衛星・裂星!王的減傷全數消失——全服接下來 10 分鐘傷害 +15%!」)
RE_GUARDS_CLEARED = re.compile(r"王的減傷全數消失")


@dataclass
class WeaknessState:
    current_element: str          # 目前應該打的屬性
    boss_element: Optional[str] = None   # BOSS 目前自身五行(轉變後才有)
    boss_type: Optional[str] = None      # BOSS 目前類型(轉變後才有)
    source: str = ""              # 判斷依據: "initial" 或 "transition"

    boss_name: Optional[str] = None      # 王名(用來跟 world_boss_progress 的記錄 key 對齊)
    phase: Optional[int] = None          # 目前相位，例如 1、2、3
    phase_total: Optional[int] = None    # 「相位 X/Y」的 Y，不同王可能不同，照文字實際抓，不寫死
    has_guards: Optional[bool] = None    # 這則訊息裡有沒有提到護衛存活數；沒提到就是 None，
                                          # 不代表沒有護衛——呼叫端要自己保留上一次已知值，
                                          # 只有遇到 is_guards_cleared() 才明確轉 False

    # 雙屬性切換王(例如「淵影對舞」機制)的預留位置。
    # 目前不主動解析切換規則，只有訊息裡剛好帶出目前生效中的另一屬性時才會用到，
    # 抓不到就維持 None。之後要接 ALIAS 快速套用雙屬性打法時，從這裡擴充。
    dual_element_mode: bool = False
    element_alt: Optional[str] = None


class WeaknessParser:
    """純解析,不做任何 I/O 或決策"""

    @staticmethod
    def parse(message: str) -> Optional[WeaknessState]:
        guards_alive = RE_GUARDS_ALIVE.search(message)
        has_guards = (int(guards_alive.group(1)) > 0) if guards_alive else None

        m = RE_TRANSITION.search(message)
        if m:
            _, boss_element, _, boss_type, new_weakness = m.groups()
            phase_m = RE_PHASE.search(message)
            name_m = RE_BOSS_NAME_TRANSITION.search(message)
            return WeaknessState(
                current_element=new_weakness,
                boss_element=boss_element,
                boss_type=boss_type,
                source="transition",
                boss_name=name_m.group(1) if name_m else None,
                phase=int(phase_m.group(1)) if phase_m else None,
                phase_total=int(phase_m.group(2)) if phase_m else None,
                has_guards=has_guards,
            )

        m = RE_INITIAL_WEAKNESS.search(message)
        if m:
            phase_m = RE_PHASE.search(message)
            name_m = RE_BOSS_NAME_SPAWN.search(message)
            type_m = RE_BOSS_TYPE_SPAWN.search(message)
            return WeaknessState(
                current_element=m.group(1),
                boss_type=type_m.group("type") if type_m else None,
                source="initial",
                boss_name=name_m.group(1) if name_m else None,
                phase=int(phase_m.group(1)) if phase_m else None,
                phase_total=int(phase_m.group(2)) if phase_m else None,
                has_guards=has_guards,
            )

        return None

    @staticmethod
    def is_guards_cleared(message: str) -> bool:
        """獨立判斷式，對應護衛清空公告(不含王名/弱點的另一種 shape)。
        呼叫端看到 True 時，把該王記錄的 has_guards 轉成 False。
        """
        return bool(RE_GUARDS_CLEARED.search(message))


class TopSelector:
    """
    根據 WeaknessState 從 tops 清單(tops.json 的 detailed 陣列)選出建議陀螺。
    """

    @staticmethod
    def recommend(tops: List[dict], weakness: WeaknessState, top_n: int = 3,
                   catalog: Optional[dict] = None) -> List[dict]:
        """
        回傳依優先序排列的建議陀螺清單。

        排序邏輯:
          1. element 完全符合弱點屬性 的陀螺優先
          2. 同屬性內,依戰力 (power) 由高到低
          3. 若同屬性戰力相同,強化值 (enhancement) 高者優先

        屬性比對改用 resolve_element_any()（頂層 element → binding.element_stage.element
        → catalog fallback），不直接讀 t.get("element")——這樣呼叫端不管有沒有
        事先做過 resolve_roster()，這裡都會拿到正確答案，不會因為漏了那一步
        而安靜地漏選陀螺（2026-08-15 討論過的隱性耦合風險，這裡收斂掉）。
        catalog 不傳就只看頂層/binding，行為跟舊版一致。

        注意:兩步都查不到屬性的陀螺不會被選入,寧可不選也不要猜。
        """
        candidates = [
            t for t in tops
            if resolve_element_any(t, catalog) == weakness.current_element
        ]

        if not candidates:
            return []

        candidates.sort(
            key=lambda t: (t.get("power") or 0, t.get("enhancement") or 0),
            reverse=True,
        )
        return candidates[:top_n]

    @staticmethod
    def recommend_pair(tops: List[dict], weakness: WeaknessState, rules: dict,
                        boss_type: Optional[str] = None, catalog: Optional[dict] = None):
        """
        依世界王弱點屬性+類型，選出「主手+副手」搭配：

          主手 對王：
            五行要剋制 —— 屬性符合弱點(weakness.current_element)，這點
                          本來就對，沿用不變。
            類型也要剋制 —— 查 rules["type_control"]，攻>持>防>攻 三角，
                          找哪個類型剋制王的類型。王是「平衡型」(三角
                          沒涵蓋，見 rules["type_no_counter"])時，優先選
                          攻擊型。
          副手 對主手：
            五行要生 —— 查 rules["element_generate"]，找哪個屬性生主手
                        屬性，沿用不變。
            類型要互補 —— 主手是攻擊型就選防禦型副手，主手不是攻擊型就
                        選攻擊型副手，以取得最高傷害的可能。
                        (2026-09-03 熊確認的規則)

        屬性/五行是硬性條件(候選名單先用它篩選)，類型是排序時的優先加分
        (同屬性候選裡，類型對的排前面，同樣戰力優先)——這樣即使手上找不到
        「屬性又對、類型也對」的完美陀螺，也不會整組挑不出東西，跟之前
        「挑手上最好的」的既有原則一致，只是現在的「最好」多考慮了類型。

        找不到主手就整組回傳 None,None（弱點屬性手上完全沒有能打的陀螺，
        副手怎麼選都沒意義）。副手找不到相生屬性的候選時，回傳 sub=None，
        不強塞一顆不相生的陀螺進去。
        """
        main_candidates = [
            t for t in tops
            if resolve_element_any(t, catalog) == weakness.current_element
        ]
        if not main_candidates:
            return None, None

        type_control = rules.get("type_control", {})
        no_counter_types = set(rules.get("type_no_counter", {}).get("types", []))

        def _type_that_beats(target_type):
            if not target_type or target_type in no_counter_types:
                return None
            for attacker, defender in type_control.items():
                if defender == target_type:
                    return attacker
            return None

        desired_main_type = _type_that_beats(boss_type) or "攻擊型"

        main_candidates.sort(
            key=lambda t: (
                t.get("type") == desired_main_type,
                t.get("power") or 0,
                t.get("enhancement") or 0,
            ),
            reverse=True,
        )
        main_pick = main_candidates[0]

        generating_element = None
        for src, dst in rules.get("element_generate", {}).items():
            if dst == weakness.current_element:
                generating_element = src
                break

        sub_pick = None
        if generating_element:
            sub_candidates = [
                t for t in tops
                if t is not main_pick and resolve_element_any(t, catalog) == generating_element
            ]
            if sub_candidates:
                desired_sub_type = "防禦型" if main_pick.get("type") == "攻擊型" else "攻擊型"
                sub_candidates.sort(
                    key=lambda t: (
                        t.get("type") == desired_sub_type,
                        t.get("power") or 0,
                        t.get("enhancement") or 0,
                    ),
                    reverse=True,
                )
                sub_pick = sub_candidates[0]

        return main_pick, sub_pick

    @staticmethod
    def missing_element_warning(tops: List[dict], weakness: WeaknessState,
                                 catalog: Optional[dict] = None) -> Optional[str]:
        """
        若手上完全沒有符合弱點屬性的陀螺,回傳警告文字,方便印出提醒人工介入。
        """
        candidates = [
            t for t in tops
            if resolve_element_any(t, catalog) == weakness.current_element
        ]
        if candidates:
            return None
        unbound = [t for t in tops if resolve_element_any(t, catalog) is None]
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