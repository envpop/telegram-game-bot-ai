"""
world_boss_strategy.py —— 世界王討伐時機判斷（決策層）

跟培育策略一樣的分工：這裡只負責判斷「現在該不該打」，不負責送出指令
（那是 executor 的事），也不負責記錄按鈕/訊息（那是 monitor 的事）。

核心原則（已跟使用者確認）：
  - 目標是「每一隻王（用王的名字識別）至少成功出手一次」，不是每次變身都打
  - 打過一次就算達標，超過也沒關係，不用嚴格控制次數
  - 用王的名字當作記錄 key，不用階數——階數需要靠推算，bot 可能中斷監控
    導致推算錯誤；王的名字每則訊息都直接帶有，不需要依賴任何先前狀態
  - 討伐次數上限不用管：超過上限打了也只是無效動作，沒有損失

三道觸發時機（決策優先序，但彼此獨立、不互相依賴）：
  1. 出現（boss_spawn）      —— 主要時機，王剛出現立刻打
  2. 變身（phase_transition）—— 保險，出現沒接住時，變身後（硬直60秒）補一次
  3. 查詢「世界王」回覆      —— 第二道保險，使用者手動查詢時，bot 順便檢查要不要補刀
                                （這個觸發來自不同的 chat，用另一個函式處理）

用法（公告頻道事件，出現/變身/結束/戰況/護衛）：
    from world_boss_strategy import load_catalog, decide_action

    catalog = load_catalog(BASE_DIR)
    action = decide_action(record["text"], catalog, BASE_DIR, ACCOUNT_ID)
    if action["mode"] == "now":
        await executor.send_now(action["command"], chat_id=action["chat_id"], reason=action["reason"])
    elif action["mode"] == "scheduled":
        run_at = datetime.now(LOCAL_TZ) + timedelta(seconds=action["delay_seconds"])
        asyncio.create_task(executor.schedule_at(run_at, action["command"], chat_id=action["chat_id"], reason=action["reason"]))

用法（「世界王」查詢回覆，不同 chat；新版走統一觸發清單，見檔尾 decide(ctx)）：
    from triggers.world_boss_strategy import decide
    action = decide(ctx)  # ctx 是 triggers.context.TriggerContext

decide_action_from_status_query() 本身保留、邏輯不變，decide(ctx) 只是把
「這則訊息歸不歸我管」的判斷（開關狀態）跟轉成 Action 這兩件事包在外層。
公告頻道那一路的 decide_action() 維持原本 main.py 的 announcement_strategies
清單用法，不受這次調整影響。
"""

import json
import re
from pathlib import Path

import world_boss_progress
from triggers import actions

# 給 action_dispatcher.py 的公告策略迴圈用：迴圈用 getattr(strategy,
# "SYSTEM_KEY", None) 通用地查 auto_toggle 開關狀態，不用在 dispatcher
# 裡寫死判斷「這支模組是不是世界王」。之後新增別種公告策略模組，只要
# 也宣告一個 SYSTEM_KEY（並在 auto_toggle.SYSTEM_KEYS 補一個顯示名稱），
# 開關機制就自動涵蓋，dispatcher 端完全不用改。
SYSTEM_KEY = "world_boss"

_CATALOG_CACHE = None


def _catalog_path(base_dir):
    return Path(base_dir) / "data" / "common" / "world_boss_catalog.json"


def load_catalog(base_dir, force_reload=False):
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None or force_reload:
        with _catalog_path(base_dir).open(encoding="utf-8") as f:
            _CATALOG_CACHE = json.load(f)
    return _CATALOG_CACHE


def classify_message(text, catalog):
    """依序比對 event_types 裡的 trigger_pattern，回傳第一個命中的 event dict，
    都沒命中就回傳 None。
    """
    for event in catalog["event_types"]:
        if event["trigger_pattern"] in text:
            return event
    return None


def _extract_name(text, pattern):
    """用 catalog 裡的 name_pattern（regex 字串）從訊息抓王的名字，抓不到回傳 None。"""
    if not pattern:
        return None
    m = re.search(pattern, text)
    return m.group(1).strip() if m else None


_NO_ACTION = {"mode": None, "delay_seconds": None, "command": None, "chat_id": None, "reason": None}


