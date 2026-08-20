# -*- coding: utf-8 -*-
"""
parsing/response_shapes/forge_result.py

「鑄造完成」訊息的 shape module。解析邏輯直接沿用既有的
forge_result_parser.parse_forge_result()，不重寫一份 regex——這支
shape 只是把它包成 signature()/parse()/format_for_display() 三件套，
讓 response_parser.py 能正式辨識這則訊息、profile_sync_strategy.py
能讀 structured 寫回 cast_tops_catalog.json。

=== 2026-08-19 補上 ===
forge_result_parser.py 這支檔案本身寫得很完整（parse_forge_result／
add_cast_entry 都有），但從寫好到現在沒有任何地方呼叫它——鑄造完成
訊息從來沒有被送進這支解析器過，導致新鑄造的陀螺全部沒被記錄進
cast_tops_catalog.json（實測 #36 封玉／#37 歸無 兩顆查不到屬性，
根因就是這個，不是機率問題）。這支 shape 是補上這條路徑的關鍵一塊。
"""

import re

from forge_result_parser import parse_forge_result

_SIGNATURE_RE = re.compile(r"你設計的「.+?」出爐")


def signature(text: str) -> bool:
    return bool(_SIGNATURE_RE.search(text or ""))


def parse(text: str) -> dict:
    result = parse_forge_result(text)
    if result is None:
        return {"name": None, "raw_text": text}

    d = {
        "name": result.name,
        "rarity": result.rarity,
        "stars": result.stars,
        "tier_label": result.tier_label,
        "type": result.type,
        "element": result.element,
        "element_stage": result.element_stage,
        "atk": result.atk,
        "defense": result.defense,
        "endurance": result.endurance,
        "power": result.power,
        "raw_text": text,
    }
    return d


def format_for_display(parsed: dict) -> str:
    return parsed["raw_text"]


if __name__ == "__main__":
    sample = """⚒️✨ 鑄造完成！你設計的「一刀」出爐！
──────────────
稀有度:SSR ✦✦✦（高階檔）
類型:防禦型　天生五行:🟢木(1 階)
數值:攻 40／防 56／耐 46　戰力 148
──────────────
打「我的陀螺」看收藏,「出戰 編號」派它上場 🦊"""

    print("signature() =", signature(sample))
    parsed = parse(sample)
    print(parsed)