# -*- coding: utf-8 -*-
"""
furnace_loop_strategy.py —— 世界王「爐火」模式(furnace_loop)的上層流程

負責 world_boss_mode.FURNACE_LOOP 模式底下實際要做的事：
    王出現 → 檢查陣容(主手屬性合弱點/副手屬性能相生主手) → 不夠好就換手
    → 送出「連續討伐」→ 反應戰報，偵測次數用完 → 交給 furnace_cycle_strategy
    重置一次 → 結束(這個模式定義上不追到王死，重置完就停，跟 full_clear
    不同——見 world_boss_mode.py 的模式說明)

由 world_boss_strategy.py 在判斷 mode==furnace_loop 時呼叫 start()
啟動，之後靠 decide(ctx) 反應戰報接力；觀火/投爐那段完全交給
furnace_cycle_strategy.py，不重寫（熊 2026-09-04 明確要求）。

=== 陣容判斷標準(熊 2026-09-04 確認) ===
判斷「要不要換」只看屬性有沒有對上，不強求類型也剋制（「屬性對上、戰力
盡量高就可以」）；但真的要換的話，換手目標不將就，用
weakness_matcher.TopSelector.recommend_pair() 找「屬性+類型+戰力都最好」
的陀螺——门槛放寬，換的時候還是換最好的。
    主手：目前出戰的屬性 == 弱點屬性 就不用換
    副手：目前副手的屬性 == 相生主手所需屬性 就不用換
（副手的相生對象一律是「換完之後的主手屬性」，也就是 weakness.current_
element——不管主手要不要換，換完之後主手一定是這個屬性，判斷副手時
不用區分主手有沒有換，答案一樣。）

=== 連續討伐 ===
熊確認遊戲本身有「連續討伐」指令，伺服器端自己會連續出手，這支模組只送
一次「連續討伐」，不自己迴圈送「討伐」。跟前面換手指令的間隔可以極短，
這裡偷懶直接用跟換手同樣的 2 秒間隔送成一組序列，不用另外處理更短的
間隔（熊確認這樣沒問題）。

=== 怎麼判斷次數用完 ===
反應 world_boss_battle_report shape 的戰報(這支 shape 已經有
daily_count/daily_limit 欄位，不用新解析)：daily_count >= daily_limit
時，代表這一輪能打的都打完了，交給 furnace_cycle_strategy.start()。
「連續討伐」送出後可能收到 1 則或好幾則戰報(目前沒有實際樣本能確認，
但兩種情況這支程式都能正確處理——反正只在乎「最新一則戰報有沒有到達
上限」，不管中間收到幾則，不需要為了這個特地要一份樣本)。

如果戰報一直沒到上限、也沒有新戰報進來了(可能是王被打死，或連續討伐
提前結束)，這支模組不會主動做任何事——這個模式定義上就是「有次數就打，
沒次數就重置一次」，不是「一定要打到某個結果」，安靜結束是合理行為。

唯一的例外是「換相」：王在連續討伐途中換相會被硬直卡住，次數還沒用完
但這一輪指令提前中止，不是「次數用完」，不能誤判去啟動爐火重置。
world_boss_strategy.py 偵測到 phase_transition 事件時，如果這支模組正在
進行中(is_active())，會呼叫 handle_phase_transition() 而不是走它自己
原本 touch 模式的判斷，讓 session 續命、硬直過後重送一次「連續討伐」
（熊 2026-09-06 反映；「換相會不會帶階數/懸賞資訊」目前沒有實際樣本
確認，不影響這裡的處理——這裡只在乎「續不續攻擊」，不需要那些欄位）。

=== 安全閥 ===
跟 furnace_cycle_strategy.py 同樣的邏輯：用 runtime_state 的逾時旗標
避免萬一「連續討伐」的戰報格式跟預期不同、卡住沒觸發 furnace_cycle，
導致 session 旗標永遠卡在「進行中」擋住下一隻王的判斷。

=== 護衛衝突(不在這支檔案裡處理) ===
2026-09-06 起，「王出現時剛好有護衛，會跟清護衛搶換手」這個問題的排隊/
接續機制搬到 world_boss_strategy.py 裡統一處理(因為 touch 模式也會撞到
同樣的問題，不是 furnace_loop 專屬的)。這支檔案不再持有排隊狀態，也不再
匯入 guard_clear_strategy。
"""
import time

import auto_toggle
from main_tower_advisor import load_json, RULES_PATH
from roster_loader import load_roster
from weakness_matcher import WeaknessParser, TopSelector
from triggers import actions
from triggers import furnace_cycle_strategy
from triggers import runtime_state

SYSTEM_KEY = auto_toggle.WORLD_BOSS

# 換手/連續討伐這組序列的間隔，熊確認的實測值。
SWITCH_DELAY_SECONDS = 2.0

SESSION_TIMEOUT_SECONDS = 20 * 60
_SESSION_STATE_KEY = "furnace_loop_active"


def is_active() -> bool:
    return runtime_state.is_active(_SESSION_STATE_KEY, None)


def _mark_active():
    runtime_state.set_until(_SESSION_STATE_KEY, None, time.time() + SESSION_TIMEOUT_SECONDS)


def _clear():
    runtime_state.clear(_SESSION_STATE_KEY, None)


