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

=== 2026-09-13 修正：爐火完成後直接接手，不用再查一次「世界王」 ===
原本設計是爐火完成時送一次「世界王」查詢，靠查詢回覆判斷要不要恢復
攻擊——熊反映可以省掉這個往返，直接换手+連續討伐就好。改成：觸發爐火
重置的當下，順便記住這隻王的名字/base_dir/account_id/原始文字(見
remember_boss())；爐火真的完成時，直接用記住的文字重新判斷一次陣容
(弱點/類型有沒有變過，通常不會變，但换相這種情況有可能，多檢查一次
比較保險)，換手+連續討伐一次排完，不用查詢確認。

這個「記住最後一隻王」的快取，性質跟拿掉的 session 狀態不同：記錯/記到
舊資料的代價很小(頂多多送一次不必要的換手+攻擊，被伺服器擋下或無效果，
不會像「記住流程進行中」那樣造成錯誤的長期卡住)，所以可以放心用記憶體
快取，不需要额外持久化。

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

# 見檔頭「爐火完成後直接接手」說明：記住「最後一次是哪隻王觸發爐火
# 重置」，連同當時的原始文字(拿來重新判斷弱點/類型用)。
_last_boss_context = None  # {"base_dir","account_id","name","text"} 或 None


def remember_boss(base_dir, account_id, name, text):
    """觸發爐火重置前呼叫。world_boss_strategy.decide_action_from_
    status_query() 查詢路徑觸發爐火時也要呼叫這個，兩個觸發入口共用
    同一份記憶。

    text 只有在能被 WeaknessParser.parse() 解析出弱點資訊時才會更新
    快取——次數用完是從戰報偵測到的，戰報本身沒有「🔮 弱點屬性:」這種
    格式，如果照樣覆蓋，爐火完成時就會失去換手判斷的依據、退化成
    「直接攻擊不換手」。同一隻王的話，保留 start()/handle_phase_
    transition() 當初記下來、真正帶有弱點資訊的文字，比較有用。"""
    global _last_boss_context
    if (_last_boss_context and _last_boss_context.get("name") == name
            and _last_boss_context.get("text") and WeaknessParser.parse(text) is None):
        _last_boss_context = {**_last_boss_context, "base_dir": base_dir, "account_id": account_id}
        return
    _last_boss_context = {"base_dir": base_dir, "account_id": account_id, "name": name, "text": text}


def _build_resume_action(base_dir, account_id, text, reason):
    """重新判斷陣容(換手不將就)後組出換手+連續討伐的 plain dict，跟
    start() 共用同一套邏輯，只是 reason 文字不同。"""
    weakness = WeaknessParser.parse(text)
    if weakness is None:
        return {"mode": "now", "command": "連續討伐", "chat_id": None,
                "reason": f"{reason}（抓不到弱點資訊，直接連續討伐，陣容可能不是最佳）"}

    roster = load_roster(base_dir, account_id)
    rules = load_json(RULES_PATH)
    commands = TopSelector.decide_switch_commands(roster, weakness, rules)
    commands.append("連續討伐")

    if len(commands) == 1:
        return {"mode": "now", "command": commands[0], "chat_id": None,
                "reason": f"{reason}，陣容仍然合格，直接連續討伐"}
    return {
        "mode": "sequence", "commands": commands, "interval_seconds": SWITCH_DELAY_SECONDS,
        "chat_id": None,
        "reason": f"{reason}，先換手（{' → '.join(commands[:-1])}）再連續討伐",
    }


async def _on_furnace_complete():
    """furnace_cycle_strategy 的爐火流程結束時觸發(不管正常炸爆還是被
    搶先重置)。full_clear 次數用完一律自動重置，重置完成後這裡直接
    重新判斷陣容、恢復連續討伐，不用再查一次「世界王」(熊 2026-09-13
    要求省掉這個往返)。"""
    if _last_boss_context is None:
        return
    ctx = _last_boss_context
    mode = world_boss_progress.get_mode(ctx["base_dir"], ctx["account_id"], ctx["name"])
    if mode != world_boss_mode.FULL_CLEAR:
        return  # 不是這次爐火重置的觸發者(可能是 furnace_loop 觸發的)，不是我的事

    world_boss_progress.clear_count_exhausted(ctx["base_dir"], ctx["account_id"], ctx["name"])
    action_dict = _build_resume_action(
        ctx["base_dir"], ctx["account_id"], ctx["text"],
        reason=f"「{ctx['name']}」爐火重置完成",
    )
    if await actions.execute_dict(action_dict):
        print(f"[全程模式] ▶️ 「{ctx['name']}」爐火重置完成，直接恢復連續討伐（{action_dict['reason']}）")


furnace_cycle_strategy.on_session_end(_on_furnace_complete)


def start(text, base_dir, account_id, reason="全程連續討伐，先確認陣容"):
    """world_boss_strategy.py 的 decide_action()(公告路徑)判斷
    mode==full_clear 時呼叫。text 是偵測到這隻王的原始文字(出現公告)，
    用來取得弱點/類型資訊。回傳格式跟 decide_action() 本身一致(plain
    dict)。"""
    weakness = WeaknessParser.parse(text)
    if weakness is None:
        return {"mode": None, "command": None, "chat_id": None,
                "reason": "抓不到弱點屬性，無法判斷陣容，跳過這隻王"}

    if weakness.boss_name:
        # 這裡的 text 帶有完整弱點資訊，先記下來——之後次數用完只能從
        # 戰報偵測到(戰報沒有弱點格式)，remember_boss() 才不會被沒用的
        # 文字覆蓋掉，爐火完成時才有依據重新判斷陣容。
        remember_boss(base_dir, account_id, weakness.boss_name, text)

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

    if weakness.boss_name:
        remember_boss(base_dir, account_id, weakness.boss_name, text)

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

    if mode is None:
        # 查不到這隻王的紀錄——最可能是「出現/查詢/戰報」三種訊息格式
        # 抓出來的王名剛好對不上。/wbmode 手動指定時直接信任開關設定，
        # 不用因為名字對不上就整個放棄；auto 的話沒有階數/懸賞資訊真的
        # 無法判斷，才送查詢讓查詢回覆用它自己抓到的名字重新確認。
        override = world_boss_mode.get_override(ctx.base_dir, ctx.account_id)
        if override == world_boss_mode.FULL_CLEAR:
            mode = world_boss_mode.FULL_CLEAR
            print(f"[全程模式] 「{boss_name}」查不到紀錄(可能是王名對不上)，"
                  f"但 /wbmode 手動指定 full_clear，直接信任開關設定")
        else:
            return actions.send_now(
                "世界王", chat_id=None,
                reason=f"「{boss_name}」次數用完但查不到模式紀錄，查詢確認狀態",
            )

    if mode != world_boss_mode.FULL_CLEAR:
        return None  # 不是我負責的王

    remember_boss(ctx.base_dir, ctx.account_id, boss_name, ctx.text)
    daily_count = ctx.structured.get("daily_count")
    daily_limit = ctx.structured.get("daily_limit")
    return furnace_cycle_strategy.start(
        reason=f"「{boss_name}」今日 {daily_count}/{daily_limit} 次已用完，"
               "全程模式自動爐火重置（不設次數上限）",
    )