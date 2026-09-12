# -*- coding: utf-8 -*-
"""
satellite_catalog_display.py —— 衛星圖鑑「重點顯示」格式化

設計依據（跟熊討論定案）：
  - 多頁原始資料照樣讀進 satellites.json（parser 不變），這裡只負責「怎麼顯示」。
  - 分三段，不是統計 vs 融合二選一，而是各自服務不同用途：

    1. 主力清單：技能數（len(skills)）>= MAIN_THRESHOLD 的衛星
       → 保留原編號、完整技能列表（這段本來就要看細節，融合價值最高）

    2. 特殊金技清單：擁有 SPECIAL_GOLD_SKILLS 任一者（星隕/山嶽/汲魂/過載）
       → 不管技能數多寡都獨立列出（因為這4種是稀有隨機取得，價值不看數量）
       → 只列「編號＋擁有的特殊金技＋技能數」，不列完整技能（熊確認不用太在乎其他技能）
       → 跟第一段可能重複（一顆衛星技能數>=8 又剛好有特殊金技，兩段都會出現，
         這是刻意的：兩段的「查詢用途」不同，重複不是 bug）

    3. 其餘：依「技能組合」分組（不是依技能數字統計）
       → 相同技能組合的衛星等價，分到同一組，組內編號用區間壓縮
       → 比純數字統計（"7技x3隻"）更有用：能直接看到那組技能是什麼

  - 編號一律用原始 index（來自伺服器回應 #N），不重新編號。
  - compress_ids() 是共用小工具，把連續編號壓成區間（45,48,51-53），
    第二、三段都用得到，之後如果要做「技能反查索引」也能共用。
"""

from collections import defaultdict

MAIN_THRESHOLD = 8
SPECIAL_GOLD_SKILLS = ["星隕", "山嶽", "汲魂", "過載"]


def compress_ids(ids):
    """把遞增編號清單壓縮成區間字串：[45,48,51,52,53] -> '45,48,51-53'"""
    ids = sorted(set(ids))
    if not ids:
        return ""
    ranges = []
    start = prev = ids[0]
    for i in ids[1:]:
        if i == prev + 1:
            prev = i
            continue
        ranges.append((start, prev))
        start = prev = i
    ranges.append((start, prev))
    return ",".join(f"{a}-{b}" if a != b else f"{a}" for a, b in ranges)


def _find_special_gold(skills):
    """回傳這顆衛星身上出現的特殊金技名稱（可能不只一個）。"""
    found = []
    for special in SPECIAL_GOLD_SKILLS:
        if any(special in s for s in skills):
            found.append(special)
    return found


def _skill_combo_key(skills):
    """技能組合分組 key：同一組合視為等價（順序不影響分組）。"""
    return tuple(sorted(skills))


def classify_satellites(satellites):
    """把衛星分成三組，回傳 dict。"""
    main_list = []
    special_gold = []
    remaining = []

    for sat in satellites:
        skills = sat.get("skills", [])
        skill_count = len(skills)
        specials = _find_special_gold(skills)

        if skill_count >= MAIN_THRESHOLD:
            main_list.append(sat)

        if specials:
            special_gold.append((sat, specials))

        if skill_count < MAIN_THRESHOLD and not specials:
            remaining.append(sat)

    return {
        "main_list": sorted(main_list, key=lambda s: s["index"]),
        "special_gold": sorted(special_gold, key=lambda t: t[0]["index"]),
        "remaining": remaining,
    }


def format_main_list(main_list):
    if not main_list:
        return ""
    lines = ["【主力｜普金技合計 ≥%d】(%d隻)" % (MAIN_THRESHOLD, len(main_list))]
    for sat in main_list:
        skills = sat.get("skills", [])
        active_tag = "⚔️" if sat.get("is_active") else ""
        lines.append(
            "#%d %s%s（%s級·%d分·%s）%d技：%s"
            % (
                sat["index"],
                active_tag,
                sat["name"],
                sat["grade"],
                sat["score"],
                sat["build"],
                len(skills),
                "、".join(skills),
            )
        )
    return "\n".join(lines)


def format_special_gold(special_gold):
    """每種特殊金技各一行：技能名 → 編號清單。沒有任何衛星擁有的種類直接不顯示。"""
    if not special_gold:
        return ""

    owners_by_skill = defaultdict(list)  # 星隕/山嶽/汲魂/過載 -> [index,...]
    for sat, specials in special_gold:
        for s in specials:
            owners_by_skill[s].append(sat["index"])

    lines = ["【特殊金技擁有者】"]
    for skill in SPECIAL_GOLD_SKILLS:  # 固定順序：星隕/山嶽/汲魂/過載
        ids = owners_by_skill.get(skill)
        if not ids:
            continue
        lines.append("%s → #%s" % (skill, compress_ids(ids)))
    return "\n".join(lines)


def format_remaining(remaining):
    if not remaining:
        return ""

    by_count = defaultdict(lambda: defaultdict(list))  # skill_count -> combo -> [index,...]
    for sat in remaining:
        skills = sat.get("skills", [])
        combo = _skill_combo_key(skills)
        by_count[len(skills)][combo].append(sat["index"])

    lines = ["【其餘】(%d隻，依技能組合分組)" % len(remaining)]
    for count in sorted(by_count.keys(), reverse=True):
        lines.append(f"{count}技：")
        combos = by_count[count]
        # 組內按「該組第一個編號」排序，讓輸出順序穩定、好對照
        for combo in sorted(combos.keys(), key=lambda c: min(combos[c])):
            ids = combos[combo]
            skill_str = "、".join(combo) if combo else "（無技能）"
            lines.append("  %s → #%s" % (skill_str, compress_ids(ids)))
    return "\n".join(lines)


def format_satellite_catalog(data):
    """主入口：吃 parse_satellite_catalog() 的回傳 dict，輸出重點顯示文字。"""
    satellites = data.get("satellites", [])
    groups = classify_satellites(satellites)

    parts = [
        "🛰️ 衛星圖鑑摘要（共 %d 隻）" % data.get("total_count", len(satellites)),
        "──────────────",
    ]
    # 2026-08-17 熊要求區塊順序反過來：最上面放技能少的（其餘，7技以下），
    # 中間維持特殊金技，最下面放技能多的（主力，≥8技）——這樣不用捲動
    # 就能同時看到「技能最少」（其餘區塊最上緣）跟「技能最多」（主力區塊
    # 貼在訊息最下面）兩端。其餘區塊內部排序不變（仍是 7→1 技由多到少，
    # 單一技能維持在該區塊最下面）。
    for formatter, group_key in (
        (format_remaining, "remaining"),
        (format_special_gold, "special_gold"),
        (format_main_list, "main_list"),
    ):
        text = formatter(groups[group_key])
        if text:
            parts.append(text)
            parts.append("──────────────")

    return "\n".join(parts).rstrip("─\n").rstrip() 


if __name__ == "__main__":
    import json

    with open("/mnt/user-data/uploads/satellites.json", encoding="utf-8") as f:
        data = json.load(f)

    print(format_satellite_catalog(data))