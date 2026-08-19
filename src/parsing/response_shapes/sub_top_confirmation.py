# -*- coding: utf-8 -*-
"""
parsing/response_shapes/sub_top_confirmation.py

「副陀螺 N」指令送出後的確認訊息，兩行：
    🌗 副陀螺設為：崩嶽神熊・摸摸撼地GO
    🌗 副陀螺：崩嶽神熊・摸摸撼地GO（副屬性 🟡土屬性・爆擊率分你一半・相生共鳴 +2%）

加成描述是動態的，實測樣本涵蓋這些組合（熊確認的規則）：
    - 副陀螺是爆擊/護盾/回歸其中一種綁定類型 → 對應加成文字（三選一或沒有）
      💥爆擊綁定 → "爆擊率分你一半"
      🛡️護盾綁定 → "開場護盾"
      🌀回歸綁定 → "復活機率三成"
    - 主副都已綁定 且 副手屬性生主手屬性 → 額外多「相生共鳴 +2%」
      （這條跟上面那條互相獨立，可以同時出現、只出現一個、或都不出現）

沒有戰力（power）欄位可用來消歧——跟 active_top_confirmation.py 的情況
不同，這裡只能用名字子字串比對，profile_sync_strategy.py 那邊要處理
「找不到唯一候選」的情況，不能假設一定配對得到。
"""

import re

RE_LINE1 = re.compile(r"🌗\s*副陀螺設為[:：]\s*(?P<name>.+)")
RE_LINE2 = re.compile(
    r"🌗\s*副陀螺[:：]\s*(?P<name>.+?)"
    r"（副屬性\s*\S?(?P<element>[火水木金土])屬性(?P<extra>[^）]*)）"
)

_BIND_BONUS_MAP = {
    "爆擊率分你一半": "爆擊",
    "開場護盾": "護盾",
    "復活機率三成": "回歸",
}


def signature(text: str) -> bool:
    text = text or ""
    return bool(RE_LINE1.search(text)) and "副陀螺：" in text


def _parse_extra(extra: str):
    bind_bonus = None
    resonance = False
    for token in extra.split("・"):
        token = token.strip()
        if not token:
            continue
        if token in _BIND_BONUS_MAP:
            bind_bonus = _BIND_BONUS_MAP[token]
        elif token.startswith("相生共鳴"):
            resonance = True
    return bind_bonus, resonance


def parse(text: str) -> dict:
    m1 = RE_LINE1.search(text)
    m2 = RE_LINE2.search(text)

    name = m1.group("name").strip() if m1 else None
    element = m2.group("element") if m2 else None
    bind_bonus, resonance = _parse_extra(m2.group("extra")) if m2 else (None, False)

    return {
        "name": name,
        "element": element,
        "bind_bonus": bind_bonus,      # "爆擊"/"護盾"/"回歸"/None
        "resonance_bonus": resonance,  # True/False
        "raw_text": text,
    }


def format_for_display(parsed: dict) -> str:
    return parsed["raw_text"]


if __name__ == "__main__":
    samples = [
        "🌗 副陀螺設為：崩嶽神熊・摸摸撼地GO\n🌗 副陀螺：崩嶽神熊・摸摸撼地GO（副屬性 🟡土屬性・爆擊率分你一半）",
        "🌗 副陀螺設為：極・天熊・滅卻牙\n🌗 副陀螺：極・天熊・滅卻牙（副屬性 🔴火屬性・開場護盾・相生共鳴 +2%）",
        "🌗 副陀螺設為：太初神熊・摸摸原初GO\n🌗 副陀螺：太初神熊・摸摸原初GO（副屬性 🟢木屬性・復活機率三成）",
        "🌗 副陀螺設為：磐古神熊・摸摸鎮岳GO\n🌗 副陀螺：磐古神熊・摸摸鎮岳GO（副屬性 🟡土屬性）",
        "🌗 副陀螺設為：森羅✦2\n🌗 副陀螺：森羅✦2（副屬性 ⚪金屬性・相生共鳴 +2%）",
        "🌗 副陀螺設為：🥹\n🌗 副陀螺：🥹（副屬性 🟢木屬性・相生共鳴 +2%）",
    ]
    for s in samples:
        print("signature() =", signature(s))
        print(parse(s))
        print()