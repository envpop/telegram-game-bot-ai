# -*- coding: utf-8 -*-
"""
full_clear_strategy.py —— 世界王「全程」模式(full_clear)的上層流程

負責 world_boss_mode.FULL_CLEAR 模式底下實際要做的事：
    王出現 → 檢查陣容(跟 furnace_loop 同一套判斷) → 不夠好就換手 → 連續討伐
    → 反應戰報，次數用完就爐火重置 → 重置完繼續連續討伐 → 重複直到王死
    (熊 2026-09-06 確認：不設重置次數上限，只靠 furnace_cycle_strategy
    每輪自己的 20 分鐘逾時當安全閥)

跟 furnace_loop 最大的差別：furnace_loop 打完一輪+爐火一次就停，
full_clear 打到死為止，次數用完不會停，會一直重置+繼續打。除此之外
(換手判斷、連續討伐、換相重判陣容)兩者邏輯一致，程式結構刻意保持相似，
方便對照維護——沒有硬要抽成共用模組，是因為「次數用完後要不要繼續」
這個分歧點如果硬塞共用函式，之後兩邊各自演化容易变得比分開寫更難懂，
只把真正共用、不會因模式而異的部分(換手判斷本身)抽出去(見
weakness_matcher.TopSelector.decide_switch_commands())。

=== 次數用完時的行為(熊 2026-09-06 確認) ===
不遵守 FURNACE_AUTO 開關——跟 furnace_loop 不一樣，full_clear 次數用完
一律自動觸發爐火重置，不會因為 FURNACE_AUTO 關閉就停下來等手動。理由：
選了 full_clear 就代表要一路打到死，沒有「先觀望」的選項，跟 furnace_
loop(中等投入，可以先手動確認)的定位不同。

爐火重置完成(furnace_cycle_strategy 的 on_session_end 回呼觸發)後，
自動送出「連續討伐」繼續打，不像 furnace_loop 那樣重置完就結束。

=== 王死亡偵測(熊 2026-09-06 確認) ===
反應 world_boss_catalog.json 裡本來就有、但目前沒有行為的 boss_defeated
事件(「被討伐!」)。world_boss_strategy.py 偵測到這個事件時，如果
full_clear_strategy 正在進行中(is_active())，呼叫 on_boss_defeated()
結束 session。沒有其他判斷方式(熊確認先用這個就好，之後如果發現這個
公告有漏接的情況，再另外處理)。

=== 安全閥 ===
不設重置次數上限(熊確認)。session 本身還是有 SESSION_TIMEOUT_SECONDS
逾時保險，避免萬一戰報/爐火格式跟預期不同、卡住沒觸發任何後續動作時，
session 永遠卡在「進行中」擋住下一隻王的判斷。

已知限制：如果 furnace_cycle_strategy 那一輪爐火流程是撞到它自己的
20 分鐘逾時而不是正常結束(卡住、沒有走到 explode/blocked 那兩個結束
點)，不會呼叫 on_session_end() 的回呼，full_clear 這邊就不會收到「可以
繼續打了」的通知，只能等自己的 SESSION_TIMEOUT_SECONDS 逾時放棄。這種
情況需要熊手動介入，目前沒有更好的偵測方式。
"""
import time

import auto_toggle
from main_tower_advisor import load_json, RULES_PATH
from roster_loader import load_roster
from weakness_matcher import WeaknessParser, TopSelector
from triggers import actions
from triggers import furnace_cycle_strategy
from triggers import runtime_state
from triggers.furnace_loop_strategy import is_daily_count_exhausted

SYSTEM_KEY = auto_toggle.WORLD_BOSS

SWITCH_DELAY_SECONDS = 2.0

SESSION_TIMEOUT_SECONDS = 20 * 60
_SESSION_STATE_KEY = "full_clear_active"

