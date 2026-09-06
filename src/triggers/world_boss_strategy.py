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

import auto_toggle
import world_boss_mode
import world_boss_progress
from parsing.response_shapes import world_boss_status
from triggers import actions
from triggers import furnace_loop_strategy
from triggers import guard_clear_strategy

# 給 action_dispatcher.py 的公告策略迴圈用：迴圈用 getattr(strategy,
# "SYSTEM_KEY", None) 通用地查 auto_toggle 開關狀態，不用在 dispatcher
# 裡寫死判斷「這支模組是不是世界王」。之後新增別種公告策略模組，只要
# 也宣告一個 SYSTEM_KEY（並在 auto_toggle.SYSTEM_KEYS 補一個顯示名稱），
# 開關機制就自動涵蓋，dispatcher 端完全不用改。
SYSTEM_KEY = auto_toggle.WORLD_BOSS

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

# 王出現時剛好護衛也在，兩邊都會換手/搶陣容（熊 2026-09-05／09-06 反映）。
# 判斷準則是「這隻王身上有沒有護衛」+「清護衛的自動開關有沒有開」——
# 不是查 guard_clear_strategy.is_session_active()：王剛出現的當下，
# guard_clear 可能都還沒來得及反應護衛訊息、session 根本還沒開始，用
# session 狀態判斷會有時間差漏洞。只要「有護衛」且「開關開著」，就代表
# 護衛遲早會被清，世界王(不管哪個模式)都先讓路，不用等到「session 真的
# 開始了」才知道要讓路。
#
# 這個排隊機制本來只放在 furnace_loop_strategy.py(因為當初只有它會換手)，
# 2026-09-06 改成放在這裡、對所有模式生效，因為 touch 模式也會跟清護衛
# 搶「出戰」這個共用資源。
_pending_boss = None  # {"text","name","base_dir","account_id"} 或 None


def _has_guards(text):
    if not world_boss_status.signature(text):
        return False
    return bool(world_boss_status.parse(text).get("has_guards"))


def _should_wait_for_guards(text, base_dir):
    return _has_guards(text) and auto_toggle.is_enabled(base_dir, auto_toggle.GUARD_CLEAR)


def _queue_pending_boss(text, name, base_dir, account_id):
    global _pending_boss
    _pending_boss = {"text": text, "name": name, "base_dir": base_dir, "account_id": account_id}
    print(f"[世界王] 🕒 「{name}」身上有護衛、清護衛自動開啟中，先排隊，等護衛清完再行動")


async def resume_pending_boss():
    """guard_clear_strategy 的 session 結束時透過 on_session_end() 回呼
    觸發。沒有排隊中的王時安靜結束，不是錯誤（大部分護衛清理事件都跟
    世界王無關，不該每次都印東西）。"""
    global _pending_boss
    if _pending_boss is None:
        return
    pending = _pending_boss
    _pending_boss = None
    mode, mode_reason = _current_mode(pending["text"], pending["base_dir"], pending["account_id"])
    action_dict = _dispatch_for_mode(
        pending["text"], pending["name"], mode, mode_reason, pending["base_dir"], pending["account_id"],
    )
    if await actions.execute_dict(action_dict):
        print(f"[世界王] ▶️ 護衛清完，接續處理「{pending['name']}」（判定為 {mode}）")


guard_clear_strategy.on_session_end(resume_pending_boss)


def _dispatch_for_mode(text, name, mode, mode_reason, base_dir, account_id):
    """依 mode 分派要做的事。抽出來給 boss_spawn 現場觸發、跟護衛清完後的
    接續觸發(resume_pending_boss)共用，不要各自寫一次判斷邏輯。"""
    catalog = load_catalog(base_dir)
    chat_id = catalog["status_query"]["chat_id"]
    command = catalog["attack_command"]

    if mode == world_boss_mode.FURNACE_LOOP:
        print(f"[世界王] 「{name}」判定為 furnace_loop（{mode_reason}），交給爐火模式處理")
        return furnace_loop_strategy.start(text, base_dir, account_id)
    if mode != world_boss_mode.TOUCH:
        print(f"[世界王] 「{name}」判定為 {mode}（{mode_reason}），不是 touch，先不摸一下（等 {mode} 的行為邏輯補上）")
        return _NO_ACTION
    return {"mode": "now", "delay_seconds": None, "command": command, "chat_id": chat_id,
            "reason": f"世界王「{name}」剛出現，今天還沒打過，立刻討伐"}


