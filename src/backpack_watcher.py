"""
backpack_watcher.py —— 背包偵測 + 未知道具自動查詢

流程：
  1. 偵測到「背包」的伺服器回應（文字開頭是 🎒）→ 解析出目前擁有的所有道具名稱
  2. 跟已知道具清單（data/{帳號}/known_items.json）比對，找出沒看過的
  3. 對每個沒看過的道具，依序送出「道具說明 {名稱}」查詢
     （送出當下就先標記成「已查詢」，避免同一輪或下一次背包更新時重複觸發）
  4. 偵測到「道具說明」的回應 → 解析出說明內容，
     寫進 data/{帳號}/item_descriptions.json
"""

import json
import re
from pathlib import Path

from data_store import common_dir, account_dir


# ============================================================
# 解析：背包
# ============================================================

_CATEGORY_LINE_PATTERN = re.compile(r"^(\S+)\s+(\S+?)｜(.+)$")
_ITEM_PATTERN = re.compile(r"(.+?)×(\d+)")
_FRAGMENT_LINE_PATTERN = re.compile(r"^(\S+)\s+(.+?)\s+(\d+)/(\d+)$")


def parse_backpack(text):
    """解析「背包」的伺服器回應，回傳所有道具（含碎片區）跟其數量。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    items = []
    fragments = []
    in_fragment_section = False

    for line in lines:
        if "碎片" in line and "──" in line:
            in_fragment_section = True
            continue

        if line.startswith("「"):
            # 底部操作提示行（例如「使用 簡稱」用道具...），不是道具資料，跳過
            continue

        if in_fragment_section:
            m = _FRAGMENT_LINE_PATTERN.match(line)
            if m:
                _, name, current, total = m.groups()
                fragments.append({
                    "name": name.strip(),
                    "current": int(current),
                    "total": int(total),
                })
            continue

        m = _CATEGORY_LINE_PATTERN.match(line)
        if not m:
            continue

        _, category, items_raw = m.groups()
        for item_str in items_raw.split("、"):
            item_m = _ITEM_PATTERN.match(item_str.strip())
            if item_m:
                name, count = item_m.groups()
                items.append({
                    "name": name.strip(),
                    "count": int(count),
                    "category": category,
                })

    return {"items": items, "fragments": fragments}


def is_backpack_message(text):
    return (text or "").strip().startswith("🎒")


# ============================================================
# 解析：道具說明
# ============================================================

_ITEM_DESC_PATTERN = re.compile(r"^(\S+)\s*(.+?)（簡稱[:：]\s*(.+?)）\n(.+)$", re.DOTALL)


def parse_item_description(text):
    """解析「道具說明 XXX」的伺服器回應。不符合格式回傳 None
    （用來判斷這則訊息是不是道具說明,而不是別的伺服器回應）。
    """
    m = _ITEM_DESC_PATTERN.match((text or "").strip())
    if not m:
        return None

    emoji, display_name, short_name, description = m.groups()
    return {
        "display_name": display_name.strip(),
        "short_name": short_name.strip(),
        "description": description.strip(),
    }


# ============================================================
# 已知道具清單 / 道具說明資料庫（依帳號隔離）
# ============================================================

def _known_items_file(base_dir):
    # 道具說明是遊戲本身的靜態資料，跟帳號無關；「查過了沒」這件事本質上也是
    # 共通的（A 帳號查過的道具，B 帳號不用重查），所以放共通資料夾。
    return common_dir(base_dir) / "known_items.json"

def _item_descriptions_file(base_dir):
    return common_dir(base_dir) / "item_descriptions.json"

def _inventory_file(base_dir, account_id):
    # 「這個帳號現在有幾個」才是真正屬於個人的資料。
    return account_dir(base_dir, account_id) / "inventory.json"


def load_known_items(base_dir):
    f = _known_items_file(base_dir)
    if not f.exists():
        return set()
    with f.open("r", encoding="utf-8") as fp:
        return set(json.load(fp))


def save_known_items(base_dir, known_items_set):
    f = _known_items_file(base_dir)
    with f.open("w", encoding="utf-8") as fp:
        json.dump(sorted(known_items_set), fp, ensure_ascii=False, indent=2)


def load_item_descriptions(base_dir):
    f = _item_descriptions_file(base_dir)
    if not f.exists():
        return {}
    with f.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def save_item_description(base_dir, name, description_data):
    descriptions = load_item_descriptions(base_dir)
    descriptions[name] = description_data
    f = _item_descriptions_file(base_dir)
    with f.open("w", encoding="utf-8") as fp:
        json.dump(descriptions, fp, ensure_ascii=False, indent=2)


def save_inventory_snapshot(base_dir, account_id, backpack_result):
    """把這次背包解析結果（各道具目前持有數量）整份覆蓋存檔。
    背包回應本身就是「當下的完整快照」，所以用覆蓋而不是累加/比對合併，
    每次都以遊戲回報的數字為準最準確。碎片區依先前確認，不需要記錄。
    """
    inventory = {}
    for item in backpack_result["items"]:
        inventory[item["name"]] = {
            "count": item["count"],
            "category": item["category"],
        }

    f = _inventory_file(base_dir, account_id)
    with f.open("w", encoding="utf-8") as fp:
        json.dump(inventory, fp, ensure_ascii=False, indent=2)

    return inventory


def find_new_items(base_dir, backpack_result):
    """比對背包解析結果跟已知清單（共通資料），回傳「沒看過」的道具名稱清單。

    碎片區（fragments）不列入比對範圍：碎片是遊戲機制自動收集、自動合成道具用的，
    不需要查詢說明，也不需要記錄數量，這裡完全忽略碎片區的內容。
    """
    known = load_known_items(base_dir)

    names_in_backpack = []
    for item in backpack_result["items"]:
        if item["name"] not in names_in_backpack:
            names_in_backpack.append(item["name"])

    new_names = [name for name in names_in_backpack if name not in known]
    return new_names


def mark_items_as_queried(base_dir, names):
    """送出查詢指令的當下就先標記成已知，避免同一輪或下一次背包更新時重複觸發。"""
    known = load_known_items(base_dir)
    known.update(names)
    save_known_items(base_dir, known)


if __name__ == "__main__":
    backpack_text = """🎒 你的背包
