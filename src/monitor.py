import os
import json
import argparse
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

from telethon import events

from telegram_client import client, BASE_DIR, is_self_sent

# ============================================================
# 基本設定
# ============================================================

LOG_DIR = BASE_DIR / "logs"
MEDIA_DIR = LOG_DIR / "media"
LOCAL_TZ = timezone(timedelta(hours=8))

MONITORED_CHATS = {
    8707720905: "摸熊神社",
    -1004431989174: "摸摸熊戰鬥陀螺",
}

DOWNLOAD_TIMEOUT_SECONDS = 30

# 圖片下載開關：預設關閉。已確認遊戲內的圖表訊息(如契約行情走勢圖)
# 內容跟同組的文字訊息重複,目前沒有圖片辨識需求,關閉可省下大量硬碟空間。
# 關閉後 record 裡的 is_image / media 欄位仍會照常記錄「這裡曾經有一張圖」，
# 只是不下載實體檔案；未來如果發現例外情況(只有圖沒有文字)，把這裡打開即可。
DOWNLOAD_MEDIA_ENABLED = False

# 「最近訊息」小檔案：固定只保留最近 N 筆，方便快速 debug，
# 不用每次都打開一整天份的完整 log。跟完整 log 分開存放，永遠很小。
DEBUG_TAIL_SIZE = 300

# ============================================================
# CLI
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    return parser.parse_args()

class _DefaultArgs:
    """被其他程式 import 時的預設值，避免搶先解析 main.py 自己的命令列參數。"""
    watch = False

ARGS = _DefaultArgs()

# ============================================================
# 外部掛勾（給 main.py 這類外部程式使用）
# ============================================================
# monitor.py 獨立執行時完全不會用到這個，維持原本行為。
# 只有當外部程式（例如 main.py）把這個設成一個 async function 時，
# 每筆訊息存檔後才會額外呼叫一次，把 record 遞出去做後續處理（parser/executor）。
# monitor.py 本身不 import parser 或 executor，維持單一職責（純接收）。
ON_RECORD_CALLBACK = None

# monitor.py 獨立執行（python monitor.py）時，靠自己印出 watch/完整區塊來看畫面。
# 但被 main.py 這類外部程式 import、接上 ON_RECORD_CALLBACK 之後，顯示責任通常
# 已經交給外部程式自己的 display 邏輯（例如 display_formatter.py），這時應該把
# 這裡關掉，避免同一則訊息被印兩次。外部程式設定 ON_RECORD_CALLBACK 的同時，
# 記得把這個也設成 False：
#     monitor.ON_RECORD_CALLBACK = on_record
#     monitor.PRINT_ENABLED = False
PRINT_ENABLED = True

# ============================================================
# 時間與路徑
# ============================================================

def now_local():
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds")

def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)

def get_day_dir():
    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    day_dir = LOG_DIR / today
    ensure_dir(day_dir)
    return day_dir

def get_media_day_dir():
    day_dir = get_day_dir() / "media"
    ensure_dir(day_dir)
    return day_dir

def get_log_file():
    return get_day_dir() / "telegram_raw.jsonl"

def save_raw_event(record):
    log_file = get_log_file()
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

def get_debug_tail_file():
    return LOG_DIR / "debug_recent.jsonl"

def append_debug_tail(record):
    """維護一份固定筆數的「最近訊息」小檔案，方便快速 debug，不用開整天的大檔案。
    這裡用「讀出全部→加一筆→只留最後 N 筆→整檔重寫」的簡單做法：
    因為固定只有 DEBUG_TAIL_SIZE 筆（預設 1000），檔案本身不大，
    整檔重寫的成本在這個使用場景（Telegram 訊息，不是高頻資料流）可以忽略。
    """
    debug_file = get_debug_tail_file()
    line = json.dumps(record, ensure_ascii=False, default=str)

    lines = []
    if debug_file.exists():
        with debug_file.open("r", encoding="utf-8") as f:
            lines = f.readlines()

    lines.append(line + "\n")
    if len(lines) > DEBUG_TAIL_SIZE:
        lines = lines[-DEBUG_TAIL_SIZE:]

    with debug_file.open("w", encoding="utf-8") as f:
        f.writelines(lines)

