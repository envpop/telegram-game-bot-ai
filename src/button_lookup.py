"""
button_lookup.py

從 monitor.py 寫出的 raw log（logs/{日期}/telegram_raw.jsonl）裡，
查詢「按鈕文字」對應的 chat_id / message_id / data，
讓 /sched click:文字 這種寫法可以自動轉換成
executor.click_button(chat_id, message_id, data, ...) 需要的參數。

只負責讀 log 檔案，不 import monitor 也不 import executor，
維持 monitor / executor / 本模組三邊互不依賴。
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict

from telegram_client import BASE_DIR

LOCAL_TZ = timezone(timedelta(hours=8))
LOG_DIR = BASE_DIR / "logs"
RAW_LOG_FILENAME = "telegram_raw.jsonl"


def _today_log_path() -> Path:
    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    return LOG_DIR / today / RAW_LOG_FILENAME


def find_button(text_match: str, chat_id: Optional[int] = None,
                 log_path: Optional[Path] = None) -> Optional[Dict]:
    """
    在 raw log 裡由新到舊找，回傳第一筆「按鈕文字包含 text_match」的紀錄裡
    對應的按鈕資訊：{"chat_id", "message_id", "data", "button_text"}。
    找不到回傳 None。

    - chat_id 可指定只在特定聊天室裡找；不給就不限制來源。
    - 同一則訊息若因 event_type="edited" 被記錄多次，由後往前找，
      第一筆符合的就是最新狀態，不會誤用到舊的按鈕組合。
    - 用「包含比對」而不是完全比對，原因跟 click_button 註解裡說的一樣：
      按鈕文字常帶動態內容，完全比對容易因為文字微調就失效。
    """
    path = log_path or _today_log_path()
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        if chat_id is not None and record.get("chat_id") != chat_id:
            continue

        for button in record.get("buttons") or []:
            if text_match in (button.get("text") or ""):
                return {
                    "chat_id": record.get("chat_id"),
                    "message_id": record.get("message_id"),
                    "data": button.get("data"),
                    "button_text": button.get("text"),
                }

    return None


def find_button_by_position(chat_id: int, row: int, column: int,
                             message_id: Optional[int] = None,
                             log_path: Optional[Path] = None) -> Optional[Dict]:
    """
    依「第幾列第幾欄」找按鈕，不看文字內容。適合版面配置固定、
    但文字內容變動太大不好用關鍵字比對的情境（例如永遠點第一個選項）。

    - chat_id 必填：同一組 row/column 在不同聊天室的訊息裡意義不一樣，
      不限定聊天室會找錯訊息。
    - message_id 不給的話，找該聊天室「最新一則帶按鈕的訊息」；
      找到之後只看這一則，不會因為這則沒有該位置的按鈕就往更舊的訊息找
      （因為最新那則才代表目前畫面上實際看到的按鈕）。
    """
    path = log_path or _today_log_path()
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        if record.get("chat_id") != chat_id:
            continue
        if message_id is not None and record.get("message_id") != message_id:
            continue

        buttons = record.get("buttons") or []
        if not buttons:
            continue  # 這則沒有按鈕，繼續往更舊的找符合聊天室條件的訊息

        for button in buttons:
            if button.get("row") == row and button.get("column") == column:
                return {
                    "chat_id": record.get("chat_id"),
                    "message_id": record.get("message_id"),
                    "data": button.get("data"),
                    "button_text": button.get("text"),
                }
        return None  # 找到了目標訊息，但這個位置沒有按鈕，視為找不到，不再往更舊的找

    return None