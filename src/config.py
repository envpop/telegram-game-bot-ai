"""集中管理路徑與環境相關常數。"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REGISTRY_FILE = BASE_DIR / "config" / "command_registry.json"

# 專門「廣播」遊戲事件的公告頻道，目前確認只有一個，且固定不變
ANNOUNCEMENT_CHAT_ID = -1004431989174  # 摸摸熊戰鬥陀螺

# raw 資料中實際觀察到的 event_type 值（注意是 "edited"，不是 "edit"）
EVENT_TYPE_NEW = "new"
EVENT_TYPE_EDITED = "edited"
