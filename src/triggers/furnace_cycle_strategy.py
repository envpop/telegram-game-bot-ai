# -*- coding: utf-8 -*-
"""
furnace_cycle_strategy.py —— 爐火(觀火→投爐)自動循環

負責世界王「次數用完後，用爐火重置」這一段的自動化，只做這一段：
    觀火(反覆) → 燒穿【靈性顫動】 → 投爐(反覆) → 爆射(次數重置) → 結束
不管前面「要不要換手」「打到沒次數」是誰觸發的——這支模組只在乎「已經
決定要跑爐火重置了」之後的這一段，靠 start() 啟動，之後全靠 decide(ctx)
反應伺服器回覆自己接力跑完，跟 guard_clear_strategy.py 是同一種「天然
形成迴圈」的設計：不需要另外的複雜狀態機，靠「決定要送什麼指令」自然
接起來，一次只處理一則訊息。

=== 為什麼獨立成一支檔案 ===
熊 2026-09-04 明確要求：這段之後 full_clear 模式(全程連續，次數用完
一樣要靠爐火重置繼續打)也要用同一套，不要重寫——所以這支模組只依賴
呼叫端已經決定好的意圖(「該重置了，開始吧」)，不知道呼叫端是
furnace_loop 還是 full_clear，也不管重置完之後接下來要幹嘛，純粹管好
「怎麼把爐子燒穿、怎麼把它炸爆」這一段，回頭交還控制權。

=== 節奏規則(2026-09-04 熊確認的實測值，不是遊戲公告數字) ===
觀火：每次間隔 3.5~7 秒亂數；爐溫到 94~100 之間時縮短成 2 秒(快燒穿了，
      提高反應速度，減少「已經夠了但還多等好幾秒」的浪費)。
投爐：每次指定固定數量(見 FEED_QUANTITY)，數量夠大通常一兩次就會炸，
      沒有像觀火那樣的嚴格秒數要求，但保留跟觀火同樣的 3.5~7 秒間隔，
      避免連續動作送太快。
換手：固定 2 秒——這支模組不管換手，換手是呼叫端在啟動這支模組之前的
      事，這裡只是記錄這個數字方便呼叫端之後撰寫時參考，不在這支檔案
      裡用到。

=== 投爐數量怎麼算(2026-09-04 熊確認的遊戲機制) ===
觀火：不接參數，固定花費 5 碎片，隨機提升爐溫 8~21，或直接命中目標色
      直達 100；完成(燒穿)後爐溫歸零。這是遊戲機制本身的隨機性，不需要
      也不可能算出精確值，維持「送出去，看回覆怎麼說」就好。
投爐：接數量參數，每投入 1 碎片約提升爐能 3%(有隨機變動)，目標是衝到
      500%，超過 500% 的部分不會浪費，會留到下一輪爐能繼續累積。
      既然「投超過不虧」，這裡刻意抓保守一點的倍率(FEED_ENERGY_MULTIPLIER
      × FEED_SAFETY_MARGIN，等於假設只有 ×2.55 而不是官方講的「約 ×3」)
      去反推「離 500% 還差多少該投多少」，寧可多投一點一次炸掉，也不要
      因為運氣差投不夠、還要再送一次訊息。
      第一次投爐(剛燒穿，還不知道目前爐能% 是多少——這個數字只有投爐
      回覆才會告訴我們，燒穿當下的回覆不含這項)用 FEED_QUANTITY 這個
      保守固定值；從第二次投爐開始，用上一次回覆的 energy_total_pct
      反推精確數量。

=== 怎麼判斷「重置完成，流程結束」 ===
熊確認：看到「爐子炸了」(furnace_feed 的 exploded=True) 就是成功結束；
如果投爐當下收到「爐火沉寂，現在投什麼都沒反應」(furnace_feed_blocked)，
代表已經被別人搶先炸過、次數已經重置了，一樣視為成功結束，不算異常。
熊也提到可以再查一次「世界王」保險，但顯示不一定準，參考意義不大——
這裡沒有採用，只靠爐火本身的回覆判斷。

=== 安全閥 ===
跟 guard_clear_strategy.py 的 GUARD_SESSION_TIMEOUT_SECONDS 同一種設計：
用逾時而不是計次的方式擋住萬一卡住的無限迴圈——正常情況觀火/投爐幾次
就會結束，遠遠不會撞到這個逾時，只在真的出問題時（例如某種新格式的
回覆沒被任何 furnace_* shape 認出來、迴圈接不下去），避免狀態永久卡在
「進行中」擋住之後的新流程。

=== 狀態存放方式 ===
跟 guard_clear_strategy.py 的 _assumed_active_index 同一種做法：模組
層級變數存實際資料(現在是觀火還是投爐階段)，runtime_state 只負責「這
一輪還在不在進行中」的逾時旗標——這支 bot 一次只服務一個帳號，不需要
用 dict 依帳號區分（跟 guard_clear_strategy.py 的既有假設一致）。

=== 已知限制 ===
- FEED_QUANTITY 是拍腦袋的起始值，需要依熊實際的碎片存量調整，調大這個
  值可以減少投爐次數(甚至一次就炸)，調小則反過來。
- 「變身」訊息、稀有的第三種爐火觸發措辭，目前都還沒有樣本驗證，等
  furnace_watch.py／furnace_awakening_announcement.py 那邊補了新樣本，
  這裡不用跟著改——它們只認 shape 解析出來的欄位(breakthrough/exploded)，
  不重複解析原始文字。
"""
import random
import time

