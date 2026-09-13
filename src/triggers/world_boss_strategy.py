"""
world_boss_strategy.py —— 世界王討伐時機判斷（決策層）

跟培育策略一樣的分工：這裡只負責判斷「現在該不該打」，不負責送出指令
（那是 executor 的事），也不負責記錄按鈕/訊息（那是 monitor 的事）。

用王的名字當作記錄 key，不用階數——階數需要靠推算，bot 可能中斷監控
導致推算錯誤；王的名字每則訊息都直接帶有，不需要依賴任何先前狀態。

三道觸發時機（決策優先序，但彼此獨立、不互相依賴）：
  1. 出現（boss_spawn）      —— 主要時機，王剛出現立刻判斷
  2. 變身（phase_transition）—— 保險，出現沒接住時，變身後（硬直60秒）補一次；
                                也是「打到一半換相被硬直卡住」時恢復攻擊的時機
  3. 查詢「世界王」回覆      —— 第二道保險，也是 auto/furnace_loop/full_clear
                                這幾個模式「不確定現在該做什麼」時的統一確認點
                                （這個觸發來自不同的 chat，用另一個函式處理）

=== 2026-09-06 大改版：拿掉所有「流程進行中」的 session 狀態 ===
熊指出：furnace_loop_strategy/full_clear_strategy 原本各自維護
is_active() 這種「記住自己在做什麼」的旗標，斷線重連後容易變成過時的
錯誤記憶(記得在忙，但遊戲實際狀態早就不是那樣了)。改成「不確定就查
world_boss_progress.py 裡這隻王的持久記錄，或直接送一次『世界王』查詢，
不要記住自己在做什麼」。

三個模式現在都靠 world_boss_progress.py 的每王記錄(mode/count_exhausted/
furnace_completed)判斷，不再需要任何「進行中」旗標：
    - touch：摸到就算完成，不用管爐火。
    - furnace_loop：次數用完時檢查 furnace_completed，還沒完成就觸發
      爐火重置一次；已經完成過就不再觸發。
    - full_clear：次數用完一律觸發爐火重置，不設次數上限。

爐火重置完成後「要不要恢復攻擊」也不用專門的完成通知去反推——直接送
一次「世界王」查詢（見 _query_after_furnace_reset()），查詢回覆看到
「之前記錄是次數用完、現在卻沒用完」就知道是重置剛完成，交給
decide_action_from_status_query() 統一判斷要不要恢復攻擊。

戰報找不到王名(只有手動連續討伐剛好把王打死才會發生)時，一樣直接送
「世界王」查詢，不用猜是哪一隻王——查詢回覆本來就會帶出王名。

用法（公告頻道事件，出現/變身/結束/戰況/護衛）：
    from world_boss_strategy import load_catalog, decide_action

    catalog = load_catalog(BASE_DIR)
    action = decide_action(record["text"], catalog, BASE_DIR, ACCOUNT_ID)
    # action 是 plain dict，用 triggers.actions.execute_dict(action) 執行

用法（「世界王」查詢回覆，不同 chat；新版走統一觸發清單，見檔尾 decide(ctx)）：
    from triggers.world_boss_strategy import decide
    action = decide(ctx)  # ctx 是 triggers.context.TriggerContext，回傳 Action

decide_action_from_status_query() 本身保留、介面不變，decide(ctx) 只是把
「這則訊息歸不歸我管」的判斷（開關狀態）跟轉成 Action 這兩件事包在外層。
公告頻道那一路的 decide_action() 維持原本 main.py 的 announcement_strategies
清單用法，不受這次調整影響。
"""

import asyncio
import json
import re
from pathlib import Path

import auto_toggle
import world_boss_mode
import world_boss_progress
from parsing.response_shapes import world_boss_status
from triggers import actions
from triggers import furnace_cycle_strategy
from triggers import furnace_loop_strategy
from triggers import full_clear_strategy
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

# 這支 bot 一次只服務一個帳號——爐火重置完成的無參數回呼(見
# _query_after_furnace_reset())沒辦法知道要查詢哪個帳號，只能記住
# 「最後一次是誰在互動」。這裡刻意在每一次世界王相關判斷(不只是流程
# 起點)都更新，盡量降低過時的風險，跟之前討論過的「記住流程進行中」
# 性質不同——這只是「目前是哪個帳號在跑」，幾乎不會變的事實，不是會
# 過期/導致誤判的「決策狀態」。
_last_known_context = None  # {"base_dir","account_id"} 或 None


