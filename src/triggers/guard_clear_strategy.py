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
迴圈本身是靠「決定要送什麼指令」自然接起來的，你只需要手動觸發第一次
「護衛」查詢，之後全自動跑到清空為止：

    護衛狀態（第一次，手動觸發）
        │（不冷卻，立即送出）
        ▼
    decide_action() 判斷 → 出戰 N + 護衛（換陀螺剋制下一顆）
        │（不冷卻，立即送出）
        ▼
    看護衛（換陀螺後重新查詢，這次會剋制）
        │（不冷卻，立即送出）
        ▼
    decide_action() 判斷 → 清護衛
        │（冷卻 GUARD_CLEAR_COOLDOWN_SECONDS 秒，見下方常數說明）
        ▼
    decide_after_outcome() 判斷還有剩 → 護衛（重新查詢）
        │
        └──▶ 回到「decide_action() 判斷」那一步，繼續下一顆，LOOP

熊 2026-08-22 反映：全自動連續清護衛時，「清護衛」送出攻擊指令後如果
立刻重新查詢，會撞到伺服器冷卻——這是整條鏈路裡唯一需要刻意等待的
轉折點，其他轉折（查詢後換陀螺、換陀螺後查詢、查詢後清護衛）目前沒有
冷卻問題，維持立即送出。

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

=== 2026-08-27 修正：兩個會卡住/無限循環的情況 ===
1. 「無法判斷目前出戰陀螺屬性」會卡住：新陀螺剛更新、圖鑑（roster）
   還沒帶上正確的 element/type 時，_score_top() 拿 None 去跟弱點屬性
   比對，靜靜算成「不符合」，跟「真的不剋制」長得一模一樣，沒辦法分辨。
   現在改成明確檢查 element/type 是不是 None，是的話直接回傳
   mode="none" 並在 reason 講清楚「屬性未知」，不會跟「真的沒有剋制
   陀螺」搞混，也不會拿不完整的資料硬做判斷。

2. 「roster 沒更新會整個亂掉、無限循環」：decide_action() 原本每次都
   相信 roster 裡 status=="active" 的欄位，但這個欄位只有查一次「我的
   陀螺」才會更新，清護衛過程中自動送出的「出戰 N」不會回頭更新它。
   結果是：只要清護衛期間換過一次陀螺，roster 記錄的「目前出戰」就
   永遠停在換陀螺前那一隻，之後每一輪都誤判成「還沒換到剋制陀螺」，
   重複送出同一個切換指令，形成無限循環。

   修法：模組層級記住「這一輪清護衛期間，我們自己切換到了哪一隻」
   （_assumed_active_index），之後判斷「目前出戰陀螺」時優先採信這個
   假設值，不是每次都重新相信 roster 的 status 欄位。這代表這兩個函式
   不再是完全無狀態的純函式（檔頭原本這樣寫），但狀態只在單一 process
   內、單一清護衛 session 內有意義，護衛全部清空（cleared_all）時會
   重置，不會跨 session 殘留、也不需要跨帳號區分（這支 bot 一次只服務
   一個帳號，見 telegram_client.py 的帳號切換機制）。