import auto_toggle
from triggers import actions
from triggers import runtime_state

SYSTEM_KEY = auto_toggle.WORLD_BOSS

# 投爐每次的固定數量——只有「第一次投爐(剛燒穿，還不知道目前爐能%)」
# 會用到這個值，之後每次都改用 _feed_quantity_for_gap() 動態算。
# 2026-09-04 先抓一個保守的起始值，需要熊依實際碎片存量調整。
FEED_QUANTITY = 160

# 投爐的爐能估算：官方講「約×3」，這裡刻意打八五折抓保守值(×2.55)，
# 寧可多投一次炸掉，也不要因為運氣差投不夠還要再送一次訊息——反正投
# 超過 500% 的部分不會浪費，見檔頭說明。
FEED_ENERGY_TARGET = 500
FEED_ENERGY_MULTIPLIER = 3
FEED_SAFETY_MARGIN = 0.85

# 觀火間隔：一般 3.5~7 秒亂數，爐溫接近時縮短。
WATCH_DELAY_NORMAL = (3.5, 7.0)
WATCH_DELAY_NEAR_BREAKTHROUGH = 2.0
WATCH_NEAR_BREAKTHROUGH_THRESHOLD = 94

# 投爐間隔：維持跟觀火一樣的區間，避免連續動作送太快。
FEED_DELAY = (1.5, 3.2)

# 安全閥：逾時未結束就視為異常，清除狀態、放行給熊手動處理。20 分鐘是
# 拍腦袋的保險值，跟 guard_clear_strategy.py 的 GUARD_SESSION_TIMEOUT_SECONDS
# 同樣性質，不是遊戲機制數字。
SESSION_TIMEOUT_SECONDS = 20 * 60
_SESSION_STATE_KEY = "furnace_cycle_active"

# 目前進行到哪個階段："watching" | "feeding" | None(沒有進行中的流程)。
_phase = None


def is_active() -> bool:
    return runtime_state.is_active(_SESSION_STATE_KEY, None)


def _mark_active():
    runtime_state.set_until(_SESSION_STATE_KEY, None, time.time() + SESSION_TIMEOUT_SECONDS)


def _clear():
    global _phase
    _phase = None
    runtime_state.clear(_SESSION_STATE_KEY, None)


def start(reason: str = "次數用完，開始爐火重置流程"):
    """呼叫端(furnace_loop/full_clear，之後才會寫)在判斷「該重置了」時
    呼叫這個函式開始流程，回傳第一個要送出的 Action(先觀火)。"""
    global _phase
    _phase = "watching"
    _mark_active()
    return actions.send_now("觀火", reason=reason,
                             log=f"[爐火流程] 🔥 {reason}，開始觀火")


def _resolve_delay(value):
    """讓延遲常數可以填單一數字(固定秒數)或 (最小, 最大) 區間(亂數)，
    兩種格式都能用。2026-09-06 修正：熊把 FEED_DELAY 從 (3.5, 7.0) 改成
    單一數字 2 之後，原本寫死的 random.uniform(*FEED_DELAY) 解包失敗直接
    崩潰(TypeError: argument after * must be an iterable, not float)——
    常數本來就設計成「熊可以自己調」，調的格式卻只支援其中一種，是這裡
    的防呆沒做好，不是熊填錯。"""
    if isinstance(value, (int, float)):
        return float(value)
    return random.uniform(*value)


