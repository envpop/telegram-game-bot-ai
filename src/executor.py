"""
executor.py —— 輸出層

只負責一件事：把指令送出到遊戲。跟 monitor.py（純接收）完全獨立，
兩者共用同一個 telegram_client.client 連線，但誰也不 import 誰。

功能：
  send_now(text)                          立刻送出一筆指令
  click_button(chat_id, message_id, data) 點擊一顆 inline 按鈕（效果同真人點擊）
  click_button_by_text(spec)              用按鈕文字或版面位置查詢並點擊
                                           （查詢邏輯在 button_lookup.py，這裡負責串起來）
  send_sequence(commands, interval)       依序送出多筆指令，中間間隔幾秒
  schedule_at(run_at, text)               排定在指定時間送出一次
  schedule_every(interval_seconds, text)  建立固定週期重複送出的背景任務

所有送出的動作都會記錄到 logs/{日期}/actions_sent.jsonl，方便之後稽核
「BOT 到底做過什麼」，跟 monitor 記錄「觀察到什麼」的 raw log 分開存放。

button_lookup.py 只讀 monitor 寫出的 log 檔案，不 import monitor 也不 import
executor，這裡 import button_lookup 不會造成循環依賴。
"""

import asyncio
import json
import re
from datetime import datetime, timezone, timedelta

from telethon.tl.functions.messages import GetBotCallbackAnswerRequest

from telegram_client import client, BASE_DIR, mark_as_self_sent
import button_lookup

LOG_DIR = BASE_DIR / "logs"
LOCAL_TZ = timezone(timedelta(hours=8))

# 跟 monitor.py 裡的 MONITORED_CHATS 保持一致，這裡獨立定義一份，
# 避免 executor 為了一個對照表就得 import monitor（維持兩邊互不依賴）。
CHAT_NAMES = {
    8707720905: "摸熊神社",
    -1004431989174: "摸摸熊戰鬥陀螺",
}

DEFAULT_COMMAND_CHAT_ID = 8707720905

# click_button_by_text 用：spec 寫成 "row=1,col=2" 時，依版面位置查詢而不是文字。
_CLICK_POSITION_RE = re.compile(r"^row=(\d+)\s*,\s*col=(\d+)$", re.IGNORECASE)


def _now_local():
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds")


def _get_day_dir():
    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    day_dir = LOG_DIR / today
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir


def _get_actions_log_file():
    return _get_day_dir() / "actions_sent.jsonl"


def _log_action(record):
    log_file = _get_actions_log_file()
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


async def send_now(text, chat_id=None, reason=None):
    """立刻送出一筆指令，並記錄下來。"""
    target_chat_id = chat_id or DEFAULT_COMMAND_CHAT_ID

    sent_message = await client.send_message(target_chat_id, text)
    mark_as_self_sent(target_chat_id, sent_message.id)

    record = {
        "sent_at": _now_local(),
        "chat_id": target_chat_id,
        "chat_name": CHAT_NAMES.get(target_chat_id, str(target_chat_id)),
        "text": text,
        "reason": reason,
    }
    _log_action(record)

    print(f"[SENT] → {record['chat_name']}：{text}" + (f"（原因：{reason}）" if reason else ""))
    return record


