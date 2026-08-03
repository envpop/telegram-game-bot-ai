"""
credentials.py —— 帳號憑證管理

優先順序（刻意設計成這樣，保留手動介入的餘地）：
  1. .env 裡如果有對應的值（手動填寫）→ 直接用，完全不去問 Bitwarden
  2. .env 沒填 → 嘗試從 Bitwarden CLI 撈
  3. 兩邊都拿不到 → 丟出清楚的錯誤訊息，不會靜默失敗、也不會用空值繼續跑

這樣設計的理由：你隨時可以在 .env 手動填一組值蓋過 Bitwarden（例如 Bitwarden CLI
沒裝、忘記解鎖、或臨時想測試用別組憑證），不會被綁死在單一憑證來源上。

需要 Bitwarden CLI（`bw` 指令）已安裝並解鎖（`bw unlock` 後設定好 BW_SESSION
環境變數）才能使用 Bitwarden 這條路徑；沒裝或沒解鎖也沒關係，會自動略過，
只要 .env 有填就不受影響。
"""

import os
import json
import subprocess


def _try_bitwarden(item_name):
    """嘗試從 Bitwarden CLI 取得指定 item 的資料。任何失敗都回傳 None，
    不拋例外中斷程式（Bitwarden 只是其中一個選項，不該讓整個程式因此掛掉）。
    """
    if not item_name:
        return None

    try:
        result = subprocess.run(
            ["bw", "get", "item", item_name],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def get_account_credentials(account_config):
    """取得單一帳號的 API_ID / API_HASH。

    account_config 是 accounts.json 裡單一帳號的設定，例如：
      {
        "label": "測試帳號",
        "bitwarden_item": "telegram-test-account",
        "env_prefix": "TEST"
      }
    對應 .env 裡會找 TEST_API_ID / TEST_API_HASH 這兩個欄位。

    回傳 {"api_id": int, "api_hash": str, "source": "env" | "bitwarden"}
    """
    label = account_config.get("label", "?")
    env_prefix = account_config.get("env_prefix", "")
    api_id_key = f"{env_prefix}_API_ID" if env_prefix else "TELEGRAM_API_ID"
    api_hash_key = f"{env_prefix}_API_HASH" if env_prefix else "TELEGRAM_API_HASH"

    env_api_id = os.getenv(api_id_key)
    env_api_hash = os.getenv(api_hash_key)

    if env_api_id and env_api_hash:
        return {"api_id": int(env_api_id), "api_hash": env_api_hash, "source": "env"}

    bitwarden_item = account_config.get("bitwarden_item")
    item = _try_bitwarden(bitwarden_item)
    if item:
        fields = {f["name"]: f["value"] for f in item.get("fields", []) if f.get("name")}
        if "api_id" in fields and "api_hash" in fields:
            return {
                "api_id": int(fields["api_id"]),
                "api_hash": fields["api_hash"],
                "source": "bitwarden",
            }

    raise RuntimeError(
        f"找不到帳號「{label}」的憑證。\n"
        f"請擇一設定：\n"
        f"  1. 在 .env 加入 {api_id_key}=... 與 {api_hash_key}=...\n"
        f"  2. 設定好 Bitwarden item「{bitwarden_item}」（需含 api_id / api_hash 兩個自訂欄位），"
        f"並確認 Bitwarden CLI 已解鎖"
    )
