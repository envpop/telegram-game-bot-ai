"""
satellite_training_strategy.py —— 群星計畫（衛星培育）決策層

只負責一件事：看到一則培育訊息（帶按鈕），決定要點哪一顆按鈕。
不負責送出動作（那是 executor.click_button 的事），也不負責觀察記錄
（那是 monitor.py 的事）——純粹是「策略」，方便之後想換培育目標時，
只改這個檔案，不用動 monitor／executor。

目前的策略目標（融合素材用途，跟一般「求高數值、求金技」相反）：
  - 數值越低越好，不主動選數值特訓
  - 技能恰好一個普通技能：拿到第一個技能前選旋技特訓，拿到後停止
  - 完全不碰交流類選項：羈絆 >= 80 且金技數 < 3 時會自動觸發金技，
    要避免可控的那 8 個金技，就必須完全不交流
  - 另外 4 個只能靠「黃金事件」（被動觸發）取得的金技，任何選擇都無法
    避免，不需要特別處理
  - 隨機岔路事件（神秘旋核／魔鬼特訓／修行岔路等）：選哪個都行，
    目前預設固定選第一個選項（c0），純粹為了讓流程能繼續

用法：
    from satellite_training_strategy import decide_action

    action = decide_action(record["text"], record["buttons"])
    if action:
        await click_button(chat_id, message_id, action["data"], action["button_text"], reason=action["reason"])
"""

import json
import re
from pathlib import Path

_CATALOG_CACHE = None


def _catalog_path(base_dir):
    return Path(base_dir) / "data" / "common" / "satellite_training_catalog.json"


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


def decide_action(text, buttons, base_dir):
    """核心決策函式。

    text: monitor 記錄的訊息文字
    buttons: monitor 記錄的按鈕清單（extract_buttons() 的輸出格式）
    base_dir: 專案根目錄（用來讀 catalog），通常傳 telegram_client.BASE_DIR

    回傳 {"data": ..., "button_text": ..., "reason": ...} 或 None（判斷不出來要選什麼，
    呼叫端應該記錄下來、先不要自動點，留給人工介入）。
    """
    if not buttons:
        return None

    catalog = load_catalog(base_dir)
    kind, event = classify_message(text, catalog)

    if kind == "random_event":
        # 岔路事件：目前策略是選哪個都行，固定選第一個選項，純粹讓流程繼續。
        first_option = event["options"][0]
        # 拿 button data 要去實際 buttons 清單裡找，而不是直接信任 catalog
        # （catalog 只是參考資料，實際點擊一律以 monitor 當下記錄的 data 為準，
        # 避免遊戲版本更新後 catalog 沒同步更新導致點錯）。
        matched = _find_button_by_text(buttons, first_option["text"])
        if matched:
            return {
                "data": matched["data"],
                "button_text": matched["text"],
                "reason": f"隨機岔路事件（{event['event_id']}），策略：固定選第一個選項",
            }
        return None

    if kind == "main_menu":
        learned_count = count_learned_skills(text)
        if learned_count is None:
            learned_count = 0  # 保守起見，抓不到就當作還沒有技能

        if learned_count == 0:
            target_data = "tr_skill"
            reason = "尚未取得任何技能，選旋技特訓以取得第一個普通技能"
        else:
            target_data = "rest"
            reason = f"已取得 {learned_count} 個技能，達成目標，之後一律選休息避免額外成長"

        matched = _find_button_by_data(buttons, target_data)
        if matched:
            return {
                "data": matched["data"],
                "button_text": matched["text"],
                "reason": reason,
            }
        return None

    # 兩種已知情境都不符合，代表遇到目錄裡沒收錄過的新訊息，回傳 None
    # 讓呼叫端自己決定要不要記錄下來、之後再補進 catalog。
    return None


def _find_button_by_data(buttons, action_code):
    """catalog 裡存的是乾淨的行動代碼（例如 "tr_skill"），但實際按鈕的 data
    是完整字串（例如 "sat:190739112:tr_skill"，前面帶 sender_id），所以用
    「data 是否以 :action_code 結尾」來比對，而不是整串相等。
    """
    suffix = ":" + action_code
    for b in buttons:
        data = b.get("data") or ""
        if data == action_code or data.endswith(suffix):
            return b
    return None


def _find_button_by_text(buttons, text):
    for b in buttons:
        if b.get("text") == text:
            return b
    return None