def _remember_context(base_dir, account_id):
    global _last_known_context
    _last_known_context = {"base_dir": base_dir, "account_id": account_id}


# 王出現時剛好護衛也在，兩邊都會換手/搶陣容（熊 2026-09-05／09-06 反映）。
# 判斷準則是「這隻王身上有沒有護衛」+「清護衛的自動開關有沒有開」——
# 不是查 guard_clear_strategy.is_session_active()：王剛出現的當下，
# guard_clear 可能都還沒來得及反應護衛訊息、session 根本還沒開始，用
# session 狀態判斷會有時間差漏洞。只要「有護衛」且「開關開著」，就代表
# 護衛遲早會被清，世界王(不管哪個模式)都先讓路，不用等到「session 真的
# 開始了」才知道要讓路。
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


# 護衛清完的瞬間就立刻換手/攻擊，容易撞到清護衛最後一個動作本身的
# 伺服器冷卻(熊 2026-09-06 反映；熊也提到這個緩衝可能還要再調整，目前
# 先維持這個保守值)。留一個小緩衝，不用等到下一則訊息，純粹讓伺服器
# 喘口氣——數字是拍腦袋的保守值，不是遊戲機制數字。
RESUME_BUFFER_SECONDS = 3.0


async def resume_pending_boss():
    """guard_clear_strategy 的 session 結束時透過 on_session_end() 回呼
    觸發。沒有排隊中的王時安靜結束，不是錯誤（大部分護衛清理事件都跟
    世界王無關，不該每次都印東西）。"""
    global _pending_boss
    if _pending_boss is None:
        return
    pending = _pending_boss
    _pending_boss = None

    await asyncio.sleep(RESUME_BUFFER_SECONDS)

    mode, mode_reason = _current_mode(pending["text"], pending["base_dir"], pending["account_id"])
    world_boss_progress.mark_mode(pending["base_dir"], pending["account_id"], pending["name"], mode)
    action_dict = _dispatch_for_mode(
        pending["text"], pending["name"], mode, mode_reason, pending["base_dir"], pending["account_id"],
    )
    if await actions.execute_dict(action_dict):
        print(f"[世界王] ▶️ 護衛清完（緩衝 {RESUME_BUFFER_SECONDS:.0f} 秒後），"
              f"接續處理「{pending['name']}」（判定為 {mode}）")


guard_clear_strategy.on_session_end(resume_pending_boss)


async def _query_after_furnace_reset():
    """furnace_cycle_strategy 的爐火流程結束時(不管是正常炸爆還是被
    搶先重置)透過 on_session_end() 回呼觸發。不需要知道是 furnace_loop
    還是 full_clear 觸發的這次重置，也不需要記得是哪一隻王——直接送
    一次「世界王」查詢，回覆會自然帶出王名跟最新次數，交給
    decide_action_from_status_query() 判斷「次數補回來了，該不該恢復
    攻擊／該標記完成」（熊 2026-09-06 確認：出問題時僅做一次查詢，
    還有問題就放過，不重試）。"""
    if _last_known_context is None:
        return
    base_dir = _last_known_context["base_dir"]
    account_id = _last_known_context["account_id"]
    catalog = load_catalog(base_dir)
    chat_id = catalog["status_query"]["chat_id"]
    await actions.execute(actions.send_now(
        "世界王", chat_id=chat_id, reason="爐火流程結束，查詢確認是否需要恢復攻擊",
    ))


furnace_cycle_strategy.on_session_end(_query_after_furnace_reset)


