import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient


# 載入 .env
load_dotenv()

api_id = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")

if not api_id or not api_hash:
    raise RuntimeError("找不到 TELEGRAM_API_ID 或 TELEGRAM_API_HASH")

api_id = int(api_id)

# 測試帳號的 session 放在 accounts/test/
session_path = Path("accounts") / "test" / "telegram"

client = TelegramClient(str(session_path), api_id, api_hash)


async def main():
    print("正在連線到 Telegram...")

    await client.start()

    me = await client.get_me()

    print()
    print("登入成功！")
    print(f"User ID: {me.id}")
    print(f"Username: @{me.username}" if me.username else "Username: 無")
    print(f"Phone: {me.phone}" if me.phone else "Phone: 無")


with client:
    client.loop.run_until_complete(main())