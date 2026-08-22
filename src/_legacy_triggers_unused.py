# ⚠️ 2026-08-22 整理架構時發現：check_message() 沒有任何呼叫端，已被
# reaction_rules.py（ReactionRuleEngine）取代。改名讓路給新的 triggers/
# 套件（觸發模組家族），先保留檔案內容，確認真的沒用到後可以整支刪除，
# 連同 config/triggers.json 一起。

"""
triggers.py
自動化觸發規則：監聽到符合條件的訊息時，回報應該執行的 /sched 動作字串。

設計上刻意保持單純：
- 只負責「規則比對」跟「冷卻時間管理」，不主動監聽 Telegram、
  不直接呼叫 scheduler 或 executor。
- 由 monitor 收到新訊息的地方呼叫 check_message()，
  拿到回傳的 action 字串後，呼叫端自行交給
  scheduler.parse_sched() + scheduler.schedule() 執行。
  這樣不會讓 monitor 直接依賴 executor，維持既有的分層原則。

設定檔 config/triggers.json 可熱重載，新增/調整規則不用重開程式。
"""

import json
import os
import time
from typing import List, Dict

_TRIGGERS_PATH = os.path.join("config", "triggers.json")

# rule name -> 上次觸發的時間戳（存在記憶體，重啟就重置；冷卻時間通常很短，夠用）
_last_fired: Dict[str, float] = {}


def _load_raw() -> List[dict]:
    if not os.path.exists(_TRIGGERS_PATH):
        return []
    with open(_TRIGGERS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _cooldown_seconds(rule: dict) -> float:
    cd = rule.get("cooldown")
    if not cd:
        return 0.0
    # 復用 scheduler 裡同一套時間格式解析（5m / 30s / 1h），規則不重複。
    from scheduler import parse_duration
    try:
        return parse_duration(str(cd))
    except Exception:
        return 0.0


def _rule_matches(rule: dict, chat_name: str, text: str) -> bool:
    watch_chat = rule.get("watch_chat")
    if watch_chat and watch_chat != chat_name:
        return False

    match = rule.get("match", {})
    keyword = match.get("contains")
    if keyword and keyword not in text:
        return False
    # 未來要擴充比對方式（正則、開頭比對等），就在這裡加新的判斷分支。

    return True


def check_message(chat_name: str, text: str) -> List[str]:
    """
    傳入一則新收到的訊息（來源聊天室名稱、內容），回傳這次應該被觸發的
    action 字串清單（通常 0 或 1 個，同一則訊息理論上可能同時符合多條規則）。
    每次呼叫都重新讀檔，設定檔改了立即生效。
    """
    now = time.time()
    fired_actions = []

    for rule in _load_raw():
        if not rule.get("enabled", True):
            continue
        if not _rule_matches(rule, chat_name, text):
            continue

        name = rule.get("name", "未命名規則")
        cooldown = _cooldown_seconds(rule)
        last = _last_fired.get(name, 0.0)
        if now - last < cooldown:
            continue  # 還在冷卻時間內，這次跳過

        action = rule.get("action")
        if not action:
            continue

        _last_fired[name] = now
        fired_actions.append(action)

    return fired_actions