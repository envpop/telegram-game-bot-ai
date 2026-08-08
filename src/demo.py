from telegram_parser import MessageRouter

if __name__ == "__main__":
    router = MessageRouter()

    tests = [
        {
            "event_type": "new", "chat_id": -1004431989174, "chat_name": "摸摸熊戰鬥陀螺",
            "sender_id": -1004431989174, "message_id": 1059,
            "text": "🌗💥「深海級・無面」的形體崩解重組——進入【終相】(相位 3/3)!",
            "buttons": [],
        },
        {
            "event_type": "new", "chat_id": 8707720905, "chat_name": "摸熊神社",
            "sender_id": 6443180435, "message_id": 829, "text": "背包", "buttons": [],
        },
        {
            "event_type": "new", "chat_id": 8707720905, "chat_name": "摸熊神社",
            "sender_id": 8707720905, "message_id": 830,
            "text": "🐚 新系統｜奇聞軼事錄...", "buttons": [],
        },
        {
            "event_type": "new", "chat_id": 8707720905, "chat_name": "摸熊神社",
            "sender_id": 8707720905, "message_id": 831,
            "text": "🎒 你的背包\n...", "buttons": [],
        },
        {
            "event_type": "edited", "chat_id": 8707720905, "chat_name": "摸熊神社",
            "sender_id": 8707720905, "message_id": 831,
            "text": "🎒 你的背包(已更新)\n...", "buttons": [],
        },
        {
            "event_type": "new", "chat_id": 8707720905, "chat_name": "摸熊神社",
            "sender_id": 6443180435, "message_id": 832, "text": "背胞", "buttons": [],
        },
        {
            # 文字為空但帶圖片的劇情訊息，不應被歸類成 empty
            "event_type": "new", "chat_id": 8707720905, "chat_name": "摸熊神社",
            "sender_id": 8707720905, "message_id": 824, "text": "",
            "buttons": [{"row": 1, "column": 1, "text": "▶ 繼續"}],
            "media": {"type": "MessageMediaPhoto", "has_photo": True},
        },
    ]

    for record in tests:
        result = router.parse(record)
        print("=" * 60)
        print(record.get("text", "")[:30])
        print(result)
