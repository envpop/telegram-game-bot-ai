"""
主塔查詢 + 出戰建議（單體出戰體系）

roster 直接吃 tops.json 的 "detailed" 陣列，欄位對齊真實資料：
    name / element / type / power / enhancement / bind_type / status

輪迴樓層映射方式是假設（見 boss_catalog_main_tower.json 的
loop_mapping_note），實際遊戲畫面顯示方式需熊之後確認回報，
必要時調整 get_boss_for_floor() 的映射公式。
"""

import json
from pathlib import Path

def _find_base_dir(start: Path) -> Path:
    """往上找同時有 src/ 跟 data/ 的資料夾，對齊專案既有原則
    （不寫死 .parent.parent，模組被移動位置也不會壞）。
    找不到就退回 start 自己（方便單獨測試時用當前目錄）。"""
    cur = start.resolve()
    for _ in range(6):
        if (cur / "src").is_dir() and (cur / "data").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return start.resolve()

BASE_DIR = _find_base_dir(Path(__file__).parent)
BOSS_CATALOG_PATH = BASE_DIR / "data" / "common" / "boss_catalog_main_tower.json"
RULES_PATH = BASE_DIR / "data" / "common" / "element_type_rules.json"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_boss_for_floor(raw_floor: int, catalog: dict) -> dict:
    """
    依樓層數取得王資料。raw_floor <= 100 直接查表；
    超過 100（第二輪以後）依假設映射回 51~100 區間。
    """
    bosses = catalog["bosses"]
    if raw_floor <= 100:
        effective_floor = raw_floor
    else:
        # 假設：51~100 這 50 階循環，第101階起對應回51階
        effective_floor = ((raw_floor - 51) % 50) + 51

    boss = bosses.get(str(effective_floor))
    if boss is None:
        raise ValueError(f"樓層 {raw_floor}（映射後 {effective_floor}）查無資料")
    return boss


def element_that_beats(target_element: str, rules: dict) -> str | None:
    """找出剋制 target_element 的屬性（反查 element_control）"""
    if target_element is None:
        return None
    for attacker, defender in rules["element_control"].items():
        if defender == target_element:
            return attacker
    return None


def type_that_beats(target_type: str, rules: dict) -> str | None:
    """找出剋制 target_type 的類型（反查 type_control）"""
    for attacker, defender in rules["type_control"].items():
        if defender == target_type:
            return attacker
    return None  # 涵蓋 type_no_counter 情況（例如 平衡型）


def recommend_tops(boss: dict, roster: list[dict], rules: dict, top_n: int = 5) -> list[dict]:
    """
    依王的屬性/類型，從 roster 篩選並排序建議出戰陀螺。

    評分規則（優先序）：
      1. 屬性剋制王 -> +2 分
      2. 類型剋制王 -> +1 分
      （王屬性為 None 時，只看類型剋制；1~30 階王會是這種情況）
      3. 同分時依 power 由高到低排序
    """
    beating_element = element_that_beats(boss.get("element"), rules)
    beating_type = type_that_beats(boss.get("type"), rules)

    scored = []
    for top in roster:
        score = 0
        reasons = []
        if beating_element and top.get("element") == beating_element:
            score += 2
            reasons.append(f"屬性剋制（{beating_element}剋{boss['element']}）")
        if beating_type and top.get("type") == beating_type:
            score += 1
            reasons.append(f"類型剋制（{beating_type}剋{boss['type']}）")
        scored.append({**top, "_score": score, "_reasons": reasons})

    scored.sort(key=lambda t: (t["_score"], t.get("power", 0)), reverse=True)
    return scored[:top_n]


def advise_for_floor(raw_floor: int, roster: list[dict], top_n: int = 5) -> dict:
    """對外主要入口：給樓層數 + roster，回傳王資料 + 建議清單"""
    catalog = load_json(BOSS_CATALOG_PATH)
    rules = load_json(RULES_PATH)
    boss = get_boss_for_floor(raw_floor, catalog)
    recommendations = recommend_tops(boss, roster, rules, top_n=top_n)
    return {
        "floor": raw_floor,
        "boss": boss,
        "recommendations": recommendations,
    }


if __name__ == "__main__":
    with open("/mnt/user-data/uploads/tops.json", encoding="utf-8") as f:
        roster = json.load(f)["detailed"]

    result = advise_for_floor(100, roster)
    print(f"第 {result['floor']} 階王：{result['boss']['name']}"
          f"（{result['boss']['type']}・{result['boss']['element']}屬性）")
    print("建議出戰：")
    for r in result["recommendations"]:
        print(f"  {r['name']}（{r['element']}・{r['type']}・戰力{r['power']}・+{r.get('enhancement')}）"
              f" 分數{r['_score']} {r['_reasons']}")