def _dispatch_for_mode(text, name, mode, mode_reason, base_dir, account_id, context_label="剛出現"):
    """依 mode 分派要做的事。抽出來給 boss_spawn 現場觸發、查詢保險、
    護衛清完後的接續觸發(resume_pending_boss)共用，不要各自寫一次判斷
    邏輯。context_label 只影響 touch 模式的 log 文字通不通順(「剛出現」
    /「查詢時發現」)，不影響判斷邏輯本身。"""
    catalog = load_catalog(base_dir)
    chat_id = catalog["status_query"]["chat_id"]
    command = catalog["attack_command"]

    if mode == world_boss_mode.FURNACE_LOOP:
        print(f"[世界王] 「{name}」判定為 furnace_loop（{mode_reason}），交給爐火模式處理")
        return furnace_loop_strategy.start(text, base_dir, account_id)
    if mode == world_boss_mode.FULL_CLEAR:
        print(f"[世界王] 「{name}」判定為 full_clear（{mode_reason}），交給全程模式處理")
        return full_clear_strategy.start(text, base_dir, account_id)
    if mode != world_boss_mode.TOUCH:
        print(f"[世界王] 「{name}」判定為 {mode}（{mode_reason}），不是 touch，先不摸一下（等 {mode} 的行為邏輯補上）")
        return _NO_ACTION
    return {"mode": "now", "delay_seconds": None, "command": command, "chat_id": chat_id,
            "reason": f"世界王「{name}」{context_label}，今天還沒打過，立刻討伐"}


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

    _remember_context(base_dir, account_id)

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
        world_boss_progress.mark_mode(base_dir, account_id, name, mode)
        return _dispatch_for_mode(text, name, mode, mode_reason, base_dir, account_id)

    if event_id == "phase_transition":
        name = _extract_name(text, event["name_pattern"])
        if name is None:
            print(f"[世界王] ⚠️ 偵測到變身訊息，但抓不到王的名字，跳過判斷：{text[:40]}...")
            return _NO_ACTION

        delay = event.get("cooldown_seconds", 60)

        # 2026-09-06 改成查 world_boss_progress 的持久記錄，不再查
        # is_active() 這種 session 旗標(已經拿掉)。換相時「這隻王原本
        # 判定成什麼模式」直接查表就有答案，不需要「流程是否正在跑」
        # 這種額外的狀態。
        mode = world_boss_progress.get_mode(base_dir, account_id, name)

        if mode is None:
            # 沒記錄過(可能是出現公告漏接)，現在補判斷一次。
            if world_boss_progress.has_hit_today(base_dir, account_id, name):
                return _NO_ACTION
            world_boss_progress.mark_hit(base_dir, account_id, name)
            mode, mode_reason = _current_mode(text, base_dir, account_id)
            world_boss_progress.mark_mode(base_dir, account_id, name, mode)
            print(f"[世界王] 「{name}」換相時才第一次判定模式：{mode}（{mode_reason}）")

        if mode == world_boss_mode.FURNACE_LOOP:
            print(f"[世界王] 「{name}」換相，爐火模式，{delay} 秒後繼續連續討伐")
            return furnace_loop_strategy.handle_phase_transition(text, delay, base_dir, account_id)
        if mode == world_boss_mode.FULL_CLEAR:
            print(f"[世界王] 「{name}」換相，全程模式，{delay} 秒後繼續連續討伐")
            return full_clear_strategy.handle_phase_transition(text, delay, base_dir, account_id)

        # touch：換相不影響「有沒有打過」這件事，touch 只求打過一次，
        # 不需要因為換相又補一次。
        return _NO_ACTION

    if event_id == "boss_defeated":
        name = _extract_name(text, event["name_pattern"])
        if name and not world_boss_progress.has_hit_today(base_dir, account_id, name):
            print(f"[世界王] ⚠️ 「{name}」已被討伐，但今天沒有打過的記錄——出現/變身/查詢三道保險都沒接住，這隻已經錯過了。")
        # 2026-09-06：不再需要專門的「結束流程」動作——full_clear/
        # furnace_loop 都已經拿掉 session 狀態，沒有「進行中」這件事
        # 需要被結束。王死了之後自然不會再有戰報/查詢顯示這隻王的次數
        # 資訊，本身就是終止訊號，不用額外處理。
        return _NO_ACTION

    # periodic_status_report / guards_cleared：無動作
    return _NO_ACTION


