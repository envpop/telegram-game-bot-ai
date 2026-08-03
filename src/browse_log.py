"""
browse_log.py —— 快速瀏覽 raw log

用途：不用篩選、不用查詢語法，單純把每一筆訊息壓成一行「文字開頭預覽」，
讓你掃過一眼就能找到要的那筆，再用 message_id 撈出完整 JSON。

用法：
  列出今天的所有訊息（預覽模式）：
    python browse_log.py

  列出指定日期：
    python browse_log.py 2026-08-03

  列出 debug_recent.jsonl（最近 1000 筆，跨天）：
    python browse_log.py --debug

  用 message_id 撈出完整 JSON（印出格式化過、好讀的版本）：
    python browse_log.py --id 1045
    python browse_log.py 2026-08-03 --id 1045   # 指定日期時查找更快
"""

import json
import sys
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"

PREVIEW_LEN = 50


def iter_jsonl(path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def preview_text(text):
    text = (text or "").replace("\n", " ").strip()
    if not text:
        return "<無文字>"
    if len(text) <= PREVIEW_LEN:
        return text
    return text[:PREVIEW_LEN] + "..."


def print_summary_line(record):
    message_id = record.get("message_id", "?")
    chat_name = record.get("chat_name", "?")
    sender_id = record.get("sender_id", "?")
    text = preview_text(record.get("text"))
    bot_tag = "[BOT送出]" if record.get("sent_by_bot") else ""
    print(f"#{message_id:<6} {chat_name:<12} sender={sender_id} {bot_tag} | {text}")


def find_by_id(records, target_id):
    for record in records:
        if str(record.get("message_id")) == str(target_id):
            return record
    return None


def resolve_log_path(date_str):
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    return LOG_DIR / date_str / "telegram_raw.jsonl"


def main():
    args = sys.argv[1:]

    target_id = None
    if "--id" in args:
        idx = args.index("--id")
        target_id = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if "--debug" in args:
        log_path = LOG_DIR / "debug_recent.jsonl"
        args = [a for a in args if a != "--debug"]
    else:
        date_str = args[0] if args else None
        log_path = resolve_log_path(date_str)

    if not log_path.exists():
        print(f"找不到檔案：{log_path}")
        return

    records = list(iter_jsonl(log_path))
    print(f"讀取：{log_path}（共 {len(records)} 筆）")
    print("-" * 70)

    if target_id is not None:
        record = find_by_id(records, target_id)
        if record is None:
            print(f"找不到 message_id={target_id}")
            return
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return

    for record in records:
        print_summary_line(record)


if __name__ == "__main__":
    main()