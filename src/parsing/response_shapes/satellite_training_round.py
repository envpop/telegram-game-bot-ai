"""
satellite_training_round.py —— response_shapes：群星計畫（衛星培育）畫面辨識

判斷 server 訊息是不是群星計畫的培育畫面（主選單或隨機岔路事件），並把
文字轉成結構化資料。純粹回答「這是什麼」，不做任何選按鈕的決策——
決策邏輯在 triggers/satellite_training_strategy.py。

2026-09 從 triggers/satellite_training_strategy.py 搬過來：這幾個函式跟
分類邏輯原本直接寫在 Strategy 檔案裡，比 parsing/response_shapes/ 這個
慣例還早出現，一直沒有跟著遷移，這次補上。搬過來之後 Strategy 不再自己
碰原始文字，改成直接吃這裡解析好的結構化資料（見 parse() 的回傳格式）。

NEEDS_BUTTONS = True：這個 shape 的顯示內容（按鈕座標＋文字排版）需要
按鈕本身的資訊，不是只靠訊息文字就能組出來，見 response_parser.py 對
這個旗標的說明。
"""

import json
import re
from pathlib import Path

from data_store import find_base_dir, common_dir

# 2026-09：BASE_DIR 改用 data_store.find_base_dir()，不透過
# telegram_client.py——response_parser.py 啟動時會 import 所有 shape
# 模組，如果這裡跟 telegram_client 掛鉤，等於載入所有 shape 就會順便
# 觸發連線憑證設定的副作用，不必要。
BASE_DIR = find_base_dir(Path(__file__).parent)

_CATALOG_CACHE = None

NEEDS_BUTTONS = True


def _catalog_path(base_dir):
    return common_dir(base_dir) / "satellite_training_catalog.json"


def load_catalog(base_dir, force_reload=False):
    """讀取 satellite_training_catalog.json，並快取起來（JSON 是熱重載設定，
    改了檔案之後想生效，呼叫時傳 force_reload=True）。
    """
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None or force_reload:
        with _catalog_path(base_dir).open(encoding="utf-8") as f:
            _CATALOG_CACHE = json.load(f)
    return _CATALOG_CACHE


def classify_message(text, catalog):
    """判斷這則訊息屬於 main_menu 還是哪一個 random_event，都不是就回傳 None。"""
    if catalog["main_menu"]["trigger_pattern"] in text:
        return "main_menu", None

    for event in catalog["random_events"]:
        if event["trigger_pattern"] in text:
            return "random_event", event

    return None, None


def classify_session_start(text, catalog):
    """判斷「培育」指令外層是新建衛星還是續練中的衛星。

    只在收到「培育指令後 BOT 的第一則回覆」時呼叫有意義；訓練過程中每回合
    的訊息不需要再判斷這個（一旦開始，接下來都是同一個 session）。

    回傳 "new"（新建）、"continuing"（續練）或 None（判斷不出來，
    可能不是培育相關訊息）。
    """
    if catalog["main_menu"]["trigger_pattern"] not in text:
        return None

    pattern = catalog["session_start_detection"]["new_session_pattern"]
    return "new" if pattern in text else "continuing"


# 「已習得：無」或「已習得：✦旋星閃、🧿迴旋盾」這種格式，用「、」分隔技能名稱
_LEARNED_LINE_PATTERN = re.compile(r"已習得：(.+)")


def count_learned_skills(text):
    """從訊息文字裡的「已習得：」那一行，數目前已經有幾個技能（普通+金技都算）。
    抓不到那一行（代表還沒進入主選單畫面）時回傳 None，呼叫端要自己判斷怎麼處理。
    """
    match = _LEARNED_LINE_PATTERN.search(text)
    if not match:
        return None

    skills_part = match.group(1).strip()
    # 「已習得：無」代表 0 個技能
    if skills_part.startswith("無"):
        return 0

    # 用全形頓號分隔；行尾可能還接著分隔線或其他文字，這裡只取到行尾即可，
    # 因為 _LEARNED_LINE_PATTERN 已經用 .+ 吃到行尾了（不含換行）
    skills = [s for s in skills_part.split("、") if s.strip()]
    return len(skills)


# 「🌌 群星計畫　第N/M回合」這種格式，抽出目前回合數跟總回合數（catalog
# 裡的樣本都是 M=20，但抓成變數而不是寫死 20，避免之後遊戲調整回合數上限
# 時程式碼要跟著改）。抓不到（代表還沒進入主選單畫面）時回傳 None。
_ROUND_LINE_PATTERN = re.compile(r"第\s*(\d+)/(\d+)\s*回合")


