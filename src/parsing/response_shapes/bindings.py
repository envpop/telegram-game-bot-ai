# -*- coding: utf-8 -*-
"""
parsing/response_shapes/bindings.py

「綁定一覽」（🔧 你的綁定陀螺天賦一覽）的 shape module。

原始格式是兩行一組（標題／熟練+天賦），這裡改成一顆陀螺一行——不需要
外部對照表，純粹是同一份訊息裡的資料重新排版，跟 my_tops.py 需要
account_id/base_dir 才能查屬性的情況不同，這裡在 shape 層直接做完整。
"""

from inventory_parsers import is_bindings_message, parse_bindings


def signature(text: str) -> bool:
    return is_bindings_message(text)


def parse(text: str) -> dict:
    return parse_bindings(text)


def _format_talent_tail(b: dict) -> str:
    if not b.get("talents_allocated"):
        return "尚未點天賦"

    parts = []
    element_stage = b.get("element_stage")
    if element_stage:
        # 屬性名字已經在 _format_binding_line() 的 headline 顯示過了，
        # 這裡只補階級數字，避免「火屬性...火3階」重複講兩次屬性名。
        parts.append(f"{element_stage['stage']}階")

    for t in b.get("talents") or []:
        parts.append(f"{t['name']}{t['level']}")

    tail = "・".join(parts) if parts else "（無天賦項目）"

    resonance = b.get("resonance") or []
    if resonance:
        tail += f"｜共鳴:{'/'.join(resonance)}"

    return tail


def _format_binding_line(b: dict) -> str:
    """單行精簡版：編號／名字／五行屬性／類型／戰力放最前面（熊選陀螺時
    優先看的四項），用「｜」跟後面的次要資訊（強化值、綁定標籤、天賦、
    共鳴）明確隔開，一眼先掃到重點，不用在一長串字裡找。熟練度／可兌換
    次數不顯示（熊確認選陀螺不需要看這個，省下來讓行更短）。

    五行屬性只有這則「綁定一覽」訊息本身能查到 element_stage 這個來源
    （已點天賦才有）；tops.json 合併後才有的 annotate_special_source()
    來源（旋神/旋王/UR精選/鑄造對照表）這裡查不到，因為這支 shape 刻意
    不吃 base_dir/account_id（見檔案開頭 docstring）。還沒點天賦的陀螺
    這裡一律顯示「屬性未知」，不代表牠真的沒有屬性，只是這個顯示層看不到
    另一個來源。
    """
    marker = "⚔️" if b.get("is_active") else ""
    element_stage = b.get("element_stage")
    element = f"{element_stage['element']}屬性" if element_stage else "屬性未知"

    headline = f"#{b['index']} {marker}{b['name']}｜{element}・{b['build']}・戰力{b['power']}"

    enh = f"+{b['enhancement']}" if b.get("enhancement") else None
    bind_tag = f"{b['bind_type']}{b.get('bind_tier') or ''}" if b.get("bind_type") else None
    talent_str = _format_talent_tail(b)

    secondary = "・".join(p for p in (enh, bind_tag, talent_str) if p)

    return f"{headline}　{secondary}"


def format_for_display(parsed: dict) -> str:
    bindings = parsed.get("bindings") or []
    if not bindings:
        return "（沒有已綁定的陀螺）"

    lines = [f"🔧 綁定陀螺天賦一覽（共 {parsed.get('total_count', len(bindings))} 顆）", "──────────────"]
    for b in bindings:
        lines.append(_format_binding_line(b))
    return "\n".join(lines)


if __name__ == "__main__":
    sample = """🔧 你的綁定陀螺天賦一覽
──────────────
#1 炎焱燚明・焚天神熊・摸摸赤焱GO +17 💥爆擊綁定IV　攻擊型・戰力 631
　熟練 420/420・可兌換 0 次｜五行火3階・破軍3・會心3・昏蝕3・噬血1・極意2・✨共鳴:連斬/蝕滅
#13 萬象歸一・原初旗艦・摸摸GO +15 💥爆擊綁定III　平衡型・戰力 396
　熟練 360/360・可兌換 12 次｜尚未點天賦"""

    print("signature() =", signature(sample))
    parsed = parse(sample)
    print(format_for_display(parsed))