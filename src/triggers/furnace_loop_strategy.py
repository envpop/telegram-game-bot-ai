# -*- coding: utf-8 -*-
"""
furnace_loop_strategy.py —— 世界王「爐火」模式(furnace_loop)的上層流程

負責 world_boss_mode.FURNACE_LOOP 模式底下實際要做的事：
    王出現 → 檢查陣容(主手屬性合弱點/副手屬性能相生主手) → 不夠好就換手
    → 送出「連續討伐」→ 反應戰報，偵測次數用完 → 交給 furnace_cycle_strategy
    重置一次 → 結束(這個模式定義上不追到王死，重置完就停，跟 full_clear
    不同——見 world_boss_mode.py 的模式說明)

由 world_boss_strategy.py 在判斷 mode==furnace_loop 時呼叫 start()
啟動；之後靠 decide(ctx) 反應戰報接力，觀火/投爐那段完全交給
furnace_cycle_strategy.py，不重寫（熊 2026-09-04 明確要求）。

=== 2026-09-06 大改版：拿掉「流程進行中」的 session 狀態 ===
原本用 is_active()/runtime_state 記錄「furnace_loop 是不是正在跑」，
熊指出這種「記住自己在做什麼」的設計，斷線重連後容易變成過時的錯誤
記憶(記得在忙，但遊戲實際狀態早就不是那樣了)，要求整個拿掉，改成
「不確定就查當下的紀錄／查詢遊戲，不要記住自己在做什麼」。

現在的判斷完全靠 world_boss_progress.py 裡「這隻王」的持久記錄
(mode/count_exhausted/furnace_completed)，不是「這個流程是否正在跑」
這種旗標：
    - 次數用完：直接查表看這隻王的 mode 是不是 furnace_loop、
      furnace_completed 是不是還沒完成，是的話就觸發爐火——不需要知道
      「我是不是正在追蹤這隻王」，因為表格本身就是答案。
    - 找不到王名(只有手動連續討伐剛好把王打死才會發生，熊 2026-09-06
      說明)：直接送「世界王」查詢，查詢回覆自然帶出王名，交給
      world_boss_strategy.decide_action_from_status_query() 處理，
      不用自己猜是哪一隻王。
    - 換相：跟次數用完是不同的訊號(硬直，不是用完)，不需要「這個流程
      是否正在跑」才能判斷——只要偵測到換相事件，就重新判斷陣容繼續打，
      不管是誰在打。

=== 陣容判斷標準(熊 2026-09-04 確認) ===
判斷「要不要換」只看屬性有沒有對上，不強求類型也剋制（「屬性對上、戰力
盡量高就可以」）；但真的要換的話，換手目標不將就，用
weakness_matcher.TopSelector.decide_switch_commands() 找「屬性+類型+
戰力都最好」的陀螺——门槛放寬，換的時候還是換最好的。

=== 連續討伐 ===
熊確認遊戲本身有「連續討伐」指令，伺服器端自己會連續出手，這支模組只送
一次「連續討伐」，不自己迴圈送「討伐」。跟前面換手指令的間隔可以極短，
這裡偷懶直接用跟換手同樣的 2 秒間隔送成一組序列（熊確認這樣沒問題）。

=== 怎麼判斷次數用完 ===
反應 world_boss_continuous_report(連續討伐回覆，逐刀彙總格式)跟
world_boss_battle_report(單刀戰報)兩種 shape，兩者都有 daily_count/
daily_limit 欄位，判斷邏輯共用(見 is_daily_count_exhausted())。

=== 換相 ===
王在連續討伐途中換相會被硬直卡住，次數還沒用完，但這一輪指令提前中止，
不是「次數用完」，不能誤判去啟動爐火重置。world_boss_strategy.py 偵測到
phase_transition 事件時會呼叫 handle_phase_transition()，跟次數用完是
完全獨立的另一條路徑，不共用判斷。
"""
import auto_toggle
import world_boss_mode
import world_boss_progress
from main_tower_advisor import load_json, RULES_PATH
from roster_loader import load_roster
from weakness_matcher import WeaknessParser, TopSelector
from triggers import actions
from triggers import furnace_cycle_strategy

SYSTEM_KEY = auto_toggle.WORLD_BOSS

# 換手/連續討伐這組序列的間隔，熊確認的實測值。
SWITCH_DELAY_SECONDS = 2.0

# 見檔尾「爐火完成後的直接接手」說明：只記「最後一次是哪隻王觸發爐火
# 重置」，不是「流程進行中」的旗標——爐火真的完成時，靠這個知道該把
# 哪隻王標記成 furnace_completed，省掉多送一次「世界王」查詢的往返。
# 記錯/記到舊資料的代價很小(頂多錯過一次標記，下次戰報還是會再判斷一
# 次)，跟之前拿掉的 session 狀態(記錯會導致做出錯誤動作)性質不同。
_last_boss_context = None  # {"base_dir","account_id","name"} 或 None


