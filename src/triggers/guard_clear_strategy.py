# -*- coding: utf-8 -*-
"""
guard_clear_strategy.py —— 清護衛自動化決策層

負責兩個決策點：
    decide_action()       收到「還被 N/M 顆環繞」查詢結果時，決定要不要
                           直接打清護衛，還是先換陀螺再重新查詢
    decide_after_outcome() 收到「一擊拆除!」或戰鬥「🏆 勝利!」結果時，
                           決定要不要繼續查下一顆

=== 設計：不需要額外狀態，天然形成迴圈 ===
兩個函式都是無狀態的（純函式，不記錄「已經試過幾次」這類東西）——
迴圈本身是靠「決定要送什麼指令」自然接起來的：

    收到查詢結果 → decide_action() 判斷 → 送出「清護衛」或「出戰 N」+「護衛」
                                                              ↓
                                                    這個「護衛」指令會讓
                                                    伺服器再回一則查詢結果，
                                                    重新觸發 decide_action()
    收到結果訊息 → decide_after_outcome() 判斷 → 還有剩就送「護衛」重新查
                                                    → 一樣會重新觸發上面那條

不用擔心無限迴圈：如果 roster 裡找不到能完美剋制的陀螺，decide_action()
回傳 mode="none"，不送出任何指令，迴圈在這裡自然停止（不是靠計數器擋，
是靠「沒有動作可送」這件事本身停止）。護衛數量通常不多（10 顆以下），
熊確認不用特別做防護。

戰鬥模式（sh沒一擊拆掉、進入按鈕戰鬥）不在這裡決策——那是
guard_battle_prompt.py 解析出來的訊息，直接沿用
main_tower_battle_strategy.decide_action()，只是傳更保守的門檻參數
（見 GUARD_CRITICAL_HP_RATIO / GUARD_SHIELD_PHASE_THRESHOLD），呼叫端
（action_dispatcher.py）負責接線，不在這支檔案裡。

=== 已知限制 ===
- 戰敗（battle_victory 的對應失敗情況）目前沒有樣本，decide_after_outcome()
  只認得 remaining/cleared_all 這兩個成功情境的欄位，戰敗訊息目前不會被
  guard_clear_outcome.py 判斷成任何已知 shape，會 fallback 顯示原文，
  迴圈會卡住不繼續（不會出錯，只是不會自動接著查下一顆，需要熊手動處理）。
  等有戰敗樣本再補。
- 換陀螺跟送出「清護衛」之間有極短空檔，如果剛好這段期間護衛重新增生
  換了屬性，可能打到不完美剋制的護衛、進入戰鬥模式——這不是 bug，是
  遊戲機制本身的限制，戰鬥模式本來就能處理，只是多打一場。
"""

from query_reactor import recommend_for_guard_target
from triggers import actions
from triggers import main_tower_battle_strategy

SYSTEM_KEY = "guard_clear"

# 護衛戰鬥可能是不利對局（見 main_tower_battle_strategy.py 的說明），
# 門檻比 mtb 更保守——這是拍腦袋的起始值，不是遊戲內建數字，熊實戰觀察
# 覺得不準直接改這裡即可。
GUARD_CRITICAL_HP_RATIO = 0.25
GUARD_SHIELD_PHASE_THRESHOLD = 300


def _score_top(top, next_target):
    """兩項都符合 -> 2（一擊拆掉），符合一項 -> 1，都不符合 -> 0。
    跟 query_reactor.recommend_for_guard_target() 內部算分邏輯一致。"""
    score = 0
    if top.get("element") == next_target.get("weak_element"):
        score += 1
    if top.get("type") == next_target.get("weak_type"):
        score += 1
    return score


