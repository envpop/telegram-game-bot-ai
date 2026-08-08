# -*- coding: utf-8 -*-
"""
csv_export.py

將 tops.json / satellites.json 攤平成 CSV,方便匯入 Google Sheets。
巢狀欄位 (binding.talents / skills) 會被攤平成單一字串欄位,
方便在 Sheets 用篩選器 (Filter view) 或樞紐分析表操作。

用法:
    python csv_export.py tops.json tops.csv
    python csv_export.py satellites.json satellites.csv
"""

import csv
import json
import sys


TOP_FIELDS = [
    "index", "name", "base_name", "match_key", "rarity", "source_category",
    "type", "element", "power", "stars", "enhancement", "status",
    "evolution_marker", "bind_type", "bind_tier",
    "mastery_current", "mastery_max", "element_stage",
    "talents", "resonance",
]

SAT_FIELDS = [
    "index", "name", "is_active", "grade", "score", "build", "trait",
    "attack", "defense", "hp", "attack_pct", "defense_pct", "hp_pct",
    "skills", "normal_used", "normal_total", "gold_used", "gold_total",
]


def flatten_top(t):
    binding = t.get("binding") or {}
    mastery = binding.get("mastery") or {}
    stage = binding.get("element_stage") or {}
    talents = binding.get("talents") or []
    row = {
        "index": t.get("index"),
        "name": t.get("name"),
        "base_name": t.get("base_name"),
        "match_key": t.get("match_key"),
        "rarity": t.get("rarity"),
        "source_category": t.get("source_category"),
        "type": t.get("type"),
        "element": t.get("element"),
        "power": t.get("power"),
        "stars": t.get("stars"),
        "enhancement": t.get("enhancement"),
        "status": t.get("status"),
        "evolution_marker": t.get("evolution_marker"),
        "bind_type": t.get("bind_type"),
        "bind_tier": t.get("bind_tier"),
        "mastery_current": mastery.get("current"),
        "mastery_max": mastery.get("max"),
        "element_stage": f"{stage.get('element','')}{stage.get('stage','')}" if stage else "",
        "talents": "、".join(f"{tal['name']}Lv{tal['level']}" for tal in talents),
        "resonance": "、".join(binding.get("resonance") or []),
    }
    return row


def flatten_sat(s):
    base = s.get("base_stats") or {}
    bonus = s.get("bonus") or {}
    slots = s.get("slots") or {}
    row = {
        "index": s.get("index"),
        "name": s.get("name"),
        "is_active": s.get("is_active"),
        "grade": s.get("grade"),
        "score": s.get("score"),
        "build": s.get("build"),
        "trait": s.get("trait"),
        "attack": base.get("attack"),
        "defense": base.get("defense"),
        "hp": base.get("hp"),
        "attack_pct": bonus.get("attack_pct"),
        "defense_pct": bonus.get("defense_pct"),
        "hp_pct": bonus.get("hp_pct"),
        "skills": "、".join(s.get("skills") or []),
        "normal_used": slots.get("normal_used"),
        "normal_total": slots.get("normal_total"),
        "gold_used": slots.get("gold_used"),
        "gold_total": slots.get("gold_total"),
    }
    return row


def main():
    if len(sys.argv) != 3:
        print("用法: python csv_export.py <輸入.json> <輸出.csv>")
        sys.exit(1)

    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as f:
        data = json.load(f)

    # utf-8-sig 加 BOM,讓 Excel / Google Sheets 匯入中文不會亂碼
    if "detailed" in data:
        rows = [flatten_top(t) for t in data["detailed"]]
        fields = TOP_FIELDS
    elif "satellites" in data:
        rows = [flatten_sat(s) for s in data["satellites"]]
        fields = SAT_FIELDS
    else:
        print("⚠️ 無法辨識的資料格式")
        sys.exit(1)

    with open(dst, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"已匯出 {len(rows)} 筆資料到 {dst}")


if __name__ == "__main__":
    main()