def decide_action(text, catalog, base_dir, account_id):
    """公告頻道（出現/變身/結束/戰況/護衛）事件的判斷入口。"""
    event = classify_message(text, catalog)
    if event is None:
        return _NO_ACTION

    event_id = event["event_id"]
    # 討伐指令固定送去摸熊神社(bot 私訊)，不是送回偵測到訊息的公告頻道——
    # 公告頻道是唯讀的，bot 沒有發言權限，送過去會直接被 Telegram 拒絕
    # （SendMessageRequest: Chat admin privileges are required）。
    # 偵測來源（哪個 chat 看到這則訊息）跟行動目標（指令送去哪）是兩件事，
    # 不該用同一個 chat_id。
    chat_id = catalog["status_query"]["chat_id"]
    command = catalog["attack_command"]

    if event_id == "boss_spawn":
        name = _extract_name(text, event["name_pattern"])
        if name is None:
            print(f"[世界王] ⚠️ 偵測到出現訊息，但抓不到王的名字，跳過判斷：{text[:40]}...")
            return _NO_ACTION
        if world_boss_progress.has_hit_today(base_dir, account_id, name):
            return _NO_ACTION
        world_boss_progress.mark_hit(base_dir, account_id, name)
        return {"mode": "now", "delay_seconds": None, "command": command, "chat_id": chat_id,
                "reason": f"世界王「{name}」剛出現，今天還沒打過，立刻討伐"}

    if event_id == "phase_transition":
        name = _extract_name(text, event["name_pattern"])
        if name is None:
            print(f"[世界王] ⚠️ 偵測到變身訊息，但抓不到王的名字，跳過判斷：{text[:40]}...")
            return _NO_ACTION
        if world_boss_progress.has_hit_today(base_dir, account_id, name):
            return _NO_ACTION
        delay = event.get("cooldown_seconds", 60)
        world_boss_progress.mark_hit(base_dir, account_id, name)
        return {"mode": "scheduled", "delay_seconds": delay, "command": command, "chat_id": chat_id,
                "reason": f"世界王「{name}」變身，今天還沒打過，等硬直 {delay} 秒後討伐"}

    if event_id == "boss_defeated":
        name = _extract_name(text, event["name_pattern"])
        if name and not world_boss_progress.has_hit_today(base_dir, account_id, name):
            print(f"[世界王] ⚠️ 「{name}」已被討伐，但今天沒有打過的記錄——出現/變身/查詢三道保險都沒接住，這隻已經錯過了。")
        return _NO_ACTION

    # periodic_status_report / guards_cleared：無動作
    return _NO_ACTION


def decide_action_from_status_query(text, catalog, base_dir, account_id):
    """「世界王」查詢指令回覆的判斷入口（第三道保險）。跟 decide_action 是分開的
    函式，因為這個觸發來自不同的 chat、不同的訊息格式，不是被動監聽公告頻道。
    """
    query = catalog["status_query"]
    if query["trigger_pattern"] not in text:
        return _NO_ACTION

    name = _extract_name(text, query["name_pattern"])
    if name is None:
        print(f"[世界王] ⚠️ 偵測到查詢回覆，但抓不到王的名字，跳過判斷：{text[:40]}...")
        return _NO_ACTION

    if world_boss_progress.has_hit_today(base_dir, account_id, name):
        return _NO_ACTION  # 今天已經打過了，不用補刀

    if query["alive_check_pattern"] in text:
        return _NO_ACTION  # 王已經死了，補不了

    world_boss_progress.mark_hit(base_dir, account_id, name)
    return {
        "mode": "now",
        "delay_seconds": None,
        "command": catalog["attack_command"],
        "chat_id": query["chat_id"],  # 討伐指令固定送去摸熊神社(bot 私訊)，公告頻道沒有發言權限
        "reason": f"查詢「世界王」時發現「{name}」今天還沒打過、王還活著，補一刀",
    }


def decide(ctx):
    """action_dispatcher.py 統一觸發清單入口（server 訊息這一路，第三道保險），
    取代原本的 _handle_world_boss_status_query()；跟公告頻道那一路的
    decide_action() 是分開的兩個函式，維持原本檔頭說明的分工，只是這裡
    多包一層轉成 Action。開關關閉時 stop=False（不吃掉訊息，放行給其他
    trigger），跟原本行為一致。"""
    if not ctx.is_enabled(SYSTEM_KEY):
        return None

    catalog = load_catalog(ctx.base_dir)
    action = decide_action_from_status_query(ctx.text, catalog, ctx.base_dir, ctx.account_id)
    if action["mode"] == "now":
        return actions.send_now(action["command"], chat_id=action["chat_id"], reason=action["reason"])
    return None