def decide_action_from_status_query(text, catalog, base_dir, account_id):
    """「世界王」查詢指令回覆的判斷入口（第三道保險，也是 furnace_loop/
    full_clear「不確定現在該做什麼」時的統一確認點）。跟 decide_action
    是分開的函式，因為這個觸發來自不同的 chat、不同的訊息格式，不是
    被動監聽公告頻道。
    """
    query = catalog["status_query"]
    if query["trigger_pattern"] not in text:
        return _NO_ACTION

    name = _extract_name(text, query["name_pattern"])
    if name is None:
        print(f"[世界王] ⚠️ 偵測到查詢回覆，但抓不到王的名字，跳過判斷：{text[:40]}...")
        return _NO_ACTION

    _remember_context(base_dir, account_id)

    if query["alive_check_pattern"] in text:
        return _NO_ACTION  # 王已經死了，補不了

    # 2026-09-06：查詢回覆本身就帶著「今日 X/Y 次」，用這個資訊直接判斷
    # 次數是否用完並持久化記錄——不用等戰報，查詢本身就是最新狀態。
    # 熊確認：沒有這個欄位代表沒有正在活著的世界王了。
    parsed = world_boss_status.parse(text) if world_boss_status.signature(text) else {}
    daily_count = parsed.get("your_daily_count")
    daily_limit = parsed.get("your_daily_limit")

    if daily_count is None or daily_limit is None:
        return _NO_ACTION  # 沒有活著的世界王，不用往下判斷

    exhausted_now = daily_count >= daily_limit
    was_exhausted = world_boss_progress.is_count_exhausted(base_dir, account_id, name)

    if exhausted_now:
        world_boss_progress.mark_count_exhausted(base_dir, account_id, name)
        mode = world_boss_progress.get_mode(base_dir, account_id, name)
        if mode == world_boss_mode.FULL_CLEAR:
            print(f"[世界王] 查詢「{name}」次數已用完，全程模式自動觸發爐火重置")
            return furnace_cycle_strategy.start(reason=f"查詢確認「{name}」次數已用完，全程模式自動爐火重置")
        if mode == world_boss_mode.FURNACE_LOOP and not world_boss_progress.is_furnace_completed(base_dir, account_id, name):
            print(f"[世界王] 查詢「{name}」次數已用完，爐火模式自動觸發爐火重置")
            return furnace_cycle_strategy.start(reason=f"查詢確認「{name}」次數已用完，爐火模式自動爐火重置")
        return _NO_ACTION  # touch，或 furnace_loop 已經重置過一次了，不用再動作

    if was_exhausted:
        # 之前記錄是用完的，現在查詢卻顯示沒用完 —— 代表爐火重置剛完成，
        # 次數補回來了。清掉標記，才能正確偵測「下一輪」次數用完
        # (full_clear 需要重複偵測很多輪)。
        world_boss_progress.clear_count_exhausted(base_dir, account_id, name)
        mode = world_boss_progress.get_mode(base_dir, account_id, name)
        if mode == world_boss_mode.FULL_CLEAR:
            print(f"[世界王] 「{name}」爐火重置完成(次數已補回)，全程模式恢復連續討伐")
            catalog_chat_id = catalog["status_query"]["chat_id"]
            return {"mode": "now", "command": "連續討伐", "chat_id": catalog_chat_id,
                    "reason": f"「{name}」爐火重置完成，全程模式恢復連續討伐"}
        if mode == world_boss_mode.FURNACE_LOOP:
            world_boss_progress.mark_furnace_completed(base_dir, account_id, name)
            print(f"[世界王] 「{name}」爐火重置完成，爐火模式到此為止，不繼續打")
        return _NO_ACTION

    # 真正第一次看到這隻王、還沒打過。
    if world_boss_progress.has_hit_today(base_dir, account_id, name):
        return _NO_ACTION

    world_boss_progress.mark_hit(base_dir, account_id, name)

    if _should_wait_for_guards(text, base_dir):
        _queue_pending_boss(text, name, base_dir, account_id)
        return _NO_ACTION

    mode, mode_reason = _current_mode(text, base_dir, account_id)
    world_boss_progress.mark_mode(base_dir, account_id, name, mode)
    return _dispatch_for_mode(text, name, mode, mode_reason, base_dir, account_id, context_label="查詢時發現")


def decide(ctx):
    """action_dispatcher.py 統一觸發清單入口（server 訊息這一路，第三道保險），
    取代原本的 _handle_world_boss_status_query()；跟公告頻道那一路的
    decide_action() 是分開的兩個函式，維持原本檔頭說明的分工，只是這裡
    多包一層轉成 Action。開關關閉時回傳 None（不吃掉訊息，放行給其他
    trigger），跟原本行為一致。

    2026-09-06 修正：原本這裡只認 action["mode"]=="now"，"scheduled"/
    "sequence" 會被直接忽略——查詢觸發 furnace_loop/full_clear(需要
    換手，回傳 "sequence")時完全不會有動作，是個潛在的漏洞。改用
    actions.dict_to_action() 統一轉換，三種 mode 都能正確處理。

    decide_action_from_status_query() 觸發爐火重置時，回傳的是
    furnace_cycle_strategy.start() 直接給的 Action 物件(不是 plain
    dict)——因為那支函式本來就是設計給 server_trigger 的 decide(ctx)
    用，這裡剛好也是同一種介面，直接沿用，不用另外包一層轉換，兩種
    回傳型別在這裡分流處理。
    """
    if not ctx.is_enabled(SYSTEM_KEY):
        return None

    catalog = load_catalog(ctx.base_dir)
    action = decide_action_from_status_query(ctx.text, catalog, ctx.base_dir, ctx.account_id)
    if isinstance(action, dict):
        return actions.dict_to_action(action)
    return action  # 已經是 Action 物件