async def click_button(chat_id, message_id, data, button_text=None, reason=None):
    """點擊一顆 inline 按鈕，效果跟真人手動點擊完全相同（同一個 API 呼叫）。

    chat_id / message_id：按鈕所在的訊息位置（從 monitor.py 記錄的 raw log 取得）。
    data：monitor.py 記錄的按鈕 data 字串（例如 "sat:190739112:tr_atk"）。
    button_text：按鈕上顯示的文字（例如 "🗡️ 攻擊特訓"），純粹讓 log／print 可讀，
                 不影響實際點擊行為。

    需要「用文字或位置找按鈕」時用 click_button_by_text，不要自己拼 chat_id/
    message_id/data，那些細節交給 button_lookup 處理。
    """
    data_bytes = data.encode("utf-8")

    result = await client(GetBotCallbackAnswerRequest(
        peer=chat_id,
        msg_id=message_id,
        data=data_bytes,
    ))
    toast = getattr(result, "message", None)  # 遊戲點擊後彈出的提示文字（若有）

    record = {
        "sent_at": _now_local(),
        "chat_id": chat_id,
        "chat_name": CHAT_NAMES.get(chat_id, str(chat_id)),
        "action": "click_button",
        "message_id": message_id,
        "button_text": button_text,
        "data": data,
        "response_toast": toast,
        "reason": reason,
    }
    _log_action(record)

    display = button_text or data
    extra = f"（回應：{toast}）" if toast else ""
    print(f"[CLICK] → {record['chat_name']}：{display}{extra}" + (f"（原因：{reason}）" if reason else ""))
    return record


async def click_button_by_text(spec, chat_id=None, reason=None):
    """用按鈕文字或版面位置找到按鈕並點擊，供 /sched click:xxx、/click xxx 使用。

    spec 可以是：
      - 一般文字：模糊比對按鈕文字（例如 "強攻"）
      - "row=1,col=2"：依版面位置比對，不管文字內容（文字太多變時比較穩定）

    沒指定 chat_id 時 fallback 用 DEFAULT_COMMAND_CHAT_ID，跟 send_now 行為一致。
    找不到符合的按鈕會丟 ValueError，呼叫端可以直接把訊息印出來或往上拋。
    """
    target_chat_id = chat_id or DEFAULT_COMMAND_CHAT_ID

    pos_match = _CLICK_POSITION_RE.match(spec.strip())
    if pos_match:
        row, column = int(pos_match.group(1)), int(pos_match.group(2))
        match = button_lookup.find_button_by_position(chat_id=target_chat_id, row=row, column=column)
    else:
        match = button_lookup.find_button(spec, chat_id=target_chat_id)

    if match is None:
        raise ValueError(f"raw log 裡找不到符合「{spec}」的按鈕")

    return await click_button(
        match["chat_id"], match["message_id"], match["data"],
        button_text=match["button_text"], reason=reason,
    )


async def send_sequence(commands, interval_seconds=2, chat_id=None, reason=None):
    """依序送出多筆指令，每筆之間間隔 interval_seconds 秒。
    用在需要連續操作的情境（例如連續使用多個道具、依序兌換多個獎勵）。
    """
    results = []
    for i, text in enumerate(commands):
        result = await send_now(text, chat_id=chat_id, reason=reason)
        results.append(result)
        if i < len(commands) - 1:
            await asyncio.sleep(interval_seconds)
    return results


async def schedule_at(run_at, text, chat_id=None, reason=None):
    """排定在指定的未來時間點送出一次指令。

    run_at: datetime，若沒有 tzinfo 會視為 LOCAL_TZ（UTC+8）。
    這個函式會一直等到時間到才回傳，通常用 asyncio.create_task() 包起來，
    這樣才不會卡住其他程式邏輯。
    """
    now = datetime.now(LOCAL_TZ)
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=LOCAL_TZ)
    delay = (run_at - now).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)
    return await send_now(text, chat_id=chat_id, reason=reason)


def schedule_every(interval_seconds, text, chat_id=None, reason=None):
    """建立一個固定週期重複送出指令的背景任務（例如每天簽到、每小時查一次行情）。

    回傳 asyncio.Task，需要停止時呼叫 task.cancel()。
    第一次執行會先等滿一個 interval_seconds，才送出第一筆
    （如果需要「馬上先送一次，之後才開始等間隔」，呼叫前自己先 await send_now() 一次即可）。
    """
    async def _loop():
        while True:
            await asyncio.sleep(interval_seconds)
            await send_now(text, chat_id=chat_id, reason=reason)

    return asyncio.create_task(_loop())
