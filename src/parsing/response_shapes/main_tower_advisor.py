"""
主塔查詢 + 出戰建議（單體出戰體系）

=== 假設與待確認事項（ASSUMPTIONS） ===
本模組沒有拿到 tops.json 的實際 schema，roster 的欄位名稱是暫定的，
串接進正式專案前請對照 tops.json 實際欄位改 KEY 名稱：

    {
        "id": "T0001",          # 穩定 ID
        "name": "崩嶽神熊・摸摸撼地GO",
        "element": "土",         # 木/火/土/金/水
        "type": "攻擊型",        # 攻擊型/防禦型/持久型/平衡型
        "power": 611,
        "bound": None            # 靈魂綁定方向："爆擊" / "護盾" / "回歸" / None
    }

輪迴樓層映射：boss_catalog_main_tower.json 原本的 loop_mapping_note
只是猜測（假設樓層數字會累加到 101 以上，再用 mod 換算回 51~100）。
2026-08-14 拿到實際訊息樣本（第 2 輪迴・第 100 階）後確認：遊戲畫面
把「輪迴數」跟「樓層數」分開顯示，樓層永遠是 1~100 內的數字，不會
累加。get_boss_for_floor() 因此已經改成直接用訊息裡的「第 X 階」查表，
不再做 mod 換算；loop 數字目前只作為顯示/紀錄用途，不影響查表結果。
"""

import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
BOSS_CATALOG_PATH = BASE_DIR / "boss_catalog_main_tower.json"
RULES_PATH = BASE_DIR / "element_type_rules.json"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_boss_for_floor(floor: int, catalog: dict) -> dict:
    """
    依樓層數取得王資料。遊戲畫面的「輪迴數」跟「樓層數」是分開顯示的欄位，
    樓層本身永遠是 1~100 內的數字（已用實際訊息樣本確認，見本檔頭部說明），
    直接查表即可，不需要任何換算。
    """
    bosses = catalog["bosses"]
    boss = bosses.get(str(floor))
    if boss is None:
        raise ValueError(f"樓層 {floor} 查無資料")
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


def advise_for_floor(floor: int, roster: list[dict], top_n: int = 5) -> dict:
    """對外主要入口：給樓層數 + roster，回傳王資料 + 建議清單"""
    catalog = load_json(BOSS_CATALOG_PATH)
    rules = load_json(RULES_PATH)
    boss = get_boss_for_floor(floor, catalog)
    recommendations = recommend_tops(boss, roster, rules, top_n=top_n)
    return {
        "floor": floor,
        "boss": boss,
        "recommendations": recommendations,
    }


if __name__ == "__main__":
    # 範例用法（roster 為假資料，實際串接時換成從 tops.json 讀取）
    example_roster = [
        {"id": "T0001", "name": "崩嶽神熊・摸摸撼地GO", "element": "土", "type": "攻擊型", "power": 611, "bound": None},
        {"id": "T0002", "name": "極・天熊・滅卻牙", "element": "火", "type": "攻擊型", "power": 580, "bound": "爆擊"},
        {"id": "T0003", "name": "窈冥淵渟・玄冥神熊・摸摸寒淵GO", "element": "水", "type": "防禦型", "power": 640, "bound": "護盾"},
    ]

    result = advise_for_floor(84, example_roster)
    print(f"第 {result['floor']} 階王：{result['boss']['name']}"
          f"（{result['boss']['type']}・{result['boss']['element']}屬性）")
    print("建議出戰：")
    for r in result["recommendations"]:
        print(f"  {r['name']}（{r['element']}・{r['type']}・戰力{r['power']}）"
              f" 分數{r['_score']} {r['_reasons']}")
