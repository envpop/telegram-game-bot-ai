# -*- coding: utf-8 -*-
"""
full_clear_strategy.py —— 世界王「全程」模式(full_clear)的上層流程

負責 world_boss_mode.FULL_CLEAR 模式底下實際要做的事：
    王出現 → 檢查陣容(跟 furnace_loop 同一套判斷) → 不夠好就換手 → 連續討伐
    → 反應戰報，次數用完就爐火重置 → 重置完繼續連續討伐 → 重複直到王死
    (熊 2026-09-06 確認：不設重置次數上限，只有 furnace_cycle_strategy
    每輪自己的 20 分鐘逾時當安全閥)

跟 furnace_loop 最大的差別：furnace_loop 打完一輪+爐火一次就停，
full_clear 打到死為止，次數用完不會停，會一直重置+繼續打。除此之外
(換手判斷、連續討伐、換相重判陣容)兩者邏輯一致，程式結構刻意保持相似，
方便對照維護——沒有硬要抽成共用模組，是因為「次數用完後要不要繼續」
這個分歧點如果硬塞共用函式，之後兩邊各自演化容易变得比分開寫更難懂，
只把真正共用、不會因模式而異的部分(換手判斷本身)抽出去(見
weakness_matcher.TopSelector.decide_switch_commands())。

=== 2026-09-06 大改版：拿掉「流程進行中」的 session 狀態 ===
原本用 is_active()/_last_context 記錄「full_clear 是不是正在跑」，熊
指出這種「記住自己在做什麼」的設計，斷線重連後容易變成過時的錯誤記憶，
要求整個拿掉，改成「不確定就查當下的紀錄／查詢遊戲」。現在完全靠
world_boss_progress.py 的持久記錄(mode/count_exhausted)判斷，不需要
任何 session 旗標：
    - 次數用完：查表看這隻王的 mode 是不是 full_clear，是的話直接觸發
      爐火重置(不像 furnace_loop 要看 furnace_completed，full_clear
      不設重置次數上限，一律觸發)。
    - 找不到王名：送「世界王」查詢，交給查詢回覆處理，不用自己猜。
    - 爐火重置完成後要恢復攻擊：也是靠查詢——furnace_cycle_strategy
      完成時會觸發一次「世界王」查詢(見 world_boss_strategy.py 的
      _query_after_furnace_reset())，查詢回覆看到「之前記錄用完、現在
      卻沒用完」就知道是重置完成，full_clear 的話會自動送出「連續討伐」
      恢復攻擊——這段邏輯統一寫在 world_boss_strategy.
      decide_action_from_status_query() 裡，這支檔案不用另外處理。

=== 王死亡偵測 ===
world_boss_progress 跨日重置時會自然清空這隻王的記錄；「王死了之後不會
再有戰報/查詢顯示這隻王的次數資訊」本身就是自然的終止訊號，不需要另外
反應 boss_defeated 公告或維護 session 讓它「結束」——沒有 session 可以
結束，也就不需要專門的結束動作。
"""
import world_boss_mode
import world_boss_progress
from main_tower_advisor import load_json, RULES_PATH
from roster_loader import load_roster
from weakness_matcher import WeaknessParser, TopSelector
from triggers import actions
from triggers import furnace_cycle_strategy
from triggers.furnace_loop_strategy import is_daily_count_exhausted

SWITCH_DELAY_SECONDS = 2.0


def start(text, base_dir, account_id, reason="全程連續討伐，先確認陣容"):
    """world_boss_strategy.py 的 decide_action()(公告路徑)判斷
    mode==full_clear 時呼叫。text 是偵測到這隻王的原始文字(出現公告)，
    用來取得弱點/類型資訊。回傳格式跟 decide_action() 本身一致(plain
    dict)，理由跟 furnace_loop_strategy.start() 相同。"""
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
    """跟 furnace_loop_strategy.handle_phase_transition() 邏輯一致——
    換相會被硬直卡住，次數還沒用完，不是「王死了」也不是「次數用完」，
    重新判斷陣容後排程恢復連續討伐（換相後弱點/類型可能整個換掉）。"""
    weakness = WeaknessParser.parse(text)
    if weakness is None:
        return {
            "mode": "scheduled", "delay_seconds": delay_seconds, "steps": ["連續討伐"],
            "interval": (SWITCH_DELAY_SECONDS, SWITCH_DELAY_SECONDS), "chat_id": None,
            "reason": f"世界王換相，硬直 {delay_seconds} 秒後繼續連續討伐"
                      "（全程模式，換相文字抓不到新弱點，陣容可能不是最佳）",
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
                  f"硬直 {delay_seconds} 秒後{switch_note}繼續連續討伐（全程模式）",
    }


def decide(ctx):
    if ctx.shape not in ("world_boss_continuous_report", "world_boss_battle_report"):
        return None

    exhausted = is_daily_count_exhausted(ctx.structured)
    if exhausted is None or not exhausted:
        return None

    boss_name = ctx.structured.get("boss_name")

    if boss_name is None:
        # 見 furnace_loop_strategy.decide() 同樣的說明：只有手動連續討伐
        # 剛好把王打死才會發生，查詢確認，不用自己猜。
        return actions.send_now(
            "世界王", chat_id=None,
            reason="連續討伐次數用完但抓不到王名，查詢確認狀態",
        )

    world_boss_progress.mark_count_exhausted(ctx.base_dir, ctx.account_id, boss_name)
    mode = world_boss_progress.get_mode(ctx.base_dir, ctx.account_id, boss_name)

    if mode != world_boss_mode.FULL_CLEAR:
        return None  # 不是我負責的王

    daily_count = ctx.structured.get("daily_count")
    daily_limit = ctx.structured.get("daily_limit")
    return furnace_cycle_strategy.start(
        reason=f"「{boss_name}」今日 {daily_count}/{daily_limit} 次已用完，"
               "全程模式自動爐火重置（不設次數上限）",
    )