def remember_boss(base_dir, account_id, name):
    """觸發爐火重置前呼叫，讓爐火完成時知道要把哪隻王標記完成。
    world_boss_strategy.decide_action_from_status_query() 查詢路徑觸發
    爐火時也要呼叫這個，兩個觸發入口共用同一份記憶。"""
    global _last_boss_context
    _last_boss_context = {"base_dir": base_dir, "account_id": account_id, "name": name}


async def _on_furnace_complete():
    """furnace_cycle_strategy 的爐火流程結束時觸發(不管正常炸爆還是被
    搶先重置)。furnace_loop 不用像 full_clear 那樣恢復攻擊，只需要把
    「這隻王已經重置過一次了」記下來，不用查詢遊戲確認——這個標記本身
    就是本地的持久記錄，不需要跟遊戲對答案。"""
    if _last_boss_context is None:
        return
    ctx = _last_boss_context
    mode = world_boss_progress.get_mode(ctx["base_dir"], ctx["account_id"], ctx["name"])
    if mode != world_boss_mode.FURNACE_LOOP:
        return  # 不是這次爐火重置的觸發者(可能是 full_clear 觸發的)，不是我的事
    world_boss_progress.mark_furnace_completed(ctx["base_dir"], ctx["account_id"], ctx["name"])
    print(f"[爐火模式] 「{ctx['name']}」爐火重置完成，到此為止，不繼續打")


furnace_cycle_strategy.on_session_end(_on_furnace_complete)


def start(text, base_dir, account_id, reason="次數還沒用完，先確認陣容再連續討伐"):
    """world_boss_strategy.py 的 decide_action()(公告路徑)判斷
    mode==furnace_loop 時呼叫。text 是偵測到這隻王的原始文字(出現公告)，
    用來取得弱點/類型資訊。

    回傳格式刻意跟 decide_action() 本身一致(plain dict，mode 是
    "now"/"sequence"/None)，不是 triggers.actions.Action 物件——
    因為呼叫端(world_boss_strategy.decide_action())本身就是走公告路徑的
    plain-dict 介面，被 action_dispatcher.py 的 _handle_announcement()
    同步消費，不能回傳這支模組另一半(decide())用的 Action 物件。
    """
    weakness = WeaknessParser.parse(text)
    if weakness is None:
        return {"mode": None, "command": None, "chat_id": None,
                "reason": "抓不到弱點屬性，無法判斷陣容，跳過這隻王"}

    roster = load_roster(base_dir, account_id)
    rules = load_json(RULES_PATH)

    commands = TopSelector.decide_switch_commands(roster, weakness, rules)
    commands.append("連續討伐")

    if len(commands) == 1:
        return {"mode": "now", "command": commands[0], "chat_id": None,
                "reason": f"{reason}，陣容已經合格，直接連續討伐"}

    return {
        "mode": "sequence", "commands": commands, "interval_seconds": SWITCH_DELAY_SECONDS,
        "chat_id": None,
        "reason": f"{reason}，先換手（{' → '.join(commands[:-1])}）再連續討伐",
    }


def handle_phase_transition(text, delay_seconds, base_dir, account_id):
    """world_boss_strategy.py 偵測到 phase_transition 事件時呼叫。代表王
    在連續討伐途中換相，被硬直卡住——次數還沒用完，只是這一輪指令提前
    中止，不是「次數用完」，不能誤判去啟動爐火重置。

    換相後弱點屬性、甚至王的類型都可能整個換掉(熊反映的真實案例：
    木→火、防禦型→持久型)，重新判斷陣容後排程恢復連續討伐，不是單純
    重送「連續討伐」。WeaknessParser.parse() 本來就認得換相公告的格式
    (「五行 X→Y　類型 A→B　新弱點:Z」)，直接沿用。

    硬直 delay_seconds 秒是遊戲機制本身的限制，這段時間本來就打不到王；
    換手動作排在硬直之後、攻擊之前，一次用 scheduler.py 的
    steps+interval 機制排完(硬直→換手→換手→攻擊，彼此間隔
    SWITCH_DELAY_SECONDS)，不會太快連續送出指令。"""
    weakness = WeaknessParser.parse(text)
    if weakness is None:
        # 抓不到新弱點，沒辦法判斷陣容，只能照舊直接重送連續討伐——
        # 陣容可能不是最佳，但總比完全不打好，不要因為解析失敗就放棄。
        return {
            "mode": "scheduled", "delay_seconds": delay_seconds, "steps": ["連續討伐"],
            "interval": (SWITCH_DELAY_SECONDS, SWITCH_DELAY_SECONDS), "chat_id": None,
            "reason": f"世界王換相，硬直 {delay_seconds} 秒後繼續連續討伐"
                      "（次數還沒用完，換相文字抓不到新弱點，陣容可能不是最佳）",
        }

    roster = load_roster(base_dir, account_id)
    rules = load_json(RULES_PATH)
    commands = TopSelector.decide_switch_commands(roster, weakness, rules)
    commands.append("連續討伐")

    switch_note = f"先換手（{' → '.join(commands[:-1])}）再" if len(commands) > 1 else ""
    return {
        "mode": "scheduled", "delay_seconds": delay_seconds, "steps": commands,
        "interval": (SWITCH_DELAY_SECONDS, SWITCH_DELAY_SECONDS), "chat_id": None,
        "reason": f"世界王換相(新弱點 {weakness.current_element}屬性)，"
                  f"硬直 {delay_seconds} 秒後{switch_note}繼續連續討伐（次數還沒用完）",
    }


