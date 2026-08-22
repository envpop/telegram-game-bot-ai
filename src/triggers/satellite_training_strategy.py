"""
satellite_training_strategy.py —— 群星計畫（衛星培育）決策層

只負責一件事：看到一則培育訊息（帶按鈕），決定要點哪一顆按鈕。
不負責送出動作（那是 executor.click_button 的事），也不負責觀察記錄
（那是 monitor.py 的事）——純粹是「策略」，方便之後想換培育目標時，
只改這個檔案，不用動 monitor／executor。

目前的策略目標（融合素材用途，跟一般「求高數值、求金技」相反）：
  - 數值越低越好，不主動選數值特訓
  - 技能恰好一個普通技能：拿到第一個技能前選旋技特訓，拿到後停止
  - 領悟值超過保底線（上限 - 15，體力值夠時旋技特訓每次拿 15~20 領悟值）
    時先改選休息，保留這個穩拿的機會，等真的需要時（例如最後一回合）
    再用（熊 2026-08-22 指定，之前沒有這條，是每回合都選旋技特訓直到拿到技能）
  - 最後一回合（第 N/N 回合）還沒拿到技能：強制選旋技特訓，不管其他判斷，
    避免整個 session 白做（熊 2026-08-22 指定，之前沒有這條保底規則）
  - 完全不碰交流類選項：羈絆 >= 80 且金技數 < 3 時會自動觸發金技，
    要避免可控的那 8 個金技，就必須完全不交流
  - 另外 4 個只能靠「黃金事件」（被動觸發）取得的金技，任何選擇都無法
    避免，不需要特別處理
  - 隨機岔路事件（神秘旋核／魔鬼特訓／修行岔路等）：選哪個都行，
    目前預設固定選第二個選項（熊 2026-08-22 指定，原本是第一個）

用法（新版，見檔尾 decide(ctx)）：
    from triggers.satellite_training_strategy import decide
    action = decide(ctx)  # ctx 是 triggers.context.TriggerContext

「剛打了培育指令，等待 BOT 第一則回覆」這個狀態，觸發點（使用者打「培育」
指令）跟消費點（BOT 回覆時判斷新建/續練）是兩個不同來源的事件，現在統一
透過 triggers/runtime_state.py 存取（key="awaiting_training_reply"），
不再由這支檔案自己管理狀態——搬移前這裡曾經有 mark_awaiting_reply /
consume_awaiting_reply 兩個函式，但實際上 action_dispatcher.py 一直是
自己另外用一個 dict 記這個旗標，沒有呼叫這兩個函式，等於是重複的兩套
機制、其中一套是死碼，這次整理順便拿掉，只留 runtime_state.py 這一套。
"""

import json
import re
from pathlib import Path

from triggers import actions

_CATALOG_CACHE = None

# 自動點擊開關的 system_key，跟主塔戰鬥、世界王共用同一套 auto_toggle 機制。
SYSTEM_KEY = "satellite_training"


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


# 「🌌 群星計畫　第N/M回合」這種格式，抽出目前回合數跟總回合數（catalog
# 裡的樣本都是 M=20，但抓成變數而不是寫死 20，避免之後遊戲調整回合數上限
# 時程式碼要跟著改）。抓不到（代表還沒進入主選單畫面）時回傳 None。
_ROUND_LINE_PATTERN = re.compile(r"第(\d+)/(\d+)回合")


