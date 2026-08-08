# -*- coding: utf-8 -*-
"""
human_readable_report.py

將 inventory_parsers.py 產出的 tops.json / satellites.json
轉換成人類可讀的文字報告,並附上基本的資料完整性檢查。

用法:
    python human_readable_report.py tops.json satellites.json > report.txt
    (也可以只丟一個檔案)
"""

import json
import sys
from collections import Counter


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------- 完整性檢查 ----------

def check_integrity(data, label):
    """
    檢查資料本身宣稱的完整性:
    - total_count / declared_count / 實際陣列長度是否一致
    - is_complete 旗標
    - match_key / name 是否有重複(理論上陀螺不會重複,重複代表解析出錯)
    """
    lines = []
    lines.append(f"【{label} 完整性檢查】")

    total = data.get("total_count")
    declared = data.get("declared_count")
    is_complete = data.get("is_complete")

    items = data.get("detailed") or data.get("satellites") or []
    actual = len(items)

    lines.append(f"  total_count={total}  declared_count={declared}  實際筆數={actual}  is_complete={is_complete}")

    problems = []
    if total is not None and total != actual:
        problems.append(f"total_count({total}) 與實際筆數({actual}) 不一致")
    if declared is not None and declared != actual:
        problems.append(f"declared_count({declared}) 與實際筆數({actual}) 不一致")
    if is_complete is False:
        problems.append("資料本身標記為不完整 (is_complete=false)")

    # 重複檢查:陀螺用 match_key,衛星用 name+index
    if "detailed" in data:
        keys = [t.get("match_key") or t.get("name") for t in items]
    else:
        keys = [s.get("name") for s in items]
    dup = [k for k, c in Counter(keys).items() if c > 1]
    if dup:
        problems.append(f"發現重複項目: {dup}")

    if problems:
        lines.append("  ⚠️ 發現問題:")
        for p in problems:
            lines.append(f"     - {p}")
    else:
        lines.append("  ✅ 未發現明顯問題")

    return "\n".join(lines)


# ---------- 陀螺報告 ----------

def format_tops(data):
    out = [check_integrity(data, "陀螺 (tops)"), ""]

    summary = data.get("rarity_summary", {})
    out.append(f"稀有度統計: {summary}")
    out.append("")

    tops = data.get("detailed", [])

    # 依 element 分組,方便人工確認每個屬性有哪些陀螺可用
    by_element = {}
    no_element = []
    for t in tops:
        el = t.get("element")
        if el:
            by_element.setdefault(el, []).append(t)
        else:
            no_element.append(t)

    for el in ["火", "土", "金", "木", "水"]:
        group = by_element.get(el, [])
        out.append(f"=== 屬性:{el} ({len(group)} 隻) ===")
        for t in sorted(group, key=lambda x: -(x.get("power") or 0)):
            out.append(_format_one_top(t))
        out.append("")

    if no_element:
        out.append(f"=== 屬性未知/未綁定 ({len(no_element)} 隻) ===")
        out.append("  (這些陀螺沒有 binding 資料,可能尚未綁定屬性五行,無法用於屬性剋制判斷)")
        for t in sorted(no_element, key=lambda x: -(x.get("power") or 0)):
            out.append(_format_one_top(t))
        out.append("")

    return "\n".join(out)


def _format_one_top(t):
    name = t.get("name", "?")
    power = t.get("power", "?")
    enh = t.get("enhancement", "?")
    status = t.get("status", "?")
    typ = t.get("type", "?")
    rarity = t.get("rarity", "?")
    bind = t.get("bind_type", "")
    tier = t.get("bind_tier", "")
    src = t.get("source_category", "")
    line = f"  [{rarity}/{src}] {name} +{enh}  戰力:{power}  類型:{typ}  狀態:{status}"
    if bind:
        line += f"  綁定:{bind}{tier}"
    binding = t.get("binding")
    if binding:
        talents = binding.get("talents", [])
        talent_str = "、".join(f"{tal['name']}Lv{tal['level']}" for tal in talents)
        stage = binding.get("element_stage") or {}
        line += f"\n      天賦: {talent_str}  五行階段: {stage.get('element')}{stage.get('stage')}"
    return line


# ---------- 衛星報告 ----------

def format_satellites(data):
    out = [check_integrity(data, "衛星 (satellites)"), ""]

    sats = data.get("satellites", [])
    equip_limit = data.get("equip_limit")
    out.append(f"裝備上限: {equip_limit}")
    out.append("")

    active = [s for s in sats if s.get("is_active")]
    bench = [s for s in sats if not s.get("is_active")]

    out.append(f"=== 出戰中 ({len(active)} 隻) ===")
    for s in active:
        out.append(_format_one_sat(s))
    out.append("")

    out.append(f"=== 待機 ({len(bench)} 隻),依評分排序 ===")
    for s in sorted(bench, key=lambda x: -(x.get("score") or 0)):
        out.append(_format_one_sat(s))

    return "\n".join(out)


def _format_one_sat(s):
    name = s.get("name", "?")
    grade = s.get("grade", "?")
    score = s.get("score", "?")
    build = s.get("build", "?")
    trait = s.get("trait", "?")
    skills = "、".join(s.get("skills", []))
    return f"  [{grade}] {name}  評分:{score}  流派:{build}  性格:{trait}\n      技能: {skills}"


def main():
    if len(sys.argv) < 2:
        print("用法: python human_readable_report.py tops.json [satellites.json]")
        sys.exit(1)

    chunks = []
    for path in sys.argv[1:]:
        data = load_json(path)
        if "detailed" in data:
            chunks.append(format_tops(data))
        elif "satellites" in data:
            chunks.append(format_satellites(data))
        else:
            chunks.append(f"⚠️ 無法辨識的資料格式: {path}")

    print("\n\n".join(chunks))


if __name__ == "__main__":
    main()