"""

import auto_toggle
from query_reactor import recommend_for_guard_target
from triggers import actions
from triggers import main_tower_battle_strategy

SYSTEM_KEY = auto_toggle.GUARD_CLEAR

# 「清護衛」送出攻擊指令後，伺服器需要一段冷卻才能再查詢——熊 2026-08-22
# 反映全自動連續清護衛時，攻擊後立刻重新查詢會撞到冷卻。這是實測觀察值，
# 不是遊戲公告的數字，如果之後發現還是偶爾撞到，直接調大這個常數即可。
# 其他轉折（護衛狀態→換陀螺→重新查詢、查詢→清護衛）目前沒有回報冷卻
# 問題，維持原本立即送出，不用跟著加等待。
GUARD_CLEAR_COOLDOWN_SECONDS = 1.5

# 護衛戰鬥可能是不利對局（見 main_tower_battle_strategy.py 的說明），
# 門檻比 mtb 更保守——這是拍腦袋的起始值，不是遊戲內建數字，熊實戰觀察
# 覺得不準直接改這裡即可。
GUARD_CRITICAL_HP_RATIO = 0.25
GUARD_SHIELD_PHASE_THRESHOLD = 300

# 記住「這一輪清護衛期間，我們自己切換到了哪一隻陀螺」，見檔頭
# 2026-08-27 修正說明。cleared_all 時重置，None 代表「還沒自己切換過，
# 相信 roster 的 status 欄位」。
_assumed_active_index = None

# 連續遇到「資料本身有問題」（屬性未知、roster 找不到出戰陀螺、結果訊息
# 缺欄位）幾次之後，主動重新查一次「我的陀螺」＋「綁定一覽」刷新 roster，
# 而不是一直卡著等熊發現。「沒有完美剋制的陀螺」不算問題（那是正常的
# 業務判斷，不是資料壞掉），不會累加這個計數。
PROBLEM_REFRESH_THRESHOLD = 2
_consecutive_problem_count = 0


def _note_outcome(is_problem: bool) -> bool:
    """記錄這次判斷是不是「資料有問題」，回傳是否已經達到刷新門檻
    （達到的話呼叫端要改成送出刷新指令，並且這個函式已經把計數器歸零，
    不用呼叫端自己再歸零一次）。"""
    global _consecutive_problem_count
    if not is_problem:
        _consecutive_problem_count = 0
        return False
    _consecutive_problem_count += 1
    if _consecutive_problem_count >= PROBLEM_REFRESH_THRESHOLD:
        _consecutive_problem_count = 0
        return True
    return False


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
        return {"mode": "none", "commands": [], "problem": True,
                "reason": "查詢結果沒有下一顆的弱點資訊，無法判斷"}

    if not roster:
        return {"mode": "none", "commands": [], "problem": True,
                "reason": "沒有 roster 資料（tops.json 不存在或尚未查過陀螺收藏），無法判斷"}

    global _assumed_active_index

    active_top = next((t for t in roster if t.get("status") == "active"), None)
    # 如果這一輪清護衛期間我們自己切換過陀螺，roster 的 status 欄位在
    # 下次「我的陀螺」重新查詢前都不會反映這次切換——優先採信自己記住
    # 的假設值，不然會一直誤判成「還沒換到剋制陀螺」形成無限循環
    # （見檔頭 2026-08-27 修正說明）。
    if _assumed_active_index is not None:
        assumed_top = next((t for t in roster if t.get("index") == _assumed_active_index), None)
        if assumed_top is not None:
            active_top = assumed_top

    if active_top is None:
        return {"mode": "none", "commands": [], "problem": True,
                "reason": "roster 裡找不到目前出戰的陀螺，無法判斷"}

    if active_top.get("element") is None or active_top.get("type") is None:
        return {
            "mode": "none",
            "commands": [],
            "problem": True,
            "reason": (f"目前出戰「{active_top.get('name')}」的屬性／類型未知"
                       "（圖鑑可能還沒更新這隻陀螺的資料），不自動出手，避免用不完整的資料誤判"),
        }

    if _score_top(active_top, next_target) == 2:
        return {
            "mode": "attack",
            "commands": ["清護衛"],
            "problem": False,
            "reason": f"目前出戰「{active_top.get('name')}」完美剋制下一顆護衛，自動打清護衛",
        }

    best = recommend_for_guard_target(next_target, roster, top_n=1)
    if not best or best[0]["_score"] != 2:
        return {
            "mode": "none",
            "commands": [],
            "problem": False,  # 真的沒有剋制陀螺，是正常業務判斷，不是資料壞掉
            "reason": "手上沒有完美剋制下一顆護衛的陀螺，僅顯示建議，不自動出手",
        }

    if best[0].get("element") is None or best[0].get("type") is None:
        return {
            "mode": "none",
            "commands": [],
            "problem": True,
            "reason": f"建議的陀螺「{best[0].get('name')}」屬性／類型未知，不自動切換，避免用不完整的資料誤判",
        }

    _assumed_active_index = best[0]["index"]
    return {
        "mode": "switch_and_requery",
        "commands": [f"出戰 {best[0]['index']}", "護衛"],
        "problem": False,
        "reason": f"切換為「{best[0]['name']}」（完美剋制下一顆護衛），切換後重新查詢確認",
    }


def decide_after_outcome(parsed):
    """guard_clear_outcome.py 解析出來的結果訊息（一擊拆除 / 戰鬥勝利）進來時呼叫。

    回傳 {"mode": "requery"|"none", "commands": [...], "reason": str}
    """
    global _assumed_active_index

    structured = parsed.get("structured") or {}

    if structured.get("cleared_all"):
        # 本輪清護衛結束，重置切換假設——下一輪（下次護衛重新增生）
        # 重新相信 roster 的 status 欄位，不要延續這一輪的假設值。
        _assumed_active_index = None
        return {"mode": "none", "commands": [], "problem": False,
                "reason": "護衛已全數清空，清護衛迴圈結束"}

    remaining = structured.get("remaining")
    if remaining is not None and remaining > 0:
        return {
            "mode": "requery",
            "commands": ["護衛"],
            "problem": False,
            "reason": f"還剩 {remaining} 顆，重新查詢繼續清",
        }

    return {"mode": "none", "commands": [], "problem": True,
            "reason": "結果訊息沒有剩餘數量資訊，無法判斷是否繼續，交給你手動查看"}


def _refresh_roster_action(reason_prefix: str):
    """連續遇到資料問題達到門檻時觸發：重新查一次「我的陀螺」＋「綁定一覽」
    刷新 roster，最後再補一次「護衛」讓迴圈接著跑下去（不然刷新完資料，
    但沒有東西觸發下一次判斷，迴圈就停在這裡了）。順便清掉切換假設
    ——反正馬上就有全新的 roster 資料，不需要延續舊的假設值。"""
    global _assumed_active_index
    _assumed_active_index = None
    return actions.send_sequence(
        ["我的陀螺", "綁定一覽", "護衛"], interval_seconds=2,
        reason=f"{reason_prefix}，主動刷新陀螺圖鑑後重新查詢",
        log=f"[清護衛] 🔄 {reason_prefix}，懷疑圖鑑資料過期，重新查詢「我的陀螺」＋「綁定一覽」刷新後繼續",
    )


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
        if _note_outcome(action["problem"]):
            return _refresh_roster_action(
                f"連續 {PROBLEM_REFRESH_THRESHOLD} 次判斷不出來（最近一次：{action['reason']}）"
            )
        if action["mode"] == "none":
            return actions.none(log=f"[清護衛] {action['reason']}")
        return actions.send_sequence(
            action["commands"], interval_seconds=2, reason=action["reason"],
            log=f"[清護衛] ✅ {action['reason']}",
        )

    if shape == "guard_clear_outcome":
        action = decide_after_outcome(ctx.parsed)
        if _note_outcome(action["problem"]):
            return _refresh_roster_action(
                f"連續 {PROBLEM_REFRESH_THRESHOLD} 次判斷不出來（最近一次：{action['reason']}）"
            )
        if action["mode"] == "none":
            return actions.none(log=f"[清護衛] {action['reason']}")
        # 這是「清護衛→看護衛」這個轉折，唯一需要等冷卻的地方（見檔頭
        # GUARD_CLEAR_COOLDOWN_SECONDS 說明）。用 schedule 而不是立即送出，
        # 一來天生可取消（真的卡住可以 /sched cancel），二來不會卡住
        # dispatch() 讓其他訊息等這 1.5 秒才被處理。
        return actions.schedule(
            steps=action["commands"], delay_seconds=GUARD_CLEAR_COOLDOWN_SECONDS,
            reason=action["reason"],
            log=(f"[清護衛] 🔁 {action['reason']}"
                 f"（等待 {GUARD_CLEAR_COOLDOWN_SECONDS} 秒冷卻後重新查詢）"),
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