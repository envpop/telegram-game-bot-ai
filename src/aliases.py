"""
aliases.py
管理可自訂的指令別名（巨集）。每個 alias 對應一組固定順序的指令樣板，
可用 {1} {2} ... 代入呼叫時給的參數，例如「備戰」對應「切換出戰陀螺 {1}」
「切換副手陀螺 {2}」，呼叫時用 /sched alias=備戰 T0001 T0002 帶入實際的陀螺 ID。

兩種設定格式都支援：
- 純清單（原本的格式，向下相容）：
    "備戰": ["切換出戰陀螺 {1}", "切換副手陀螺 {2}"]
  展開後全部歸類到「可重複」那組，外層 /sched rep=N 會重複整組。
- 物件格式，把「只做一次的設定」跟「要重複的動作」分開：
    "抽卡設定後連抽": {
      "once": ["切換出戰陀螺 {1}"],
      "repeat": ["click:再抽一次"]
    }
  搭配 /sched rep=5 alias=抽卡設定後連抽 T0001，設定只做一次，
  按鈕點擊重複 5 次，設定不會被重複到。

設定檔 config/aliases.json 是熱重載的：每次呼叫都重新讀檔，
改完檔案不用重開程式就會生效。
"""

import json
import os
import re
from typing import List, Tuple

_ALIASES_PATH = os.path.join("config", "aliases.json")
_PLACEHOLDER_RE = re.compile(r"\{(\d+)\}")


class AliasError(ValueError):
    """alias 找不到、格式錯誤、參數數量不對時丟出。"""
    pass


def _load_raw() -> dict:
    if not os.path.exists(_ALIASES_PATH):
        return {}
    with open(_ALIASES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def list_aliases() -> List[str]:
    """回傳目前設定檔裡所有 alias 名稱（排序過），供 /sched aliases 顯示用。"""
    return sorted(_load_raw().keys())


def resolve_alias(name: str, args: List[str]) -> Tuple[List[str], List[str]]:
    """
    展開 alias，回傳 (once_steps, repeat_steps) 兩組已代入參數的指令清單。
    once_steps 只會執行一次；repeat_steps 會被外層的 rep= 重複。
    參數不足會報錯（避免代到一半留下 "{2}" 這種殘缺指令送出去）。
    """
    data = _load_raw()
    if name not in data:
        available = "、".join(list_aliases()) or "（目前沒有任何 alias，請先在 config/aliases.json 設定）"
        raise AliasError(f"找不到 alias「{name}」。目前可用：{available}")

    template = data[name]

    if isinstance(template, list):
        once_template: List[str] = []
        repeat_template: List[str] = template
    elif isinstance(template, dict):
        once_template = template.get("once", [])
        repeat_template = template.get("repeat", [])
        if not isinstance(once_template, list) or not isinstance(repeat_template, list):
            raise AliasError(f"alias「{name}」的 once/repeat 都必須是指令字串清單。")
    else:
        raise AliasError(f"alias「{name}」的設定格式不對，應該是指令清單，或包含 once/repeat 的物件。")

    if not once_template and not repeat_template:
        raise AliasError(f"alias「{name}」的內容是空的，沒有任何指令。")

    all_lines = once_template + repeat_template
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

    once_steps = [_substitute(line) for line in once_template]
    repeat_steps = [_substitute(line) for line in repeat_template]
    return once_steps, repeat_steps