def _current_mode(text, base_dir, account_id):
    """回傳 (mode, reason)。stage/ticket_bounty 只有在這則文字符合
    world_boss_status 的格式(帶完整「今日世界王」區塊)時才抓得到——
    目前確認「出現」「查詢回覆」都有這個區塊，可以重用 world_boss_status
    的解析結果，不用在這裡重寫一次 regex(共用資料語意，不重複解析邏輯)。

    「變身」(phase_transition，「形體崩解重組」格式)目前手上沒有實際
    樣本能確認訊息裡有沒有階數/商會懸賞資訊，保守起見抓不到就當作
    stage=None、沒有懸賞，讓 determine_mode() 用預設值判斷——之後拿到
    實際樣本，如果變身訊息其實也帶得到這些欄位，再回頭補。
    """
    stage = None
    has_ticket_bounty = False
    if world_boss_status.signature(text):
        parsed = world_boss_status.parse(text)
        stage = parsed.get("stage")
        has_ticket_bounty = parsed.get("ticket_bounty", False)
    return world_boss_mode.determine_mode(base_dir, account_id, stage, has_ticket_bounty)


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

        # 這則出現公告通常不會重複，一旦決定要處理(不管是現在動作、還是
        # 排隊等護衛)，就是我們唯一能處理這隻王的機會，先標記掉。
        world_boss_progress.mark_hit(base_dir, account_id, name)

        if _should_wait_for_guards(text, base_dir):
            _queue_pending_boss(text, name, base_dir, account_id)
            return _NO_ACTION

        mode, mode_reason = _current_mode(text, base_dir, account_id)
        return _dispatch_for_mode(text, name, mode, mode_reason, base_dir, account_id)

    if event_id == "phase_transition":
        name = _extract_name(text, event["name_pattern"])
        if name is None:
            print(f"[世界王] ⚠️ 偵測到變身訊息，但抓不到王的名字，跳過判斷：{text[:40]}...")
            return _NO_ACTION

        delay = event.get("cooldown_seconds", 60)

        # 爐火流程(furnace_loop)進行中如果剛好換相，代表次數還沒用完、
        # 但攻擊被硬直卡住了——不是次數用完，不能直接判斷成「這一輪結束」
        # 去啟動爐火重置。硬直 delay 秒後重送一次「連續討伐」繼續打，
        # 沿用既有的變身硬直秒數，不用另外訂數字（熊 2026-09-06 反映）。
        if furnace_loop_strategy.is_active():
            print(f"[世界王] 「{name}」換相，爐火流程還在進行中（次數還沒用完），{delay} 秒後繼續連續討伐")
            return furnace_loop_strategy.handle_phase_transition(delay)

        if world_boss_progress.has_hit_today(base_dir, account_id, name):
            return _NO_ACTION

        world_boss_progress.mark_hit(base_dir, account_id, name)

        if _should_wait_for_guards(text, base_dir):
            _queue_pending_boss(text, name, base_dir, account_id)
            return _NO_ACTION

        mode, mode_reason = _current_mode(text, base_dir, account_id)
        if mode != world_boss_mode.TOUCH:
            print(f"[世界王] 「{name}」判定為 {mode}（{mode_reason}），不是 touch，先不摸一下（等 {mode} 的行為邏輯補上）")
            return _NO_ACTION
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

    if _should_wait_for_guards(text, base_dir):
        _queue_pending_boss(text, name, base_dir, account_id)
        return _NO_ACTION

    mode, mode_reason = _current_mode(text, base_dir, account_id)
    if mode != world_boss_mode.TOUCH:
        print(f"[世界王] 「{name}」判定為 {mode}（{mode_reason}），不是 touch，先不補刀（等 {mode} 的行為邏輯補上）")
        return _NO_ACTION

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