def parse_round_progress(text):
    """回傳 (目前回合, 總回合數) 的 tuple，抓不到回傳 None。"""
    match = _ROUND_LINE_PATTERN.search(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


# 「🌀 領悟 19/28」這種格式，抓目前領悟值跟上限。上限不寫死（可能因衛星
# 而異），每次都從當下訊息文字讀，跟 parse_round_progress 同樣的作法。
_INSIGHT_LINE_PATTERN = re.compile(r"領悟\s*(\d+)\s*/\s*(\d+)")

# 體力值夠的情況下，旋技特訓每次拿 15~20 領悟值（熊 2026-08-22 確認）。
# 用「保底最小增量」而不是固定門檻數字，是因為只要目前領悟值超過
# 「上限 - 最小增量」，下一次旋技特訓不管骰到多少都保證滿值拿到技能——
# 這是數學上算出來的保底線，不是憑感覺訂的門檻，上限變動時這個判斷
# 依然成立，不用跟著調整常數。
MIN_INSIGHT_GAIN_WITH_SUFFICIENT_STAMINA = 15


def parse_insight_progress(text):
    """回傳 (目前領悟值, 領悟值上限) 的 tuple，抓不到回傳 None。"""
    match = _INSIGHT_LINE_PATTERN.search(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


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
        # 岔路事件：目前策略是選哪個都行，固定選第二個選項，純粹讓流程繼續
        # （熊 2026-08-22 指定改成第二個，原本是第一個；catalog 裡目前收錄
        # 的 5 個岔路事件都剛好只有兩個選項，這裡直接假設至少有 2 個，
        # 之後 catalog 補新事件如果只有 1 個選項，這裡會 IndexError，
        # 算是刻意保留的提醒，不用特別防禦）。
        second_option = event["options"][1]
        # 拿 button data 要去實際 buttons 清單裡找，而不是直接信任 catalog
        # （catalog 只是參考資料，實際點擊一律以 monitor 當下記錄的 data 為準，
        # 避免遊戲版本更新後 catalog 沒同步更新導致點錯）。
        matched = _find_button_by_text(buttons, second_option["text"])
        if matched:
            return {
                "data": matched["data"],
                "button_text": matched["text"],
                "reason": f"隨機岔路事件（{event['event_id']}），策略：固定選第二個選項",
            }
        return None

    if kind == "main_menu":
        learned_count = count_learned_skills(text)
        if learned_count is None:
            learned_count = 0  # 保守起見，抓不到就當作還沒有技能

        round_progress = parse_round_progress(text)
        is_last_round = round_progress is not None and round_progress[0] >= round_progress[1]

        insight_progress = parse_insight_progress(text)
        # 目前領悟值超過「上限 - 最小增量」，代表下一次旋技特訓不管骰到
        # 多少都保證滿值拿到技能——保底已經穩了，不用急著這回合就用掉。
        guaranteed_next_roll = (
            insight_progress is not None
            and insight_progress[0] > insight_progress[1] - MIN_INSIGHT_GAIN_WITH_SUFFICIENT_STAMINA
        )

        if learned_count == 0 and is_last_round:
            # 這個 session 最後一回合了還沒拿到技能——這是最後一次機會，
            # 強制選旋技特訓，不管其他判斷怎麼說（包括下面的保底判斷：
            # 就算領悟值已經過保底線，此時也該用掉而不是繼續休息），
            # 避免這次培育白做。
            target_data = "tr_skill"
            reason = (f"第 {round_progress[0]}/{round_progress[1]} 回合"
                       "（本 session 最後一回合），尚未取得技能，最後機會強制選旋技特訓")
        elif learned_count == 0 and guaranteed_next_roll:
            # 還沒拿到技能，但領悟值已經過保底線：先選休息（不增加領悟值），
            # 把這個穩拿的機會留到真的需要的時候（例如最後一回合）才用掉，
            # 不用每回合都急著選旋技特訓。
            target_data = "rest"
            reason = (f"領悟 {insight_progress[0]}/{insight_progress[1]}，已過保底門檻"
                       "（再選旋技特訓必定滿值拿到技能），先休息保留這個機會，"
                       "留到真的需要時（例如最後一回合）再用")
        elif learned_count == 0:
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


def decide(ctx):
    """action_dispatcher.py 的統一觸發清單入口，取代原本 _handle_satellite_buttons()。
    判斷順序（先看有沒有按鈕、再看開關、再看是否剛好在等培育回覆）跟原本
    完全一致，包括開關關閉時 stop=False（放行給 reaction_rules 兜底）這個
    跟主塔戰鬥不同的行為——這是原本就有的差異，不是這次整理造成的不一致，
    先照舊保留，之後熊想統一成同一種行為再說。"""
    if not ctx.buttons:
        return None

    if not ctx.is_enabled(SYSTEM_KEY):
        return actions.none(
            log="[群星計畫] 🔕 自動點擊已關閉（終端機輸入 /auto 查看開關狀態），"
                "已收到訊息但不會自動點擊，請自行手動選擇",
            stop=False,
        )

    if ctx.awaiting_training_reply:
        catalog = load_catalog(ctx.base_dir)
        session_kind = classify_session_start(ctx.text, catalog)
        if session_kind == "new":
            print("[群星計畫] 🆕 開始新一輪培育（新建衛星）")
        elif session_kind == "continuing":
            print("[群星計畫] ▶️ 續練進行中的衛星")

    action = decide_action(ctx.text, ctx.buttons, ctx.base_dir)
    if action:
        return actions.click_button(
            chat_id=ctx.chat_id, message_id=ctx.message_id,
            data=action["data"], button_text=action["button_text"], reason=action["reason"],
        )

    # 判斷不出來：可能真的是衛星培育但策略沒把握，也可能根本不是衛星培育
    # （例如戰鬥選擇戰術的按鈕）。stop=False，放行讓 reaction_rules 有機會
    # 接手（例如戰鬥用 click: 規則自動應戰）。
    return actions.none(
        log=f"[群星計畫] ⚠️ 策略無法判斷要選哪個按鈕，若非群星計畫訊息可忽略：{ctx.text[:40]}...",
        stop=False,
    )