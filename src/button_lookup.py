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