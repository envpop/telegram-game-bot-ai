"""
world_boss_progress.py —— 世界王「今天打過哪些王」的個人進度記錄

用王的名字（例如「海溝級・燭龍」）當 key，不用階數——階數推算容易因為
bot 中斷監控而算錯，王的名字是每則訊息（出現／變身／結束／查詢）都各自
獨立帶有的資訊，不需要依賴任何先前累積的狀態去推算。

存檔位置：data/{帳號}/world_boss_progress.json（跟帳號綁定，不是遊戲共通資料）。
換日（台北時間 00:00）後第一次讀取，會自動把「今天打過的王」清單清空重來。
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from data_store import account_dir

LOCAL_TZ = timezone(timedelta(hours=8))  # 台北時間，跟 executor.py 保持一致


def _progress_file(base_dir, account_id):
    return account_dir(base_dir, account_id) / "world_boss_progress.json"


def _today_str():
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def _load(base_dir, account_id):
    """讀取進度，順便處理跨日重置。永遠回傳一份「日期是今天」的乾淨資料。"""
    f = _progress_file(base_dir, account_id)
    if f.exists():
        try:
            with f.open(encoding="utf-8") as fp:
                data = json.load(fp)
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}

    today = _today_str()
    if data.get("date") != today:
        # 換日了（或檔案不存在/壞掉），今天的紀錄從空的開始
        data = {"date": today, "hit_king_names_today": []}

    data.setdefault("hit_king_names_today", [])
    return data


def _save(base_dir, account_id, data):
    f = _progress_file(base_dir, account_id)
    with f.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def has_hit_today(base_dir, account_id, king_name):
    data = _load(base_dir, account_id)
    return king_name in data["hit_king_names_today"]


def mark_hit(base_dir, account_id, king_name):
    """記錄「今天打過這隻王了」。重複呼叫同一個名字沒有副作用（不會重複累加）。"""
    data = _load(base_dir, account_id)
    if king_name not in data["hit_king_names_today"]:
        data["hit_king_names_today"].append(king_name)
    _save(base_dir, account_id, data)
