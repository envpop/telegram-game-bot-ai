# -*- coding: utf-8 -*-
"""
response_shapes/event_tower_run.py —— 「連續活動塔」shape

三函式介面：
    signature(text) -> bool
    parse(text) -> dict
    format_for_display(parsed) -> str

只回答「這是什麼」：把訊息拆成結構化資料，不判斷要不要做什麼、
不碰 executor / scheduler、不存檔。

【parse() 輸出】
    {
      "start_stage": 1,                 # 標題「第 N 關開始」的 N，沒有則 None
      "stages": [                       # 每個「第 N 關」行
        {"stage": 1, "status": "✅",
         "rewards": [{"name": "抽卡冷卻消除劑", "count": 2, "fragment": True}, ...]},
        ...
      ],
      "cleared": True,                  # 訊息含「通關整座塔」
      "last_stage": 8, "total_stages": 8,   # 「最後一戰」區段的 第 X/Y 關，沒有則 None
      "boss": "櫻祭大將",
      "bonus_rewards": [...],           # 「雙倍獎勵掉落」那行，格式同 rewards
      "synthesized": ["抽卡祝福券", "回歸靈魂綁定石"],   # 「合成了」那行
    }

【顯示設計】
使用者只要看碎片數量、行數越少越好，所以只輸出：
    🌸 連續活動塔 通關 8/8 關
    碎片 21｜抽冷×4 塔冷×3 ...
    合成：抽祝、歸石          ← 只有真的有合成才出現
其餘（福袋、最後一戰戰況、冷卻提示）parse() 仍會保留在資料裡，只是不顯示。

【重要假設，請用實際背包數量驗證】
  「雙倍獎勵掉落」那行的內容，跟最後一關（第 8 關）那行的碎片完全相同，
  所以這裡把它視為最後一關的重複顯示，碎片總數只算各關的行，
  不再加上 bonus_rewards，避免重複計算。
  如果實際上是額外再掉一次，改 _fragment_totals() 把 bonus_rewards 加進去即可。

【名稱縮寫】
  碎片名稱用 SHORT_NAMES 對照成背包裡慣用的簡稱（抽冷、塔冷…），方便跟背包的
  碎片進度對照；沒列在表裡的就顯示原名，不會壞。
"""

import re


_TITLE = re.compile(r"連續活動塔\s*[（(]\s*第\s*(\d+)\s*關開始\s*[）)]")
_STAGE_LINE = re.compile(r"^第\s*(\d+)\s*關\s*(\S+)\s*(.*)$")
# 道具：前面可能有 emoji，名稱從第一個漢字開始，到「×數量」為止
_ITEM = re.compile(r"^(.*?)([\u4e00-\u9fff].*?)×\s*(\d+)$")
_LAST_STAGE = re.compile(r"活動塔\s*第\s*(\d+)\s*/\s*(\d+)\s*關[・·]\s*([^（(\[\s]+)")
_BONUS = re.compile(r"雙倍獎勵掉落[：:]\s*(.+)")
_SYNTH = re.compile(r"合成了[：:]\s*(.+?)\s*[！!]?\s*$")

_FRAGMENT_SUFFIX = "碎片"

# 全名（不含「碎片」）→ 簡稱。對照的是背包裡慣用的簡稱。
SHORT_NAMES = {
    "抽卡冷卻消除劑": "抽冷",
    "爬塔冷卻消除劑": "塔冷",
    "專注靈魂符": "專注符",
    "融合祝福券": "融祝",
    "抽卡祝福券": "抽祝",
    "千錘百鍊的捷徑": "捷徑",
    "回歸靈魂綁定石": "歸石",
    "護盾靈魂綁定石": "盾石",
    "櫻花綻放的圓舞曲": "櫻花",
}


def signature(text):
    return bool(_TITLE.search(text)) and any(
        _STAGE_LINE.match(line.strip()) for line in text.splitlines()
    )


def _parse_items(s):
    """「🧪抽卡冷卻消除劑碎片×2、🧊爬塔冷卻消除劑碎片×1」→ 道具清單。"""
    items = []
    for part in s.split("、"):
        m = _ITEM.match(part.strip())
        if not m:
            continue
        name = m.group(2).strip()
        is_fragment = name.endswith(_FRAGMENT_SUFFIX)
        if is_fragment:
            name = name[: -len(_FRAGMENT_SUFFIX)]
        items.append({"name": name, "count": int(m.group(3)), "fragment": is_fragment})
    return items


def parse(text):
    title = _TITLE.search(text)
    stages = []
    bonus_rewards = []
    synthesized = []

    for raw in text.splitlines():
        line = raw.strip()
        sm = _STAGE_LINE.match(line)
        if sm:
            stages.append(
                {
                    "stage": int(sm.group(1)),
                    "status": sm.group(2),
                    "rewards": _parse_items(sm.group(3)),
                }
            )
            continue
        bm = _BONUS.search(line)
        if bm:
            bonus_rewards = _parse_items(bm.group(1))
            continue
        ym = _SYNTH.search(line)
        if ym:
            synthesized = [n.strip() for n in ym.group(1).split("、") if n.strip()]

    last = _LAST_STAGE.search(text)
    return {
        "start_stage": int(title.group(1)) if title else None,
        "stages": stages,
        "cleared": "通關整座塔" in text,
        "last_stage": int(last.group(1)) if last else None,
        "total_stages": int(last.group(2)) if last else None,
        "boss": last.group(3) if last else None,
        "bonus_rewards": bonus_rewards,
        "synthesized": synthesized,
    }


# ── 顯示 ─────────────────────────────────────────────

def _short(name):
    return SHORT_NAMES.get(name, name)


def _fragment_totals(stages):
    """各關碎片加總 → {簡稱: 數量}，順序為第一次出現的順序。"""
    totals = {}
    for stage in stages:
        for r in stage["rewards"]:
            if r["fragment"]:
                key = _short(r["name"])
                totals[key] = totals.get(key, 0) + r["count"]
    return totals


def format_for_display(parsed):
    stages = parsed.get("stages", [])
    totals = _fragment_totals(stages)

    if parsed.get("cleared"):
        total = parsed.get("total_stages") or len(stages)
        state = f"通關 {total}/{total} 關" if total else "通關"
    elif stages:
        state = f"進行至第 {max(s['stage'] for s in stages)} 關"
    else:
        state = ""

    lines = [f"🌸 連續活動塔 {state}".rstrip()]

    detail = " ".join(f"{name}×{n}" for name, n in totals.items())
    lines.append(f"碎片 {sum(totals.values())}" + (f"｜{detail}" if detail else ""))

    synthesized = parsed.get("synthesized", [])
    if synthesized:
        lines.append("合成：" + "、".join(_short(n) for n in synthesized))

    return "\n".join(lines)