def is_daily_count_exhausted(structured: dict):
    """純函式：從戰報/連續討伐回覆的解析結果判斷「今天次數是否用完」。
    抽出來是因為這個判斷不只 furnace_loop 用得到——full_clear 模式也要
    問同一個問題，不要各自重複讀 daily_count/daily_limit 兩個欄位。

    回傳 True/False/None：抓不到次數資訊(這則回覆本來就沒有次數欄位)
    時回傳 None，呼叫端要自己決定「不知道」時該怎麼辦，不要當成 False。
    """
    daily_count = structured.get("daily_count")
    daily_limit = structured.get("daily_limit")
    if daily_count is None or daily_limit is None:
        return None
    return daily_count >= daily_limit


def decide(ctx):
    # 「連續討伐」的回覆是 world_boss_continuous_report(逐刀彙總格式)，
    # 不是單刀的 world_boss_battle_report——兩者完全是不同的訊息格式，
    # 但都有 daily_count/daily_limit 欄位，判斷邏輯可以共用。
    if ctx.shape not in ("world_boss_continuous_report", "world_boss_battle_report"):
        return None

    exhausted = is_daily_count_exhausted(ctx.structured)
    if exhausted is None or not exhausted:
        return None  # 還沒用完，或這則回覆本來就沒有次數資訊，不用做什麼

    boss_name = ctx.structured.get("boss_name")

    if boss_name is None:
        # 只有手動連續討伐剛好把王打死才會發生這種精簡格式(熊 2026-09-06
        # 說明：開始一定看過公告知道王名，只有討伐後沒有下一個動作的
        # 機會才會漏接)。不知道是哪隻王，直接查詢「世界王」——查詢回覆
        # 自然帶出王名，交給 decide_action_from_status_query() 接手判斷，
        # 不用自己猜。
        return actions.send_now(
            "世界王", chat_id=None,
            reason="連續討伐次數用完但抓不到王名，查詢確認狀態",
        )

    world_boss_progress.mark_count_exhausted(ctx.base_dir, ctx.account_id, boss_name)
    mode = world_boss_progress.get_mode(ctx.base_dir, ctx.account_id, boss_name)

    if mode is None:
        # 查不到這隻王的紀錄——最可能是「出現/查詢/戰報」三種訊息格式
        # 抓出來的王名剛好對不上(多一個符號、全形半形不同之類)。如果
        # /wbmode 是手動指定(不是 auto)，不需要靠這隻王的紀錄也能知道
        # 該用哪個模式，直接信任目前的開關設定，不要因為名字對不上就
        # 整個放棄；如果是 auto，沒有階數/懸賞資訊真的無法判斷，才送
        # 查詢讓 decide_action_from_status_query() 用它自己抓到的名字
        # 重新確認一次。
        override = world_boss_mode.get_override(ctx.base_dir, ctx.account_id)
        if override == world_boss_mode.FURNACE_LOOP:
            mode = world_boss_mode.FURNACE_LOOP
            print(f"[爐火模式] 「{boss_name}」查不到紀錄(可能是王名對不上)，"
                  f"但 /wbmode 手動指定 furnace_loop，直接信任開關設定")
        else:
            return actions.send_now(
                "世界王", chat_id=None,
                reason=f"「{boss_name}」次數用完但查不到模式紀錄，查詢確認狀態",
            )

    if mode != world_boss_mode.FURNACE_LOOP:
        return None  # 不是我負責的王(touch 或 full_clear，交給對應模組/流程)

    if world_boss_progress.is_furnace_completed(ctx.base_dir, ctx.account_id, boss_name):
        return None  # 已經重置過一次了，furnace_loop 定義上不追加，不再觸發

    remember_boss(ctx.base_dir, ctx.account_id, boss_name)
    daily_count = ctx.structured.get("daily_count")
    daily_limit = ctx.structured.get("daily_limit")
    return furnace_cycle_strategy.start(
        reason=f"「{boss_name}」今日 {daily_count}/{daily_limit} 次已用完，爐火模式自動爐火重置",
    )