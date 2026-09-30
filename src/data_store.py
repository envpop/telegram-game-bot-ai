"""
data_store.py —— 資料庫路徑管理

集中管理 data/ 底下的路徑規則，所有需要存檔的解析器（backpack_watcher、
inventory_parsers 等）都用這裡提供的函式取得路徑，不要各自定義一份。

  data/common/...       跟帳號無關的共通資料（道具說明、樓主資訊等遊戲本身的靜態資料）
  data/{帳號ID}/...     只屬於這個帳號的個人資料（持有數量、陀螺清單、衛星清單、進度、可個人化設定）

=== 2026-09 新增 find_base_dir() ===
專案裡原本有三套獨立算 BASE_DIR 的邏輯：telegram_client.py（往上兩層，
寫死）、log_maintenance.py（往上兩層，寫死，跟 telegram_client.py 各自
維護一份一模一樣的算法）、main_tower_advisor.py（往上找 src/+data/，
唯一一個搬到子資料夾也不會壞的寫法）。往上兩層那兩套在熊接下來要做的
src/ 分類重整之後會直接算錯——這裡把 main_tower_advisor.py 那套穩健的
演算法收進來當唯一版本，需要自己算 BASE_DIR 的模組都呼叫這裡，不要
各自維護一份。

這個函式故意放在 data_store.py，不是放進 telegram_client.py 讓大家
import BASE_DIR 常數——import telegram_client.py 會觸發讀 .env、解析
帳號設定、連線憑證等副作用，像 main_tower_advisor.py 這種有獨立
__main__ 測試區塊的檔案，不該為了拿一個路徑就被迫載入這些。
data_store.py 本身沒有任何副作用，適合當這個共用邏輯的家。

已經能從 telegram_client 拿到 BASE_DIR 的模組（main.py／monitor.py／
button_lookup.py）不用改，那條路徑本身沒問題，只是它算 BASE_DIR 的
演算法要跟著這裡的修法更新（見 telegram_client.py 的對應修改）。
"""

from pathlib import Path


def find_base_dir(start: Path) -> Path:
    """往上找同時有 src/ 跟 data/ 的資料夾，回傳專案根目錄。
    不寫死「往上幾層」，模組被搬到子資料夾也不會算錯。
    找不到就退回 start 自己（方便單獨測試時用當前目錄）。
    """
    cur = Path(start).resolve()
    for _ in range(6):
        if (cur / "src").is_dir() and (cur / "data").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return Path(start).resolve()


def common_dir(base_dir):
    d = Path(base_dir) / "data" / "common"
    d.mkdir(parents=True, exist_ok=True)
    return d


def account_dir(base_dir, account_id):
    d = Path(base_dir) / "data" / str(account_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_dir(base_dir):
    """config/ 底下放的是靜態設定檔（accounts.json、aliases.json、
    reaction_rules.json 等），通常是預先存在、檢查進版控的檔案，不是
    程式執行期間才產生的——這裡一樣建立資料夾（跟其他 *_dir() 函式行為
    一致，冪等操作，資料夾已存在時無副作用），但不代表這個資料夾預期
    是空的。"""
    d = Path(base_dir) / "config"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir(base_dir):
    """logs/ 根目錄——telegram_raw.jsonl、debug_recent.jsonl、壓縮檔、
    media/ 子資料夾都在這底下，依日期再分子資料夾（見呼叫端 monitor.py
    的 get_day_dir()）。"""
    d = Path(base_dir) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d