def decide_action(parsed, roster):
    """guard_status.py 解析出來的查詢結果（type="active"）進來時呼叫。

    回傳 {"mode": "attack"|"switch_and_requery"|"none", "commands": [...], "reason": str}
    mode="none" 時 commands 是空的，呼叫端不應該送出任何指令。
    """
    structured = parsed.get("structured") or {}
    if structured.get("type") != "active":
        return None  # 不是「還有護衛」的查詢結果（可能是已散去通知），不歸這裡管

    next_target = structured.get("next_target")
    if not next_target:
        return {"mode": "none", "commands": [], "reason": "查詢結果沒有下一顆的弱點資訊，無法判斷"}

    if not roster:
        return {"mode": "none", "commands": [], "reason": "沒有 roster 資料（tops.json 不存在或尚未查過陀螺收藏），無法判斷"}

    active_top = next((t for t in roster if t.get("status") == "active"), None)
    if active_top is None:
        return {"mode": "none", "commands": [], "reason": "roster 裡找不到目前出戰的陀螺，無法判斷"}

    if _score_top(active_top, next_target) == 2:
        return {
            "mode": "attack",
            "commands": ["清護衛"],
            "reason": f"目前出戰「{active_top.get('name')}」完美剋制下一顆護衛，自動打清護衛",
        }

    best = recommend_for_guard_target(next_target, roster, top_n=1)
    if not best or best[0]["_score"] != 2:
        return {
            "mode": "none",
            "commands": [],
            "reason": "手上沒有完美剋制下一顆護衛的陀螺，僅顯示建議，不自動出手",
        }

    return {
        "mode": "switch_and_requery",
        "commands": [f"出戰 {best[0]['index']}", "護衛"],
        "reason": f"切換為「{best[0]['name']}」（完美剋制下一顆護衛），切換後重新查詢確認",
    }


def decide_after_outcome(parsed):
    """guard_clear_outcome.py 解析出來的結果訊息（一擊拆除 / 戰鬥勝利）進來時呼叫。

    回傳 {"mode": "requery"|"none", "commands": [...], "reason": str}
    """
    structured = parsed.get("structured") or {}

    if structured.get("cleared_all"):
        return {"mode": "none", "commands": [], "reason": "護衛已全數清空，清護衛迴圈結束"}

    remaining = structured.get("remaining")
    if remaining is not None and remaining > 0:
        return {
            "mode": "requery",
            "commands": ["護衛"],
            "reason": f"還剩 {remaining} 顆，重新查詢繼續清",
        }

    return {"mode": "none", "commands": [], "reason": "結果訊息沒有剩餘數量資訊，無法判斷是否繼續，交給你手動查看"}


def decide(ctx):
    """action_dispatcher.py 的統一觸發清單入口，取代原本 _handle_guard_clear()。
    三種 shape 的判斷邏輯本身沒有變，只是把「這則訊息歸不歸我管」的判斷
    搬進來，跟原本散在 action_dispatcher.py 裡的行為完全一致（包括每個
    分支各自的 stop 語意）。"""
    shape = ctx.shape
    if shape not in ("guard_status", "guard_clear_outcome", "guard_battle_prompt"):
        return None

    if not ctx.is_enabled(SYSTEM_KEY):
        return None  # 關閉時不吃掉訊息，維持原行為（放行給其他 trigger／reaction_rules）

    if shape == "guard_status":
        action = decide_action(ctx.parsed, ctx.roster)
        if action is None:
            return None  # 不是「還有護衛」的查詢結果，交給其他 trigger
        if action["mode"] == "none":
            return actions.none(log=f"[清護衛] {action['reason']}")
        return actions.send_sequence(
            action["commands"], interval_seconds=2, reason=action["reason"],
            log=f"[清護衛] ✅ {action['reason']}",
        )

    if shape == "guard_clear_outcome":
        action = decide_after_outcome(ctx.parsed)
        if action["mode"] == "none":
            return actions.none(log=f"[清護衛] {action['reason']}")
        return actions.send_now(
            action["commands"][0], reason=action["reason"],
            log=f"[清護衛] 🔁 {action['reason']}",
        )

    # shape == "guard_battle_prompt"：沒一擊拆掉，進入按鈕戰鬥，沿用主塔
    # 戰鬥的決策邏輯，但門檻更保守（見檔頭 GUARD_CRITICAL_HP_RATIO 等說明）。
    if not ctx.buttons:
        return None  # 原行為：沒按鈕就不吃掉，放行

    action = main_tower_battle_strategy.decide_action(
        ctx.structured, ctx.buttons,
        critical_hp_ratio=GUARD_CRITICAL_HP_RATIO,
        shield_phase_threshold=GUARD_SHIELD_PHASE_THRESHOLD,
    )
    if action:
        return actions.click_button(
            chat_id=ctx.chat_id, message_id=ctx.message_id,
            data=action["data"], button_text=action["button_text"], reason=action["reason"],
        )

    return actions.none(
        log=f"[護衛戰鬥] ⚠️ 策略無法判斷要選哪個戰術按鈕：{ctx.text[:40]}...",
        stop=True,
    )