def parse_round_progress(text):
    """回傳 (目前回合, 總回合數) 的 tuple，抓不到回傳 None。"""
    match = _ROUND_LINE_PATTERN.search(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


# 「🌀 領悟 19/28」這種格式，抓目前領悟值跟上限。上限不寫死（可能因衛星
# 而異），每次都從當下訊息文字讀，跟 parse_round_progress 同樣的作法。
_INSIGHT_LINE_PATTERN = re.compile(r"領悟\s*(\d+)\s*/\s*(\d+)")


def parse_insight_progress(text):
    """回傳 (目前領悟值, 領悟值上限) 的 tuple，抓不到回傳 None。"""
    match = _INSIGHT_LINE_PATTERN.search(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


# 「⚡ 體力 85/100」這種格式，跟 round/insight 同一種「數字/數字」寫法。
_STAMINA_LINE_PATTERN = re.compile(r"體力\s*(\d+)\s*/\s*(\d+)")


def parse_stamina_progress(text):
    """回傳 (目前體力, 體力上限) 的 tuple，抓不到回傳 None。"""
    match = _STAMINA_LINE_PATTERN.search(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _format_buttons_grid(buttons) -> str:
    """把按鈕依欄（column）分組、依列（row）排序，組成方便一眼看盤面的格線文字。

    跟 Telegram 原本「同一列（row）橫向排列」的顯示方式刻意相反：這裡改成
    同一欄的按鈕排成一行，熊指定的排版是把同一欄的選項對齊在同一條視覺線上
    （方便終端機閱讀），跟遊戲畫面上實際的排列方式不需要一致。
    """
    if not buttons:
        return ""

    columns = sorted({b.get("column") for b in buttons if b.get("column") is not None})
    lines = []
    for col in columns:
        col_buttons = sorted(
            (b for b in buttons if b.get("column") == col),
            key=lambda b: b.get("row") or 0,
        )
        line = " ".join(
            f"🔘 [{b.get('row')},{b.get('column')}] {b.get('text')}"
            for b in col_buttons
        )
        lines.append(line)
    return "\n".join(lines)


# ==================== response_shapes 標準介面 ====================
# 每個 response_shape 模組要提供：
#   signature(text) -> bool          這段文字是不是這個 shape
#   parse(text) -> dict              抽成結構化資料
#   format_for_display(parsed) -> str 組出給 display 用的文字
# 見 parsing/response_parser.py 的說明。

def signature(text) -> bool:
    catalog = load_catalog(BASE_DIR)
    kind, _ = classify_message(text, catalog)
    return kind is not None


def parse(text, buttons=None) -> dict:
    """buttons 只有在 NEEDS_BUTTONS=True 時，response_parser.py 才會傳進來
    （見該檔案的說明），這裡只用來組顯示用的按鈕格線，不影響決策——
    決策層（satellite_training_strategy.py）拿按鈕清單一律直接讀
    ctx.buttons（monitor 記錄的原始資料），不吃這裡的 structured["buttons"]。
    """
    catalog = load_catalog(BASE_DIR)
    kind, event = classify_message(text, catalog)

    if kind == "random_event":
        return {
            "kind": "random_event",
            "event_id": event["event_id"],
            "options": event["options"],
        }

    # kind == "main_menu"（signature() 已經保證 kind 不是 None 才會呼叫到這裡）
    return {
        "kind": "main_menu",
        "session_kind": classify_session_start(text, catalog),
        "learned_count": count_learned_skills(text) or 0,
        "round_progress": parse_round_progress(text),
        "insight_progress": parse_insight_progress(text),
        "stamina_progress": parse_stamina_progress(text),
        "buttons": buttons,
    }


def format_for_display(parsed: dict) -> str:
    if parsed["kind"] == "random_event":
        options_text = "、".join(o["text"] for o in parsed["options"])
        return f"🌌 群星計畫岔路事件（{parsed['event_id']}）：{options_text}"

    session_label = {"new": "新建", "continuing": "續練"}.get(parsed["session_kind"], "")
    round_progress = parsed["round_progress"]
    round_text = f"第{round_progress[0]}/{round_progress[1]}回合" if round_progress else "回合數未知"
    insight_progress = parsed["insight_progress"]
    insight_text = f"領悟{insight_progress[0]}/{insight_progress[1]}" if insight_progress else "領悟值未知"
    stamina_progress = parsed["stamina_progress"]
    stamina_text = f"體力{stamina_progress[0]}/{stamina_progress[1]}" if stamina_progress else "體力未知"

    summary = (f"🌌 群星計畫（{session_label}）{round_text}｜"
               f"已學{parsed['learned_count']}個技能｜{stamina_text}｜{insight_text}")

    buttons_grid = _format_buttons_grid(parsed.get("buttons"))
    if buttons_grid:
        return summary + "\n" + buttons_grid
    return summary