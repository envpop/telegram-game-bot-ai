# -*- coding: utf-8 -*-
"""
response_shapes/advanced_event_tower_run.py —— 「連續進階活動塔」shape

三函式介面：
    signature(text) -> bool
    parse(text) -> dict
    format_for_display(parsed) -> str

只回答「這是什麼」：把訊息拆成結構化資料，不判斷要不要做什麼、
不碰 executor / scheduler、不存檔。

跟 event_tower_run.py（連續活動塔）是兩個獨立的 shape：
  - 標題不同（連續進階活動塔 vs 連續活動塔），signature 互不重疊。
  - 進階版各關的行沒有獎勵，碎片是最後面單獨一行（「進階福袋碎片 ×2｜通用碎片 ×6」）。
  兩邊訊息格式不同，所以各自解析，不共用解析函式。

【parse() 輸出】
    {
      "start_stage": 1,                  # 標題「第 N 關開始」的 N
      "tactic": "防禦/持久型穩守蓄能、其他強攻,能量滿放 GO SHOOT",  # 標題裡的自動戰術，沒有則 None
      "stages": [{"stage": 1, "status": "✅"}, ...],
      "cleared": True,                   # 訊息含「通關整座進階活動塔」
      "last_stage": 8, "total_stages": 8, "boss": "旋煉・霸",   # 「最後一戰」區段，沒有則 None
      "bag": {"name": "進階福袋", "count": 1},                 # 「獲得…福袋 ×N」，沒有則 None
      "fragments": [{"name": "進階福袋", "count": 2}, {"name": "通用", "count": 6}],
    }

【顯示設計】
只看碎片、行數越少越好：
    🌸⚔️ 連續進階活動塔 通關 8/8 關
    碎片 8｜進階福袋×2 通用×6
福袋、自動戰術、最後一戰戰況、冷卻提示都不顯示，但 parse() 仍保留在資料裡。

【已知限制】
  只看過「通關」這一種樣本。失敗／中斷的訊息格式未知，目前沒通關時顯示
  「進行至第 N 關」，碎片行若不存在則顯示「碎片 0」，都是假設，拿到失敗樣本後再修。
"""

import re


_TITLE = re.compile(
    r"連續進階活動塔\s*[（(]\s*第\s*(\d+)\s*關開始"
    r"(?:\s*[，,]\s*自動戰術\s*[：:]\s*(.+?))?\s*[）)]"
)
_STAGE_LINE = re.compile(r"^第\s*(\d+)\s*關\s*(\S+)")
_LAST_STAGE = re.compile(r"第\s*(\d+)\s*/\s*(\d+)\s*關[・·]\s*([^（(\[]+?)\s*[（(]")
_BAG = re.compile(r"獲得\s*(\S+?)\s*×\s*(\d+)")
# 碎片行：「🎇 進階福袋碎片 ×2｜✨ 通用碎片 ×6」，名稱從第一個漢字開始
_FRAGMENT = re.compile(r"^(.*?)([\u4e00-\u9fff].*?)碎片\s*×\s*(\d+)$")

_CLEARED_MARK = "通關整座進階活動塔"


def signature(text):
    return bool(_TITLE.search(text)) and any(
        _STAGE_LINE.match(line.strip()) for line in text.splitlines()
    )


def _parse_fragments(line):
    items = []
    for part in line.split("｜"):
        m = _FRAGMENT.match(part.strip())
        if m:
            items.append({"name": m.group(2).strip(), "count": int(m.group(3))})
    return items


def parse(text):
    title = _TITLE.search(text)
    stages = []
    fragments = []
    bag = None

    for raw in text.splitlines():
        line = raw.strip()
        sm = _STAGE_LINE.match(line)
        if sm:
            stages.append({"stage": int(sm.group(1)), "status": sm.group(2)})
            continue
        if "碎片" in line and "×" in line:
            found = _parse_fragments(line)
            if found:
                fragments = found
                continue
        bm = _BAG.search(line)
        if bm and bag is None:
            bag = {"name": bm.group(1), "count": int(bm.group(2))}

    last = _LAST_STAGE.search(text)
    return {
        "start_stage": int(title.group(1)) if title else None,
        "tactic": title.group(2) if title and title.group(2) else None,
        "stages": stages,
        "cleared": _CLEARED_MARK in text,
        "last_stage": int(last.group(1)) if last else None,
        "total_stages": int(last.group(2)) if last else None,
        "boss": last.group(3) if last else None,
        "bag": bag,
        "fragments": fragments,
    }


def format_for_display(parsed):
    stages = parsed.get("stages", [])

    if parsed.get("cleared"):
        total = parsed.get("total_stages") or len(stages)
        state = f"通關 {total}/{total} 關" if total else "通關"
    elif stages:
        state = f"進行至第 {max(s['stage'] for s in stages)} 關"
    else:
        state = ""

    lines = [f"🌸⚔️ 連續進階活動塔 {state}".rstrip()]

    fragments = parsed.get("fragments", [])
    detail = " ".join(f"{f['name']}×{f['count']}" for f in fragments)
    lines.append(f"碎片 {sum(f['count'] for f in fragments)}" + (f"｜{detail}" if detail else ""))

    return "\n".join(lines)
