# -*- coding: utf-8 -*-
"""
furnace_matcher.py

職責:
  從爐火(觀火/投爐)相關訊息裡,解析「爆射結果」這個三種 shape
  (furnace_watch/furnace_feed/furnace_awakening_announcement)都可能出現
  的共同區塊——跟 weakness_matcher.py 是同樣的設計理由:避免三份 shape
  各自維護一份重複的 regex,共用的是「解析邏輯」本身,不是共用檔案讀取。

目前只驗證過 5 則實際樣本(橙焰・旋能不竭 ×2、綠焰・豐饒之綠 ×1、
普通觀火/投爐各 ×1),爆射色系目前只看過橙/綠兩種,其他色系(訊息裡看到
的 emoji 至少還有 🟣紫/🔵藍/🔴紅/⚪白 這幾種顏色會出現在「爐內火光」的
對比描述,但還沒看過那些顏色實際「爆射」時的效果文字長怎樣)理論上應該
符合同一種格式(「這次是/這一爆是/這次的爆射會是【○焰・○○】」),但效果
描述(討伐上限提升/福袋加成之類)的實際變化組合還沒被完整驗證過,之後
遇到新色系的樣本要記得回頭核對。

本模組不碰 monitor / executor,只做「純判斷」。
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

# 爆射色系標頭,三種 shape 各自用不同的起手句,格式共通:
#   🟠 這次的爆射會是【橙焰・旋能不竭】。                    (觀火燒穿當下,個人)
#   🟠 這次是【橙焰・旋能不竭】:今日討伐上限大幅提升...      (覺醒公告,廣播)
#   🟢 這一爆是【綠焰・豐饒之綠】!                            (投爐引爆,個人)
# 「:」後面接的效果描述只有廣播公告那則會整段放在同一行,其餘兩種效果描述
# 是另外幾行(各自 shape 自己抓),這裡抓不到 effect 就是 None,不代表沒有。
RE_BURST_HEADER = re.compile(
    r"(?:這次的爆射會是|這一爆是|這次是)【(?P<color>[^・]+)・(?P<name>[^】]+)】"
    r"(?:[:：](?P<inline_effect>[^\n]+))?"
)

# 個人觀火燒穿時的「爐火之眼」登記(還不知道最後獎勵給誰、哪隻王):
#   👁️ 你被記為【爐火之眼】——爆射後,待世界王倒下才領得到重謝。
RE_EYE_PENDING = re.compile(r"你被記為【爐火之眼】")

# 投爐引爆後,「爐火之眼」報酬正式登記(已經知道是哪隻王、哪個人):
#   👁️ 【爐火之眼】熊 的報酬已登記——「無光級・夔潮」倒下時發放。
RE_EYE_REGISTERED = re.compile(r"【爐火之眼】(?P<name>\S+?)\s*的報酬已登記——「(?P<boss_name>[^」]+)」倒下時發放")

# 引爆者: (由 @envpop 投下最後一片引爆)
RE_IGNITOR = re.compile(r"由\s*(?P<name>\S+?)\s*投下最後一片引爆")

# 連環爆射次數: 🔥 熔爐連環大爆射!這段時間共爆 2 次,以下是最新一發——
RE_CASCADE_COUNT = re.compile(r"這段時間共爆\s*(?P<count>\d+)\s*次")


@dataclass
class FurnaceBurst:
    color: str                                # 例如 "橙焰"、"綠焰"
    burst_name: str                           # 例如 "旋能不竭"、"豐饒之綠"
    inline_effect: Optional[str] = None       # 只有覺勵廣播那種格式,效果文字跟標頭同一行才有

    eye_pending: bool = False                 # 觀火燒穿當下,還不知道獎勵綁哪隻王
    eye_registered_name: Optional[str] = None # 投爐引爆後,已登記的「爐火之眼」玩家名
    eye_registered_boss: Optional[str] = None # 已登記要等哪隻王倒下才發放

    ignitor_name: Optional[str] = None        # 投下最後一片引爆的人
    cascade_count: Optional[int] = None       # 這段時間內累計爆了幾次


class FurnaceParser:
    """純解析,不做任何 I/O 或決策"""

    @staticmethod
    def parse_burst(message: str) -> Optional[FurnaceBurst]:
        header = RE_BURST_HEADER.search(message)
        if not header:
            return None

        eye_reg = RE_EYE_REGISTERED.search(message)
        ignitor = RE_IGNITOR.search(message)
        cascade = RE_CASCADE_COUNT.search(message)

        return FurnaceBurst(
            color=header.group("color"),
            burst_name=header.group("name"),
            inline_effect=header.group("inline_effect").strip() if header.group("inline_effect") else None,
            eye_pending=bool(RE_EYE_PENDING.search(message)),
            eye_registered_name=eye_reg.group("name") if eye_reg else None,
            eye_registered_boss=eye_reg.group("boss_name") if eye_reg else None,
            ignitor_name=ignitor.group("name") if ignitor else None,
            cascade_count=int(cascade.group("count")) if cascade else None,
        )
