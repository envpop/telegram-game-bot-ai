"""
plans.py —— 預排計畫（一組 /sched 行的集合）

plan 就是一份文字檔，每一行是一條 /sched 語法，載入時每一行各自展開成一個
獨立的排程 job（獨立計時、互不干擾；要不要排隊或讓路由每一行自己寫
lane= / pri= 決定，plan 本身不強制）。

檔案位置：data/{帳號ID}/plans/{名稱}.plan   ← 每個帳號各自一份
格式：
    # 以 # 開頭的整行是註解；空行略過
    at=23:30 rep=65 int=1.8-3.2 送禮 小霞 專注符2
    /sched at=00:00 lane=npc rep=5 int=9-92 霧娘;小霞;凜     ← 開頭的 /sched 可寫可不寫
    at=00:36 lane=battle rep=5 int=11-15 挑戰

這支檔案只負責「找檔、讀檔、整理成行」，不解析 /sched 語法、不排程——
解析跟排程是 scheduler.py 的事（scheduler.py import 這裡，方向跟 aliases.py
一樣，避免兩邊互相 import）。每次呼叫都重新讀檔，改完 plan 不用重開程式。
"""

import re
from pathlib import Path
from typing import List, Tuple

from data_store import account_dir

PLAN_SUFFIX = ".plan"
# "all" 留給 /plan stop all，不能拿來當 plan 名稱。
_RESERVED_NAMES = {"all"}
# 名稱不能含空白（指令用空白分隔）跟路徑／檔名不合法字元，避免跳出 plans/ 資料夾。
_BAD_NAME_RE = re.compile(r'[\s/\\:*?"<>|]')


class PlanError(ValueError):
    """plan 名稱不合法、找不到檔案、檔案是空的時丟出，訊息可直接印給使用者。"""
    pass


def plans_dir(base_dir, account_id) -> Path:
    d = account_dir(base_dir, account_id) / "plans"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_plans(base_dir, account_id) -> List[str]:
    """這個帳號目前有哪些 plan（檔名去掉 .plan，排序過）。"""
    return sorted(p.stem for p in plans_dir(base_dir, account_id).glob("*" + PLAN_SUFFIX))


def normalize_name(name: str) -> str:
    name = name.strip()
    if name.endswith(PLAN_SUFFIX):
        name = name[: -len(PLAN_SUFFIX)]
    if (not name or name.startswith(".") or _BAD_NAME_RE.search(name)
            or name.lower() in _RESERVED_NAMES):
        raise PlanError(f"plan 名稱「{name}」不合法（不能空白、不能含空白或 / \\ : * ? \" < > |，也不能是 all）")
    return name


def read_plan(base_dir, account_id, name: str) -> List[Tuple[int, str]]:
    """讀出 plan 的所有指令行，回傳 [(行號, "/sched ..."), ...]。

    沒有以 / 開頭的行會自動補上「/sched 」；以 / 開頭的行原樣保留（如果不是
    /sched 開頭，之後 parse_sched() 會判定不是排程行並報錯，這裡不重複判斷）。
    """
    name = normalize_name(name)
    path = plans_dir(base_dir, account_id) / (name + PLAN_SUFFIX)
    if not path.is_file():
        available = "、".join(list_plans(base_dir, account_id)) or "（目前沒有任何 plan）"
        raise PlanError(f"找不到 plan「{name}」。目前可用：{available}")

    lines: List[Tuple[int, str]] = []
    # utf-8-sig：Windows 記事本存的檔案開頭可能有 BOM，不處理的話第一行會壞掉。
    for line_no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith("/"):
            line = "/sched " + line
        lines.append((line_no, line))

    if not lines:
        raise PlanError(f"plan「{name}」是空的（只有註解或空行）。")
    return lines
