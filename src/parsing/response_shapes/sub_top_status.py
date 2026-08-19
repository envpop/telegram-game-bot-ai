# -*- coding: utf-8 -*-
"""
parsing/response_shapes/sub_top_status.py

「副陀螺」無參數查詢的回覆，兩種變體：
    未裝：
        🌗 副陀螺
        ──────────────
        （還沒裝）
        ...

    已裝：
        🌗 副陀螺
        ──────────────
        🌗 副陀螺：崩嶽神熊・摸摸撼地GO（副屬性 🟡土屬性・爆擊率分你一半）
        ...

跟 sub_top_confirmation.py 的差異：這則沒有「副陀螺設為：」那行（那是
切換動作才有的確認訊息），純粹是查詢當下狀態。加成描述的解析邏輯共用
sub_top_confirmation.py 的 _parse_extra()，不重寫一份。
"""

import re

from .sub_top_confirmation import RE_LINE2, _parse_extra

_HEADER = "🌗 副陀螺\n──────────────"
_EMPTY_MARK = "（還沒裝）"


def signature(text: str) -> bool:
    text = text or ""
    return text.startswith(_HEADER)


def parse(text: str) -> dict:
    if _EMPTY_MARK in text:
        return {"equipped": False, "name": None, "element": None,
                 "bind_bonus": None, "resonance_bonus": False, "raw_text": text}

    m = RE_LINE2.search(text)
    if not m:
        # 理論上不會發生：signature() 已先確認是這個 shape，內容格式跟
        # 已知兩種變體都不符——防禦性 fallback，不假裝解析成功。
        return {"equipped": None, "name": None, "element": None,
                 "bind_bonus": None, "resonance_bonus": False, "raw_text": text}

    bind_bonus, resonance = _parse_extra(m.group("extra"))
    return {
        "equipped": True,
        "name": m.group("name").strip(),
        "element": m.group("element"),
        "bind_bonus": bind_bonus,
        "resonance_bonus": resonance,
        "raw_text": text,
    }


def format_for_display(parsed: dict) -> str:
    return parsed["raw_text"]


if __name__ == "__main__":
    empty_sample = """🌗 副陀螺
──────────────
（還沒裝）

「副陀螺 <編號>」裝上一顆。編號打「我的陀螺」看。
限制:不能跟出戰陀螺同一顆,而且五行要跟主手不同。"""

    set_sample = """🌗 副陀螺
──────────────
🌗 副陀螺：磐古神熊・摸摸鎮岳GO（副屬性 🟡土屬性・開場護盾）

副陀螺不加三圍,只給:
・被剋制時改用副手的五行判定(最多打回持平,拿不到剋制加成)"""

    for label, s in [("未裝", empty_sample), ("已裝", set_sample)]:
        print(f"=== {label} ===")
        print("signature() =", signature(s))
        print(parse(s))
        print()