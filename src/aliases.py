"""
aliases.py
管理可自訂的指令別名（巨集）。每個 alias 對應一組固定順序的指令樣板，
可用 {1} {2} ... 代入呼叫時給的參數，例如「備戰」對應「切換出戰陀螺 {1}」
「切換副手陀螺 {2}」，呼叫時用 /sched alias 備戰 T0001 T0002 帶入實際的陀螺 ID。

設定檔 config/aliases.json 是熱重載的：每次呼叫都重新讀檔，
改完檔案不用重開程式就會生效。
"""

import json
import os
import re
from typing import List

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


def resolve_alias(name: str, args: List[str]) -> List[str]:
    """
    展開 alias 成實際要依序送出的指令清單，並代入參數。
    參數不足會報錯（避免代到一半留下 "{2}" 這種殘缺指令送出去）。
    """
    data = _load_raw()
    if name not in data:
        available = "、".join(list_aliases()) or "（目前沒有任何 alias，請先在 config/aliases.json 設定）"
        raise AliasError(f"找不到 alias「{name}」。目前可用：{available}")

    template = data[name]
    if not isinstance(template, list) or not template:
        raise AliasError(f"alias「{name}」的設定格式不對，應該是一個非空的指令字串清單。")

    needed_numbers = set()
    for line in template:
        needed_numbers.update(int(n) for n in _PLACEHOLDER_RE.findall(line))
    max_needed = max(needed_numbers) if needed_numbers else 0

    if max_needed > len(args):
        raise AliasError(
            f"alias「{name}」需要 {max_needed} 個參數（{{1}}~{{{max_needed}}}），"
            f"但只給了 {len(args)} 個：{args}"
        )

    def _substitute(line: str) -> str:
        return _PLACEHOLDER_RE.sub(lambda m: args[int(m.group(1)) - 1], line)

    return [_substitute(line) for line in template]