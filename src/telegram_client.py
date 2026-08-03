"""
telegram_client.py —— 唯一的 Telegram 連線來源

monitor.py（接收）跟 executor.py（輸出）共用同一個 client 實例。
不能各自建立自己的 TelegramClient，因為同一個 session 檔案不能被兩個連線同時打開。

支援多帳號切換：帳號清單定義在 config/accounts.json，
要用哪一個帳號由環境變數 ACTIVE_ACCOUNT 決定（沒設就用 accounts.json 裡的預設值）。
這樣切換帳號不用改設定檔，設一次環境變數、重啟程式即可。

注意：這裡做的是「啟動時決定要用哪個帳號」，不是「執行期間即時切換帳號」。
真的要在程式運行中切換帳號（斷線、換 session、重新連線），牽涉到 client 的
生命週期管理跟事件重新註冊，是更大的改動，目前先用「改設定/環境變數 → 重啟」的方式，
需要真的即時切換時再另外處理。
"""

import os
import json
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

import credentials

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

ACCOUNTS_CONFIG_FILE = BASE_DIR / "config" / "accounts.json"


def _load_accounts_config():
    with open(ACCOUNTS_CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_active_account():
    """決定要用哪個帳號連線。
    優先順序：環境變數 ACTIVE_ACCOUNT（手動覆寫，最高優先）
             > accounts.json 裡的 active_account 設定（預設值）。
    """
    config = _load_accounts_config()
    override = os.getenv("ACTIVE_ACCOUNT")
    account_key = override or config.get("active_account")

    if account_key not in config["accounts"]:
        available = ", ".join(config["accounts"].keys())
        raise RuntimeError(
            f"accounts.json 裡找不到帳號「{account_key}」，可用的帳號有：{available}"
        )

    return account_key, config["accounts"][account_key]


ACTIVE_ACCOUNT_KEY, ACTIVE_ACCOUNT_CONFIG = _resolve_active_account()
_creds = credentials.get_account_credentials(ACTIVE_ACCOUNT_CONFIG)

API_ID = _creds["api_id"]
API_HASH = _creds["api_hash"]
SESSION_NAME = ACTIVE_ACCOUNT_CONFIG["session_name"]

print(f"[telegram_client] 使用帳號「{ACTIVE_ACCOUNT_CONFIG.get('label', ACTIVE_ACCOUNT_KEY)}」"
      f"（憑證來源：{_creds['source']}，session：{SESSION_NAME}）")

client = TelegramClient(
    SESSION_NAME,
    API_ID,
    API_HASH,
)

# ============================================================
# 自送訊息登記表
# ============================================================
# executor 送出指令後,把 (chat_id, message_id) 登記在這裡。
# monitor 收到 NewMessage 事件時可以查這裡,判斷「這則是我自己剛送出去的」，
# 藉此避免同一個動作被 parser 重複解析、重複顯示。
# 放在這裡（而不是 monitor.py 或 executor.py 裡）是因為兩邊都要用，
# 但兩邊互相不 import，這裡是兩邊都已經在用的共用模組，最適合放這種共用狀態。

_self_sent_ids = set()
_SELF_SENT_MAX_SIZE = 500  # 避免長時間執行後無限增長，超過就清掉最舊的一半

def mark_as_self_sent(chat_id, message_id):
    _self_sent_ids.add((chat_id, message_id))
    if len(_self_sent_ids) > _SELF_SENT_MAX_SIZE:
        # 集合沒有順序可言，這裡簡單粗暴地清空重來即可，
        # 因為只要是「剛剛」送出的才需要比對到，太舊的本來就不會再用到。
        _self_sent_ids.clear()

def is_self_sent(chat_id, message_id):
    return (chat_id, message_id) in _self_sent_ids