# 爐火重置完成後恢復攻擊時只需要 base_dir/account_id(這支 bot 一次只服務
# 一個帳號，弱點沒變，只是次數補回來了，不需要重新解析弱點文字)。
_last_context = None  # {"base_dir","account_id"} 或 None


def is_active() -> bool:
    return runtime_state.is_active(_SESSION_STATE_KEY, None)


def _mark_active():
    runtime_state.set_until(_SESSION_STATE_KEY, None, time.time() + SESSION_TIMEOUT_SECONDS)


def _clear():
    global _last_context
    _last_context = None
    runtime_state.clear(_SESSION_STATE_KEY, None)


def start(text, base_dir, account_id, reason="全程連續討伐，先確認陣容"):
    """world_boss_strategy.py 的 decide_action()(公告路徑)判斷
    mode==full_clear 時呼叫。text 是偵測到這隻王的原始文字(出現公告)，
    用來取得弱點/類型資訊。回傳格式跟 decide_action() 本身一致(plain
    dict)，理由跟 furnace_loop_strategy.start() 相同。"""
    global _last_context

    weakness = WeaknessParser.parse(text)
    if weakness is None:
        return {"mode": None, "command": None, "chat_id": None,
                "reason": "抓不到弱點屬性，無法判斷陣容，跳過這隻王"}

    roster = load_roster(base_dir, account_id)
    rules = load_json(RULES_PATH)
    commands = TopSelector.decide_switch_commands(roster, weakness, rules)
    commands.append("連續討伐")

    _mark_active()
    _last_context = {"base_dir": base_dir, "account_id": account_id}

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
    _mark_active()

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


def on_boss_defeated():
    """world_boss_strategy.py 偵測到 boss_defeated 公告時呼叫。不影響
    furnace_cycle_strategy 的 session(那個由它自己的逾時/callback管理)，
    這裡只結束 full_clear 自己的 session。"""
    if not is_active():
        return  # 不是 full_clear 在處理的王，跟我無關
    _clear()
    print("[全程模式] ✅ 王被討伐了，流程結束")


async def _resume_after_furnace_reset():
    """furnace_cycle_strategy 的爐火流程結束時(不管是正常炸爆還是被
    搶先重置)透過 on_session_end() 回呼觸發。full_clear 次數用完時一律
    自動重置(不受 FURNACE_AUTO 影響，熊 2026-09-06 確認)，重置完成後
    這裡負責恢復攻擊。"""
    if not is_active() or _last_context is None:
        return  # 不是 full_clear 觸發的這次爐火重置，跟我無關
    ctx = _last_context
    _mark_active()  # 還在繼續打，延長逾時
    action_dict = {"mode": "now", "command": "連續討伐", "chat_id": None,
                   "reason": "爐火重置完成，全程模式繼續連續討伐"}
    if await actions.execute_dict(action_dict):
        print("[全程模式] ▶️ 爐火重置完成，繼續連續討伐")


furnace_cycle_strategy.on_session_end(_resume_after_furnace_reset)


def decide(ctx):
    if not is_active():
        return None
    if ctx.shape not in ("world_boss_continuous_report", "world_boss_battle_report"):
        return None

    exhausted = is_daily_count_exhausted(ctx.structured)
    if exhausted is None:
        return None  # 這則戰報沒有次數資訊，不是我們能判斷的訊號，安靜放行

    daily_count = ctx.structured.get("daily_count")
    daily_limit = ctx.structured.get("daily_limit")

    if not exhausted:
        _mark_active()  # 還有進度，延長逾時，等後續戰報(或換相/王死公告)
        return actions.none(log=f"[全程模式] 今日 {daily_count}/{daily_limit} 次，還沒用完，繼續等後續戰報")

    _mark_active()  # 延長逾時，等爐火重置跑完
    return furnace_cycle_strategy.start(
        reason=f"今日 {daily_count}/{daily_limit} 次已用完，全程模式自動爐火重置（不受 FURNACE_AUTO 影響）",
    )
