# -*- coding: utf-8 -*-
"""
max_attack_strategy.py —— 世界王「打到次數用完」模式(max_attack)的上層流程

負責 world_boss_mode.MAX_ATTACK 模式底下實際要做的事：
    王出現 → 檢查陣容(跟 furnace_loop/full_clear 同一套判斷) → 不夠好就
    換手 → 送出「連續討伐」→ 結束。

跟 furnace_loop/full_clear 的差別：這個模式完全不碰爐火——次數用完就
結束，不會觸發觀火/投爐，也不需要反應戰報去判斷「要不要重置」。是三個
進階模式裡最單純的一個，沒有「次數用完後還要做什麼」這件事。

正因為這樣，這支模組沒有 decide()——不需要反應戰報，「連續討伐」送出去
之後，不管次數有沒有用完，這個模式的工作就已經結束了，沒有後續要接手
的事情。跟 furnace_loop_strategy.py／full_clear_strategy.py 不同，這是
唯一的結構差異。

=== 陣容判斷標準 ===
跟 furnace_loop/full_clear 用同一套：weakness_matcher.TopSelector.
decide_switch_commands()，不重寫。

=== 換相 ===
王在連續討伐途中換相會被硬直卡住，次數還沒用完，不是「已經打完了」，
重新判斷陣容後排程恢復連續討伐，邏輯跟 furnace_loop/full_clear 的
handle_phase_transition() 一致。

=== 目前只能手動指定 ===
2026-09-13 新增時，還沒掛進 world_boss_mode.py 的 2~4 階/懸賞那組自動
條件判斷，只能用 /wbmode max_attack 手動指定。之後如果要讓它也能被
auto 條件選到，要去改 world_boss_mode.determine_mode()，不在這支檔案。
"""
from main_tower_advisor import load_json, RULES_PATH
from roster_loader import load_roster
from weakness_matcher import WeaknessParser, TopSelector

SWITCH_DELAY_SECONDS = 2.0


def start(text, base_dir, account_id, reason="打到次數用完，先確認陣容再連續討伐"):
    """world_boss_strategy.py 的 decide_action()(公告路徑)判斷
    mode==max_attack 時呼叫。text 是偵測到這隻王的原始文字(出現公告)，
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
    換相會被硬直卡住，次數還沒用完，不是「已經打完了」，重新判斷陣容後
    排程恢復連續討伐（換相後弱點/類型可能整個換掉）。"""
    weakness = WeaknessParser.parse(text)
    if weakness is None:
        return {
            "mode": "scheduled", "delay_seconds": delay_seconds, "steps": ["連續討伐"],
            "interval": (SWITCH_DELAY_SECONDS, SWITCH_DELAY_SECONDS), "chat_id": None,
            "reason": f"世界王換相，硬直 {delay_seconds} 秒後繼續連續討伐"
                      "（打到次數用完模式，換相文字抓不到新弱點，陣容可能不是最佳）",
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
                  f"硬直 {delay_seconds} 秒後{switch_note}繼續連續討伐（打到次數用完模式）",
    }
