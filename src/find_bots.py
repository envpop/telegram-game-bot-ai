import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient


load_dotenv()

api_id = int(os.environ["TEST_API_ID"])
api_hash = os.environ["TEST_API_HASH"]

session_path = Path("accounts") / "test" / "telegram"

client = TelegramClient(str(session_path), api_id, api_hash)


#async def main():
#    print("正在取得聊天列表...\n")
#    async for dialog in client.iter_dialogs():
#        entity = dialog.entity
#
#        if getattr(entity, "bot", False):
#            print(f"名稱：{dialog.name}")
#            print(f"Username：@{entity.username}" if entity.username else "Username：無")
#            print(f"ID：{entity.id}")
#            print("-" * 40)
#
#with client:
#    client.loop.run_until_complete(main())

async def main():
    async for dialog in client.iter_dialogs():
        name = dialog.title or (dialog.name if hasattr(dialog, "name") else "")
        print(f"名稱: {name} | ID: {dialog.id} | 類型: {type(dialog.entity).__name__}")

        # 可依名稱篩選，例如:
        # if "你要找的頻道或機器人名稱" in name:
        #     print(">>> 找到目標 ID:", dialog.id)

with client:
    client.loop.run_until_complete(main())