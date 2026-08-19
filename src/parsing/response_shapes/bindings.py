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
        parts.append(f"五行{element_stage['element']}{element_stage['stage']}階")

    for t in b.get("talents") or []:
        parts.append(f"{t['name']}{t['level']}")

    tail = "・".join(parts) if parts else "（無天賦項目）"

    resonance = b.get("resonance") or []
    if resonance:
        tail += f"｜共鳴:{'/'.join(resonance)}"

    return tail


def _format_binding_line(b: dict) -> str:
    marker = "⚔️" if b.get("is_active") else ""
    enh = f" +{b['enhancement']}" if b.get("enhancement") else ""
    bind_tag = f"　{b['bind_type']}{b.get('bind_tier') or ''}" if b.get("bind_type") else ""
    header = f"#{b['index']} {marker}{b['name']}{enh}{bind_tag}｜{b['build']}・戰力{b['power']}"

    mastery = b.get("mastery") or {}
    mastery_str = f"熟練{mastery.get('current', '?')}/{mastery.get('max', '?')}(可兌{b.get('exchange_available', 0)}次)"

    return f"{header}　{mastery_str}　{_format_talent_tail(b)}"


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