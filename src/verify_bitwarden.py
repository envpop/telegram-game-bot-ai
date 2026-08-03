"""
verify_bitwarden.py —— 在刪除 .env 明碼之前，先確認 Bitwarden 這條路真的能用

用法：
  1. 先 bw unlock --raw 設定好 $env:BW_SESSION
  2. 執行這支腳本前，先把 .env 裡對應帳號的 API_ID/API_HASH 註解掉或暫時改名
     （這樣才能確保測到的是「真的從 Bitwarden 拿到」，不是不小心還在吃 .env 的值）
  3. python verify_bitwarden.py
"""

import json
from pathlib import Path

import credentials

BASE_DIR = Path(__file__).resolve().parent.parent
ACCOUNTS_CONFIG_FILE = BASE_DIR / "config" / "accounts.json"


def main():
    with open(ACCOUNTS_CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    for key, account_config in config["accounts"].items():
        label = account_config.get("label", key)
        print(f"--- 測試帳號：{label} ({key}) ---")
        try:
            result = credentials.get_account_credentials(account_config)
            source = result["source"]
            masked_hash = result["api_hash"][:4] + "..." + result["api_hash"][-4:]
            print(f"  成功！來源：{source}")
            print(f"  api_id={result['api_id']}, api_hash={masked_hash}")
            if source == "env":
                print("  ⚠️ 注意：這次是從 .env 拿到的，不是 Bitwarden。"
                      "如果你是想驗證 Bitwarden，請先把 .env 裡對應的值註解掉再測一次。")
        except RuntimeError as e:
            print(f"  ❌ 失敗：{e}")
        print()


if __name__ == "__main__":
    main()
