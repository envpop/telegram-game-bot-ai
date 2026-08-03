import json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ITEM_CONFIG_FILE = BASE_DIR / "config" / "item_config.json"
RECORD_DIR = BASE_DIR / "data" / "records"

def load_item_config():
    with ITEM_CONFIG_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("items", [])

def save_record(record):
    day = datetime.now().strftime("%Y-%m-%d")
    out_dir = RECORD_DIR / day
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "market_records.jsonl"
    with out_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

def match_item_from_text(text, items):
    for item in items:
        if not item.get("enabled", True):
            continue
        command = item.get("command")
        if command and command in text:
            return item
        aliases = item.get("aliases") or []
        for alias in aliases:
            if alias and alias in text:
                return item
    return None

def parse_chart_image(image_path, text_hint=""):
    items = load_item_config()
    item = match_item_from_text(text_hint, items)

    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "text_hint": text_hint
        },
        "item": {
            "key": item["key"] if item else None,
            "name": item["name"] if item else None,
            "command": item["command"] if item else None
        },
        "chart": {
            "period_hours": 6,
            "current_price": None,
            "day_change_pct": None,
            "instant_change_pct": None
        },
        "extremes": {
            "high": {
                "price": None,
                "time_offset_minutes": None
            },
            "low": {
                "price": None,
                "time_offset_minutes": None
            }
        },
        "trend": {
            "direction": None,
            "strength": None
        },
        "confidence": 0.0,
        "image_path": str(image_path)
    }

def main():
    print("chart_worker ready")

if __name__ == "__main__":
    main()