# ============================================================
# 按鈕解析
# ============================================================

def extract_buttons(message):
    buttons = []
    if not message.buttons:
        return buttons

    for row_index, row in enumerate(message.buttons):
        for column_index, button in enumerate(row):
            # message.buttons 裡的每個 button 是 Telethon 的 custom.MessageButton
            # wrapper，type(button).__name__ 永遠是 "MessageButton"，看不出實際
            # 按鈕種類。真正的原始類型（KeyboardButtonCallback／KeyboardButtonUrl／
            # KeyboardButton 等）在 button.button 這個內層屬性裡。
            raw_button = getattr(button, "button", None)
            button_type = type(raw_button).__name__ if raw_button is not None else type(button).__name__

            item = {
                "row": row_index + 1,
                "column": column_index + 1,
                "text": getattr(button, "text", None),
                "type": button_type,
            }

            data = getattr(button, "data", None)
            if data is not None:
                if isinstance(data, bytes):
                    item["data"] = data.decode("utf-8", errors="replace")
                else:
                    item["data"] = str(data)

            url = getattr(button, "url", None)
            if url is not None:
                item["url"] = str(url)

            buttons.append(item)

    return buttons

# ============================================================
# 媒體資訊
# ============================================================

def extract_media_info(message):
    media = message.media
    if media is None:
        return None

    info = {
        "type": type(media).__name__,
    }

    if message.photo is not None:
        info["has_photo"] = True

    if message.document is not None:
        info["has_document"] = True
        document = message.document
        if getattr(document, "mime_type", None):
            info["mime_type"] = document.mime_type
        if getattr(document, "size", None):
            info["size"] = document.size
        if getattr(document, "id", None):
            info["document_id"] = document.id

    return info

def is_image_message(message):
    if message.photo is not None:
        return True
    document = message.document
    if document is None:
        return False
    mime_type = getattr(document, "mime_type", "") or ""
    return mime_type.startswith("image/")

async def download_media_if_needed(message, chat_id, message_id, event_type):
    if not DOWNLOAD_MEDIA_ENABLED:
        return None
    if not is_image_message(message):
        return None

    day_dir = get_media_day_dir()
    chat_dir = day_dir / str(chat_id)
    ensure_dir(chat_dir)

    ext = ".jpg"
    if message.document is not None:
        mime_type = getattr(message.document, "mime_type", "") or ""
        if mime_type == "image/png":
            ext = ".png"
        elif mime_type == "image/webp":
            ext = ".webp"
        elif mime_type == "image/jpeg":
            ext = ".jpg"

    file_name = f"{message_id}_{event_type}{ext}"
    file_path = chat_dir / file_name

    await client.download_media(message, file=str(file_path))
    return str(file_path)

# ============================================================
# watch 顯示
# ============================================================

def build_watch_preview(text, max_len=80):
    text = (text or "").replace("\n", " ").strip()
    if not text:
        return "<無文字>"
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."

def print_watch_line(record):
    chat_name = record.get("chat_name", "<unknown>")
    event_type = record.get("event_type", "")
    message_id = record.get("message_id", "")
    text = build_watch_preview(record.get("text", ""))
    has_media = "有圖" if record.get("image_path") else ("有媒體" if record.get("media") else "無媒體")
    has_buttons = f"{len(record.get('buttons') or [])}按鈕"
    print(f"[{event_type}] {chat_name} #{message_id} | {text} | {has_media} | {has_buttons}")

# ============================================================
# 共用訊息處理
# ============================================================

