"""
log_maintenance.py —— log 保留政策維護

政策（依你確認的設定）：
  - 完整 raw log（telegram_raw.jsonl）保留 7 天，超過後壓縮成 .jsonl.gz
  - 壓縮檔保留 90 天，超過後自動刪除
  - debug_recent.jsonl（最近 1000 筆）不受這裡影響，monitor.py 自己維護固定筆數

用法：
  獨立執行： python log_maintenance.py
  或被其他程式 import 呼叫： from log_maintenance import run_maintenance
"""

import gzip
import shutil
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"

COMPRESS_AFTER_DAYS = 7
DELETE_COMPRESSED_AFTER_DAYS = 90

RAW_LOG_FILENAME = "telegram_raw.jsonl"
COMPRESSED_LOG_FILENAME = "telegram_raw.jsonl.gz"


def _parse_day_dir_date(day_dir: Path):
    """day 資料夾命名格式是 YYYY-MM-DD（見 monitor.py 的 get_day_dir）。
    格式不符的資料夾（例如 media/ 本身如果誤判成 day_dir）一律跳過，不處理。
    """
    try:
        return datetime.strptime(day_dir.name, "%Y-%m-%d").date()
    except ValueError:
        return None


def compress_old_logs(today=None):
    """把超過 COMPRESS_AFTER_DAYS 天、還沒壓縮過的 telegram_raw.jsonl 壓縮成 .gz，
    壓縮成功後刪除原本的 .jsonl（資料沒有遺失，只是換成壓縮格式）。
    """
    today = today or datetime.now().date()
    compressed = []

    if not LOG_DIR.exists():
        return compressed

    for day_dir in LOG_DIR.iterdir():
        if not day_dir.is_dir():
            continue
        day_date = _parse_day_dir_date(day_dir)
        if day_date is None:
            continue

        age_days = (today - day_date).days
        raw_file = day_dir / RAW_LOG_FILENAME
        gz_file = day_dir / COMPRESSED_LOG_FILENAME

        if age_days >= COMPRESS_AFTER_DAYS and raw_file.exists() and not gz_file.exists():
            with raw_file.open("rb") as f_in, gzip.open(gz_file, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            raw_file.unlink()
            compressed.append(day_dir.name)

    return compressed


def delete_expired_archives(today=None):
    """把超過 DELETE_COMPRESSED_AFTER_DAYS 天的壓縮檔刪除。
    只刪 .jsonl.gz 本身，media 資料夾（如果有）不動，留給你自行決定要不要清。
    """
    today = today or datetime.now().date()
    deleted = []

    if not LOG_DIR.exists():
        return deleted

    for day_dir in LOG_DIR.iterdir():
        if not day_dir.is_dir():
            continue
        day_date = _parse_day_dir_date(day_dir)
        if day_date is None:
            continue

        age_days = (today - day_date).days
        gz_file = day_dir / COMPRESSED_LOG_FILENAME

        if age_days >= DELETE_COMPRESSED_AFTER_DAYS and gz_file.exists():
            gz_file.unlink()
            deleted.append(day_dir.name)

    return deleted


def run_maintenance():
    compressed = compress_old_logs()
    deleted = delete_expired_archives()

    if compressed:
        print(f"[log_maintenance] 已壓縮（{COMPRESS_AFTER_DAYS} 天前的 log）：{compressed}")
    if deleted:
        print(f"[log_maintenance] 已刪除過期壓縮檔（{DELETE_COMPRESSED_AFTER_DAYS} 天前）：{deleted}")
    if not compressed and not deleted:
        print("[log_maintenance] 沒有需要處理的檔案")

    return compressed, deleted


if __name__ == "__main__":
    run_maintenance()