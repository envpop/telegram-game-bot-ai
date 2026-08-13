"""
jsonl 訊息篩選器

用法範例 (PowerShell, 在專案根目錄執行):
    python filter_logs.py --logs-dir logs --keywords 雙屬性 屬性切換 交替屬性 輪流屬性
    python filter_logs.py --logs-dir logs --keywords 雙屬性 --start-date 2026-08-01 --end-date 2026-08-13
    python filter_logs.py --logs-dir logs --keywords 護衛 --chat-name 摸摸熊戰鬥陀螺

預設會遞迴搜尋 logs/ 底下所有 *.jsonl,只要 text 欄位命中任一關鍵字(不分大小寫、
不需完全比對,子字串命中即可)就印出來,並可選擇輸出成新的 jsonl 檔方便丟給 Claude 看。
"""

import argparse
import json
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser(description="篩選 logs/ 底下 jsonl 訊息")
    p.add_argument("--logs-dir", default="logs", help="log 根目錄 (預設: logs)")
    p.add_argument("--keywords", nargs="+", required=True, help="要比對的關鍵字(命中任一即算)")
    p.add_argument("--start-date", default=None, help="起始日期 YYYY-MM-DD (含), 對應 logs/<date>/ 資料夾名稱")
    p.add_argument("--end-date", default=None, help="結束日期 YYYY-MM-DD (含)")
    p.add_argument("--chat-name", default=None, help="只篩選特定 chat_name (子字串比對)")
    p.add_argument("--chat-id", type=int, default=None, help="只篩選特定 chat_id")
    p.add_argument("--out", default=None, help="若指定,將命中的原始 json line 輸出到這個檔案(jsonl 格式)")
    return p.parse_args()


def in_date_range(folder_name: str, start: str | None, end: str | None) -> bool:
    if start and folder_name < start:
        return False
    if end and folder_name > end:
        return False
    return True


def main():
    args = parse_args()
    logs_root = Path(args.logs_dir)
    if not logs_root.exists():
        print(f"[錯誤] 找不到 logs 目錄: {logs_root.resolve()}")
        return

    keywords = args.keywords
    hits = []

    date_folders = sorted(
        [d for d in logs_root.iterdir() if d.is_dir()]
    )

    for date_folder in date_folders:
        if not in_date_range(date_folder.name, args.start_date, args.end_date):
            continue

        for jsonl_file in sorted(date_folder.glob("*.jsonl")):
            with jsonl_file.open("r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    text = record.get("text", "") or ""
                    if not any(kw in text for kw in keywords):
                        continue

                    if args.chat_name and args.chat_name not in (record.get("chat_name") or ""):
                        continue
                    if args.chat_id is not None and record.get("chat_id") != args.chat_id:
                        continue

                    hits.append((jsonl_file, line_no, record))

    print(f"共找到 {len(hits)} 則命中訊息\n")
    for jsonl_file, line_no, record in hits:
        print(f"--- {jsonl_file} : line {line_no} ---")
        print(f"chat: {record.get('chat_name')} ({record.get('chat_id')})  "
              f"date: {record.get('message_date')}  msg_id: {record.get('message_id')}")
        print(record.get("text", ""))
        print()

    if args.out:
        out_path = Path(args.out)
        with out_path.open("w", encoding="utf-8") as f:
            for _, _, record in hits:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"[已輸出] 命中訊息已寫入: {out_path.resolve()}")


if __name__ == "__main__":
    main()