def _watch_delay(temp_current):
    if temp_current is not None and temp_current >= WATCH_NEAR_BREAKTHROUGH_THRESHOLD:
        return WATCH_DELAY_NEAR_BREAKTHROUGH
    return _resolve_delay(WATCH_DELAY_NORMAL)


def _feed_quantity_for_gap(current_energy_pct):
    """依目前爐能% 反推這次該投多少，才能一次衝過 500%。current_energy_pct
    是 None 時(還沒有任何投爐回覆可以參考，例如剛燒穿的第一次投爐)，
    用 FEED_QUANTITY 這個保守固定值頂著。"""
    if current_energy_pct is None:
        return FEED_QUANTITY
    gap = FEED_ENERGY_TARGET - current_energy_pct
    if gap <= 0:
        return FEED_QUANTITY  # 理論上不會發生(到門檻就該已經炸了)，防呆用
    effective_multiplier = FEED_ENERGY_MULTIPLIER * FEED_SAFETY_MARGIN
    return max(1, round(gap / effective_multiplier))


def _handle_watch(ctx):
    global _phase
    parsed = ctx.structured
    _mark_active()  # 還在正常進行中，延長逾時

    if parsed.get("breakthrough"):
        _phase = "feeding"
        return actions.schedule(
            [f"投爐 {FEED_QUANTITY}"], delay_seconds=2.0,
            reason="爐溫燒穿，開始投爐",
            log="[爐火流程] 🔥🔥 燒穿了，切換到投爐階段",
        )

    delay = _watch_delay(parsed.get("temp_current"))
    return actions.schedule(
        ["觀火"], delay_seconds=delay,
        reason=f"還沒燒穿(爐溫 {parsed.get('temp_current')}/100)，{delay:.1f} 秒後再觀火",
    )


def _handle_watch_blocked(ctx):
    """已經在顫動中了(可能是這個判斷點之前就有人先燒穿)，不用再觀火，
    直接跳去投爐。"""
    global _phase
    _phase = "feeding"
    _mark_active()
    return actions.schedule(
        [f"投爐 {FEED_QUANTITY}"], delay_seconds=2.0,
        reason="爐子已在顫動中，跳過觀火，直接投爐",
        log="[爐火流程] 爐子已在顫動中，直接投爐",
    )


def _handle_feed(ctx):
    parsed = ctx.structured
    if parsed.get("exploded"):
        _clear()
        return actions.none(
            log="[爐火流程] ✅ 爐子炸了，次數已重置，流程結束", stop=True,
        )
    _mark_active()
    quantity = _feed_quantity_for_gap(parsed.get("energy_total_pct"))
    delay = _resolve_delay(FEED_DELAY)
    return actions.schedule(
        [f"投爐 {quantity}"], delay_seconds=delay,
        reason=f"還沒炸(目前爐能 {parsed.get('energy_total_pct')}%)，{delay:.1f} 秒後投 {quantity}",
    )


def _handle_feed_blocked(ctx):
    """投爐當下爐火沉寂——目前流程一定是先確認顫動中才會投爐，理論上
    不該遇到「還沒顫動」這種拒絕，唯一合理解釋是被別人搶先炸過、次數
    已經重置了，視為成功結束，不當異常。"""
    was_feeding = _phase == "feeding"
    _clear()
    if was_feeding:
        return actions.none(
            log="[爐火流程] ✅ 投爐被拒(爐火已沉寂)，應該是被人搶先重置，流程結束",
            stop=True,
        )
    return actions.none(
        log="[爐火流程] ⚠️ 收到「還沒顫動」的投爐拒絕，但流程不在投爐階段，狀態異常，已中止",
        stop=True,
    )


def decide(ctx):
    if not is_active():
        return None  # 沒有進行中的爐火流程，這則訊息不歸這支 trigger 管

    if not ctx.is_enabled(SYSTEM_KEY):
        return None

    if ctx.shape == "furnace_watch":
        return _handle_watch(ctx)
    if ctx.shape == "furnace_watch_blocked":
        return _handle_watch_blocked(ctx)
    if ctx.shape == "furnace_feed":
        return _handle_feed(ctx)
    if ctx.shape == "furnace_feed_blocked":
        return _handle_feed_blocked(ctx)

    return None