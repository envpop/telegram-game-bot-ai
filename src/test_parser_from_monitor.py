import json
from pathlib import Path
from parser import MessageRouter

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"


def find_latest_jsonl(log_dir):
    candidates = sorted(log_dir.rglob("*.jsonl"))
    if not candidates:
        return None
    return candidates[-1]


def iter_jsonl(path):
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def main():
    router = MessageRouter()
    jsonl_path = find_latest_jsonl(LOG_DIR)
    if jsonl_path is None:
        print("No jsonl file found.")
        return

    print(f"Using: {jsonl_path}")
    for record in iter_jsonl(jsonl_path):
        result = router.parse(record)
        print("=" * 80)
        print("RAW RECORD:")
        print(record)
        print("PARSED RESULT:")
        print(result)


if __name__ == "__main__":
    main()