──────────────
🧧 福袋｜福袋×59、進階福袋×19、特級福袋×1
💎 靈魂石｜爆石×2
🍀 祝福／符｜抽祝×6、融祝×3
🧪 輔助｜捷徑×1
🎟️ 抽券｜10連×8、30連×1、50連×1
♻️ 其他｜通碎×2246、王核×191
── 碎片(滿 10 自動合成)──
🧩 進階福袋 1/10
──────────────
「使用 簡稱」用道具（例:用 抽冷）｜「道具說明 名稱」看效果｜綁定石打「綁定 名字」"""

    desc_text = """🧧 福袋（簡稱:福袋）
開啟隨機得 1 種消耗品"""

    result = parse_backpack(backpack_text)
    print("=== 背包解析結果 ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    print("\n=== 道具說明解析結果 ===")
    print(json.dumps(parse_item_description(desc_text), ensure_ascii=False, indent=2))

    print("\n=== 模擬「全部沒看過」的情況（共通資料）===")
    import tempfile
    tmp_base = Path(tempfile.mkdtemp())
    new_items = find_new_items(tmp_base, result)
    print(f"沒看過的道具：{new_items}")

    mark_items_as_queried(tmp_base, new_items)
    save_item_description(tmp_base, "福袋", parse_item_description(desc_text))

    print("\n=== 再解析一次同樣的背包，這次「福袋」應該不會再出現在新道具清單裡 ===")
    new_items_2 = find_new_items(tmp_base, result)
    print(f"沒看過的道具（第二次）：{new_items_2}")

    print("\n=== 個人持有數量快照（inventory.json）===")
    inventory = save_inventory_snapshot(tmp_base, "test_account", result)
    print(json.dumps(inventory, ensure_ascii=False, indent=2))