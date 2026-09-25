"""
aliases.py
管理可自訂的指令別名（巨集）。每個 alias 對應一組固定順序的指令樣板，
可用 {1} {2} ... 代入呼叫時給的參數，例如「備戰」對應「切換出戰陀螺 {1}」
「切換副手陀螺 {2}」，呼叫時用 /sched alias=備戰 T0001 T0002 帶入實際的陀螺 ID。

三種設定格式都支援：
- 純清單（原本的格式，向下相容）：
    "備戰": ["切換出戰陀螺 {1}", "切換副手陀螺 {2}"]
  展開後全部歸類到單一個「可重複」區塊，外層 /sched rep=N 會重複整組。
- 舊版 once/repeat 物件格式（向下相容，等同於只有兩個區塊：once 一組、
  repeat 一組，且 once 一定在 repeat 前面）：
    "抽卡設定後連抽": {
      "once": ["切換出戰陀螺 {1}"],
      "repeat": ["click:再抽一次"]
    }
  搭配 /sched rep=5 alias=抽卡設定後連抽 T0001，設定只做一次，
  按鈕點擊重複 5 次，設定不會被重複到。
- 2026-09 新增：有序區塊列表格式，可以任意交錯多段 once/repeat
  （例如 once→repeat→once），不再侷限「只能一組 once 接一組 repeat」：
    "抽卡後確認再連抽": [
      {"once": ["切換出戰陀螺 {1}"]},
      {"repeat": ["click:再抽一次"]},
      {"once": ["click:確定完成"]}
    ]
  每個區塊物件只能有一個 key（"once" 或 "repeat"），依清單順序執行；
  "repeat" 區塊一律套用外層同一個 /sched rep=N（每個 repeat 區塊各自
  跑 N 輪，不支援每個區塊各自不同的重複次數）。

設定檔 config/aliases.json 是熱重載的：每次呼叫都重新讀檔，
改完檔案不用重開程式就會生效。
"""

import json
import os
import re
from typing import List, Tuple

_ALIASES_PATH = os.path.join("config", "aliases.json")
_PLACEHOLDER_RE = re.compile(r"\{(\d+)\}")

Segment = Tuple[str, List[str]]  # ("once" | "repeat", 已代入參數的步驟清單)


class AliasError(ValueError):
    """alias 找不到、格式錯誤、參數數量不對時丟出。"""
    pass


def _load_raw() -> dict:
    if not os.path.exists(_ALIASES_PATH):
        return {}
    with open(_ALIASES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def list_aliases() -> List[str]:
    """回傳目前設定檔裡所有 alias 名稱（排序過），供 /alias list 顯示用。"""
    return sorted(_load_raw().keys())


def _segments_from_template(name: str, template) -> List[Tuple[str, List[str]]]:
    """把 aliases.json 裡的原始設定值（三種格式之一）轉成統一的
    [(kind, steps), ...] 有序區塊清單，還沒代入參數。"""
    if isinstance(template, list):
        if not template:
            raise AliasError(f"alias「{name}」的內容是空的，沒有任何指令。")

        if all(isinstance(item, str) for item in template):
            # 舊格式：純字串清單，整組視為單一個 repeat 區塊。
            return [("repeat", template)]

        if all(isinstance(item, dict) for item in template):
            # 新格式：有序區塊清單，每個 dict 只能有一個 key。
            segments = []
            for item in template:
                if len(item) != 1:
                    raise AliasError(
                        f"alias「{name}」的區塊格式不對，每個區塊只能有一個 "
                        "once 或 repeat key。"
                    )
                kind, steps = next(iter(item.items()))
                if kind not in ("once", "repeat"):
                    raise AliasError(
                        f"alias「{name}」的區塊種類「{kind}」不認得，只能是 once 或 repeat。"
                    )
                if not isinstance(steps, list):
                    raise AliasError(f"alias「{name}」的 {kind} 必須是指令字串清單。")
                segments.append((kind, steps))
            return segments

        raise AliasError(
            f"alias「{name}」的清單格式不一致，必須全部是字串，或全部是 "
            "{once:[...]} / {repeat:[...]} 這種區塊物件，不能混用。"
        )

    if isinstance(template, dict):
        # 舊版 once/repeat 物件格式：once 固定在 repeat 前面。
        once_template = template.get("once", [])
        repeat_template = template.get("repeat", [])
        if not isinstance(once_template, list) or not isinstance(repeat_template, list):
            raise AliasError(f"alias「{name}」的 once/repeat 都必須是指令字串清單。")
        segments = []
        if once_template:
            segments.append(("once", once_template))
        if repeat_template:
            segments.append(("repeat", repeat_template))
        if not segments:
            raise AliasError(f"alias「{name}」的內容是空的，沒有任何指令。")
        return segments

    raise AliasError(
        f"alias「{name}」的設定格式不對，應該是指令清單、包含 once/repeat 的物件，"
        "或有序區塊列表。"
    )


def resolve_alias(name: str, args: List[str]) -> List[Segment]:
    """
    展開 alias，回傳依序執行的 [(kind, steps), ...] 區塊清單，kind 是
    "once" 或 "repeat"，steps 已經代入參數。呼叫端（scheduler.py）依序
    跑過每個區塊："once" 固定跑一次，"repeat" 跑外層 rep=N 指定的輪數。

    參數不足會報錯（避免代到一半留下 "{2}" 這種殘缺指令送出去）。
    """
    data = _load_raw()
    if name not in data:
        available = "、".join(list_aliases()) or "（目前沒有任何 alias，請先在 config/aliases.json 設定）"
        raise AliasError(f"找不到 alias「{name}」。目前可用：{available}")

    segments_template = _segments_from_template(name, data[name])

    all_lines = [line for _, steps in segments_template for line in steps]
    needed_numbers = set()
    for line in all_lines:
        needed_numbers.update(int(n) for n in _PLACEHOLDER_RE.findall(line))
    max_needed = max(needed_numbers) if needed_numbers else 0

    if max_needed > len(args):
        raise AliasError(
            f"alias「{name}」需要 {max_needed} 個參數（{{1}}~{{{max_needed}}}），"
            f"但只給了 {len(args)} 個：{args}"
        )

    def _substitute(line: str) -> str:
        return _PLACEHOLDER_RE.sub(lambda m: args[int(m.group(1)) - 1], line)

    return [(kind, [_substitute(line) for line in steps]) for kind, steps in segments_template]