def _decide_switch_commands(roster, weakness, rules):
    """回傳需要送出的換手指令列表(可能是空列表)。純函式，方便測試，不做
    任何 I/O，也不呼叫 executor/scheduler。"""
    current_main = next((t for t in roster if t.get("status") == "出戰"), None)
    current_sub = next((t for t in roster if t.get("status") == "副陀螺"), None)

    main_pick, sub_pick = TopSelector.recommend_pair(roster, weakness, rules, boss_type=weakness.boss_type)

    commands = []

    main_ok = bool(current_main) and current_main.get("element") == weakness.current_element
    if not main_ok and main_pick and current_main is not main_pick:
        commands.append(f"出戰 {main_pick.get('index')}")

    # 副手要相生的對象一律是 weakness.current_element：main_ok 時目前主手
    # 本來就已經是這個屬性；main_ok 為 False 時換完之後的主手也會是這個
    # 屬性——兩種情況答案相同，不用分支各算一次。
    generating_element = None
    for src, dst in rules.get("element_generate", {}).items():
        if dst == weakness.current_element:
            generating_element = src
            break

    sub_ok = bool(current_sub) and generating_element and current_sub.get("element") == generating_element
    if not sub_ok and sub_pick and current_sub is not sub_pick:
        # 2026-09-05 修正：指令是「副手 {編號}」，不是「副陀螺 {編號}」——
        # 「副陀螺」是遊戲回覆訊息裡用來「稱呼」這個欄位的名詞(顯示用)，
        # 不是實際可以送出的指令字串，兩者長得像但不一樣，之前搞混了。
        # 確認依據：config/aliases.json 的「備戰」別名定義 ["出戰 {1}", "副手 {2}"]。
        commands.append(f"副手 {sub_pick.get('index')}")

    return commands


def start(text, base_dir, account_id, reason="次數還沒用完，先確認陣容再連續討伐"):
    """world_boss_strategy.py 的 decide_action()(公告路徑)判斷
    mode==furnace_loop 時呼叫。text 是偵測到這隻王的原始文字(出現公告)，
    用來取得弱點/類型資訊。

    回傳格式刻意跟 decide_action() 本身一致(plain dict，mode 是
    "now"/"sequence"/None)，不是 triggers.actions.Action 物件——
    因為呼叫端(world_boss_strategy.decide_action())本身就是走公告路徑的
    plain-dict 介面，被 action_dispatcher.py 的 _handle_announcement()
    同步消費，不能回傳這支模組另一半(decide())用的 Action 物件。
    "sequence" 是 2026-09-04 為了這裡需要「先換手再攻擊」新加進
    action_dispatcher.py 的 mode，見該檔案的說明。
    """
    weakness = WeaknessParser.parse(text)
    if weakness is None:
        return {"mode": None, "command": None, "chat_id": None,
                "reason": "抓不到弱點屬性，無法判斷陣容，跳過這隻王"}

    roster = load_roster(base_dir, account_id)
    rules = load_json(RULES_PATH)

    commands = _decide_switch_commands(roster, weakness, rules)
    commands.append("連續討伐")

    _mark_active()

    if len(commands) == 1:
        return {"mode": "now", "command": commands[0], "chat_id": None,
                "reason": f"{reason}，陣容已經合格，直接連續討伐"}

    return {
        "mode": "sequence", "commands": commands, "interval_seconds": SWITCH_DELAY_SECONDS,
        "chat_id": None,
        "reason": f"{reason}，先換手（{' → '.join(commands[:-1])}）再連續討伐",
    }


def handle_phase_transition(delay_seconds):
    """world_boss_strategy.py 偵測到 phase_transition 事件、且這支模組
    正在進行中(is_active())時呼叫。代表王在連續討伐途中換相，被硬直卡住
    ——次數還沒用完，只是這一輪指令提前中止，不是「次數用完」，不能誤判
    去啟動爐火重置。延長 session 逾時，回傳延遲後重送「連續討伐」的
    plain dict(跟 start() 的回傳格式一致，呼叫端一樣用 actions.
    execute_dict() 處理)。"""
    _mark_active()
    return {
        "mode": "scheduled", "delay_seconds": delay_seconds, "command": "連續討伐", "chat_id": None,
        "reason": f"世界王換相，硬直 {delay_seconds} 秒後繼續連續討伐（次數還沒用完）",
    }


def decide(ctx):
    if not is_active():
        return None
    # 「連續討伐」的回覆是 world_boss_continuous_report(逐刀彙總格式)，
    # 不是單刀的 world_boss_battle_report——2026-09-05 用實際樣本發現
    # 兩者完全是不同的訊息格式，之前只認單刀那個 shape，導致「連續討伐」
    # 送出後這支模組完全收不到反應，卡在爐火流程進不去。兩個 shape 都有
    # daily_count/daily_limit 欄位，判斷邏輯可以共用，不用分別處理。
    if ctx.shape not in ("world_boss_continuous_report", "world_boss_battle_report"):
        return None

    parsed = ctx.structured
    daily_count = parsed.get("daily_count")
    daily_limit = parsed.get("daily_limit")

    if daily_count is None or daily_limit is None:
        return None  # 這則戰報沒有次數資訊，不是我們能判斷的訊號，安靜放行

    if daily_count < daily_limit:
        _mark_active()  # 還有進度，延長逾時，等後續戰報
        return actions.none(log=f"[爐火模式] 今日 {daily_count}/{daily_limit} 次，還沒用完，繼續等後續戰報")

    _clear()
    return furnace_cycle_strategy.start(reason=f"今日 {daily_count}/{daily_limit} 次已用完，開始爐火重置")