"""
data_store.py —— 資料庫路徑管理

集中管理 data/ 底下的路徑規則，所有需要存檔的解析器（backpack_watcher、
inventory_parsers 等）都用這裡提供的函式取得路徑，不要各自定義一份。

  data/common/...       跟帳號無關的共通資料（道具說明、樓主資訊等遊戲本身的靜態資料）
  data/{帳號ID}/...     只屬於這個帳號的個人資料（持有數量、陀螺清單、衛星清單、進度）
"""

from pathlib import Path


def common_dir(base_dir):
    d = Path(base_dir) / "data" / "common"
    d.mkdir(parents=True, exist_ok=True)
    return d


def account_dir(base_dir, account_id):
    d = Path(base_dir) / "data" / str(account_id)
    d.mkdir(parents=True, exist_ok=True)
    return d