async def process_message(message, event_type):
    chat_id = message.chat_id
    if chat_id not in MONITORED_CHATS:
        return

    sender_id = message.sender_id
    text = message.text or ""
    buttons = extract_buttons(message)
    media_info = extract_media_info(message)

    image_path = None
    try:
        image_path = await asyncio.wait_for(
            download_media_if_needed(message, chat_id, message.id, event_type),
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] 圖片下載逾時（>{DOWNLOAD_TIMEOUT_SECONDS}s），略過圖片，訊息仍會記錄：chat={chat_id} msg={message.id}")
    except Exception as e:
        print(f"[WARN] 圖片下載失敗，略過圖片，訊息仍會記錄：chat={chat_id} msg={message.id} 錯誤：{e}")

    record = {
        "recorded_at": now_local(),
        "event_type": event_type,
        "chat_id": chat_id,
        "chat_name": MONITORED_CHATS.get(chat_id),
        "sender_id": sender_id,
        "message_id": message.id,
        "message_date": message.date.astimezone(LOCAL_TZ).isoformat(timespec="seconds") if message.date else None,
        "text": text,
        "buttons": buttons,
        "media": media_info,
        "image_path": image_path,
        "is_image": image_path is not None,
        "sent_by_bot": is_self_sent(chat_id, message.id),
    }

    save_raw_event(record)
    append_debug_tail(record)

    if ON_RECORD_CALLBACK is not None:
        try:
            await ON_RECORD_CALLBACK(record)
        except Exception as e:
            print(f"[WARN] 外部 callback 執行失敗（不影響 monitor 本身）：chat={chat_id} msg={message.id} 錯誤：{e}")

    if PRINT_ENABLED:
        if ARGS.watch:
            print_watch_line(record)
        else:
            print()
            print("=" * 70)
            label = {
                "new": "NEW MESSAGE",
                "edited": "MESSAGE EDITED",
            }.get(event_type, event_type.upper())

            print(label)
            print("-" * 70)
            print(f"{MONITORED_CHATS.get(chat_id)} #{message.id}")
            print("訊息時間：" + (message.date.astimezone(LOCAL_TZ).isoformat(timespec="seconds") if message.date else "<未知>"))
            print("記錄時間：" + now_local())
            print("文字：" + (text if text else "<無文字>"))
            print("按鈕：" + (f"{len(buttons)} 個" if buttons else "無"))
            print("媒體：" + (str(media_info) if media_info else "無"))
            print("圖片：" + (image_path if image_path else "無"))
            print("=" * 70)

# ============================================================
# 事件
# ============================================================

@client.on(events.NewMessage(chats=list(MONITORED_CHATS.keys())))
async def new_message_handler(event):
    try:
        await process_message(event.message, "new")
    except Exception as e:
        print(f"[ERROR] 處理新訊息失敗：chat={event.chat_id} msg={event.message.id} 錯誤：{e}")

@client.on(events.MessageEdited(chats=list(MONITORED_CHATS.keys())))
async def edited_message_handler(event):
    try:
        await process_message(event.message, "edited")
    except Exception as e:
        print(f"[ERROR] 處理編輯訊息失敗：chat={event.chat_id} msg={event.message.id} 錯誤：{e}")

# ============================================================
# 啟動
# ============================================================

async def main():
    ensure_dir(LOG_DIR)
    ensure_dir(MEDIA_DIR)

    print("Telegram Game Monitor v1")
    print("監看的 Chat：")
    for chat_id, name in MONITORED_CHATS.items():
        print(f" {name} : {chat_id}")

    print()
    print(f"原始資料：{LOG_DIR}")
    print(f"媒體資料：{MEDIA_DIR}")
    print(f"目前模式：{'watch' if ARGS.watch else 'raw'}")
    print()
    print("等待新訊息中...")
    print("按 Ctrl+C 可以停止")
    print()

    await client.start()
    await client.run_until_disconnected()

# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    with client:
        try:
            client.loop.run_until_complete(main())
        except KeyboardInterrupt:
            print("\n手動停止監看，程式結束。")