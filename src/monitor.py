import os
import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events


# ============================================================
# 基本設定
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"

load_dotenv(BASE_DIR / ".env")

API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")

# 目前測試帳號
SESSION_NAME = "accounts/test/session"

# 要監看的 Chat
MONITORED_CHATS = {
    8707720905: "摸熊神社",
    -1004431989174: "摸摸熊戰鬥陀螺",
}


# ============================================================
# Telegram Client
# ============================================================

client = TelegramClient(
    SESSION_NAME,
    API_ID,
    API_HASH,
)


# ============================================================
# 日誌
# ============================================================

def get_log_file():
    """
    依日期建立 JSONL 原始紀錄檔。

    每一筆 Telegram 事件一行。
    不做內容分析。
    """

    today = datetime.now().strftime("%Y-%m-%d")

    day_dir = LOG_DIR / today
    day_dir.mkdir(parents=True, exist_ok=True)

    return day_dir / "telegram_raw.jsonl"


def save_raw_event(record):
    """
    保存 Telegram 原始事件。

    這裡不做遊戲內容分析、不分類、不摘要。
    """

    log_file = get_log_file()

    with log_file.open("a", encoding="utf-8") as f:

        f.write(
            json.dumps(
                record,
                ensure_ascii=False,
                default=str,
            )
            + "\n"
        )


# ============================================================
# 按鈕解析
# ============================================================

def extract_buttons(message):
    """
    保存 Telegram Inline Button 的主要資訊：

    - row
    - column
    - text
    - type
    - callback data
    - URL
    """

    buttons = []

    if not message.buttons:
        return buttons

    for row_index, row in enumerate(message.buttons):

        for column_index, button in enumerate(row):

            item = {
                "row": row_index + 1,
                "column": column_index + 1,
                "text": getattr(button, "text", None),
                "type": type(button).__name__,
            }

            # callback data
            data = getattr(button, "data", None)

            if data is not None:

                if isinstance(data, bytes):
                    item["data"] = data.decode(
                        "utf-8",
                        errors="replace",
                    )

                else:
                    item["data"] = str(data)

            # URL
            url = getattr(button, "url", None)

            if url is not None:
                item["url"] = str(url)

            buttons.append(item)

    return buttons


# ============================================================
# 媒體資訊
# ============================================================

def extract_media_info(message):
    """
    保存媒體的基本資訊。

    Monitor v1 不下載媒體、不分析圖片內容，
    但留下「這則訊息有什麼媒體」的資訊。

    之後如果分析器需要圖片，再另外處理。
    """

    media = message.media

    if media is None:
        return None

    info = {
        "type": type(media).__name__,
    }

    # Telegram photo
    if message.photo is not None:
        info["has_photo"] = True

    # Document / animation / file
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


# ============================================================
# 完整文字顯示
# ============================================================

def print_full_text(text):

    if not text:
        print("訊息：<無文字>")
        return

    print("訊息：")

    for line in text.splitlines():
        print(f"  {line}")


# ============================================================
# 完整按鈕顯示
# ============================================================

def print_buttons(buttons):

    if not buttons:
        print("按鈕：無")
        return

    print(f"按鈕：{len(buttons)} 個")

    for button in buttons:

        text = button.get("text", "")

        extra = []

        if "data" in button:
            extra.append(
                f"data={button['data']}"
            )

        if "url" in button:
            extra.append(
                f"url={button['url']}"
            )

        if extra:
            suffix = " | " + " | ".join(extra)
        else:
            suffix = ""

        print(
            f"  [{button['row']},{button['column']}] "
            f"{text}{suffix}"
        )


# ============================================================
# 媒體顯示
# ============================================================

def print_media(media_info):

    if media_info is None:
        return

    print("媒體：")

    for key, value in media_info.items():
        print(f"  {key}: {value}")


# ============================================================
# 共用訊息處理
# ============================================================

async def process_message(message, event_type):

    chat_id = message.chat_id

    if chat_id not in MONITORED_CHATS:
        return

    # --------------------------------------------------------
    # 基本資料
    # --------------------------------------------------------

    sender_id = message.sender_id

    text = message.text or ""

    buttons = extract_buttons(message)

    media_info = extract_media_info(message)

    # --------------------------------------------------------
    # 完整原始資料
    # --------------------------------------------------------

    record = {
        # Monitor 收到事件的時間
        "recorded_at": datetime.now(
            timezone.utc
        ).isoformat(),

        # new / edited
        "event_type": event_type,

        # Chat
        "chat_id": chat_id,

        "chat_name": MONITORED_CHATS.get(
            chat_id
        ),

        # Sender
        "sender_id": sender_id,

        # Telegram message
        "message_id": message.id,

        # Telegram 原始訊息時間
        "message_date": (
            message.date.isoformat()
            if message.date
            else None
        ),

        # 完整文字
        "text": text,

        # 完整按鈕
        "buttons": buttons,

        # 媒體基本資訊
        "media": media_info,
    }

    save_raw_event(record)

    # --------------------------------------------------------
    # Terminal 顯示
    # --------------------------------------------------------

    print()
    print("=" * 70)

    label = {
        "new": "NEW MESSAGE",
        "edited": "MESSAGE EDITED",
    }.get(
        event_type,
        event_type.upper(),
    )

    print(label)
    print("-" * 70)

    print(
        f"{MONITORED_CHATS.get(chat_id)} "
        f"#{message.id}"
    )

    # 訊息時間
    print(
        "訊息時間："
        + (
            message.date.isoformat()
            if message.date
            else "<未知>"
        )
    )

    # Monitor 收到時間
    print(
        "記錄時間："
        + datetime.now(
            timezone.utc
        ).isoformat()
    )

    print()

    # 完整訊息
    print_full_text(text)

    print()

    # 完整按鈕
    print_buttons(buttons)

    # 媒體
    if media_info is not None:
        print()
        print_media(media_info)

    print("=" * 70)


# ============================================================
# NEW MESSAGE
# ============================================================

@client.on(
    events.NewMessage(
        chats=list(MONITORED_CHATS.keys())
    )
)
async def new_message_handler(event):

    await process_message(
        event.message,
        "new",
    )


# ============================================================
# MESSAGE EDITED
# ============================================================

@client.on(
    events.MessageEdited(
        chats=list(MONITORED_CHATS.keys())
    )
)
async def edited_message_handler(event):

    await process_message(
        event.message,
        "edited",
    )


# ============================================================
# 啟動
# ============================================================

async def main():

    print("=" * 70)
    print("Telegram Game Monitor v1")
    print("=" * 70)

    print("監看的 Chat：")

    for chat_id, name in MONITORED_CHATS.items():
        print(f"  {name} : {chat_id}")

    print()
    print(f"原始資料：{LOG_DIR}")
    print()

    print("目前模式：完整原始紀錄")
    print("不進行遊戲規則分析")
    print("不分類指令")
    print("不刪減訊息內容")
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

            client.loop.run_until_complete(
                main()
            )

        except KeyboardInterrupt:

            print(
                "\n手動停止監看，程式結束。"
            )