"""
satellite_training_strategy.py —— 群星計畫（衛星培育）決策層

只負責一件事：看到一則已經被 Parser 判定為「群星計畫培育畫面」的訊息
（ctx.shape == "satellite_training_round"，見 parsing/response_shapes/
satellite_training_round.py），決定要點哪一顆按鈕。不負責判斷「這是什麼
訊息」（2026-09 搬到 response_shapes 了，這支檔案比那個慣例還早出現，
之前一直直接吃原始文字自己判斷），也不負責送出動作（那是
executor.click_button 的事）。

目前的策略目標（融合素材用途，跟一般「求高數值、求金技」相反）：
  - 數值越低越好，不主動選數值特訓
  - 技能恰好一個普通技能：拿到第一個技能前選旋技特訓，拿到後停止
  - 領悟值超過保底線（上限 - 15，體力值夠時旋技特訓每次拿 15~20 領悟值）
    時先改選休息，保留這個穩拿的機會，等真的需要時（例如最後一回合）
    再用（熊 2026-08-22 指定，之前沒有這條，是每回合都選旋技特訓直到拿到技能）
  - 最後一回合（第 N/N 回合）還沒拿到技能：強制選旋技特訓，不管其他判斷，
    避免整個 session 白做（熊 2026-08-22 指定，之前沒有這條保底規則）
  - 完全不碰交流類選項：羈絆 >= 80 且金技數 < 3 時會自動觸發金技，
    要避免可控的那 8 個金技，就必須完全不交流
  - 另外 4 個只能靠「黃金事件」（被動觸發）取得的金技，任何選擇都無法
    避免，不需要特別處理
  - 隨機岔路事件（神秘旋核／魔鬼特訓／修行岔路等）：選哪個都行，
    目前預設固定選第二個選項（熊 2026-08-22 指定，原本是第一個）

用法（見檔尾 decide(ctx)）：
    from triggers import satellite_training_strategy
    action = satellite_training_strategy.decide(ctx)  # ctx 是 triggers.context.TriggerContext

「剛打了培育指令，等待 BOT 第一則回覆」這個狀態，觸發點（使用者打「培育」
指令）跟消費點（BOT 回覆時判斷新建/續練）是兩個不同來源的事件，透過
triggers/runtime_state.py 存取（key="awaiting_training_reply"），由
action_dispatcher.py 設置、消費，這支檔案只負責讀 ctx.awaiting_training_reply，
不受這次搬遷影響，不用跟著改。
"""

import auto_toggle
from triggers import actions

# 自動點擊開關的 system_key，跟主塔戰鬥、世界王共用同一套 auto_toggle 機制。
SYSTEM_KEY = auto_toggle.SATELLITE_TRAINING

# 體力值夠的情況下，旋技特訓每次拿 15~20 領悟值（熊 2026-08-22 確認）。
# 用「保底最小增量」而不是固定門檻數字，是因為只要目前領悟值超過
# 「上限 - 最小增量」，下一次旋技特訓不管骰到多少都保證滿值拿到技能——
# 這是數學上算出來的保底線，不是憑感覺訂的門檻，上限變動時這個判斷
# 依然成立，不用跟著調整常數。
MIN_INSIGHT_GAIN_WITH_SUFFICIENT_STAMINA = 15


def decide_action(structured, buttons):
    """核心決策函式，吃 Parser 已經解析好的結構化資料（見
    parsing/response_shapes/satellite_training_round.py 的 parse()），
    不再自己碰原始文字或讀 catalog。

    structured: response_shapes.satellite_training_round.parse() 的回傳值
    buttons: monitor 記錄的按鈕清單（extract_buttons() 的輸出格式）

    回傳 {"data": ..., "button_text": ..., "reason": ...} 或 None（判斷不出來
    要選什麼，呼叫端應該記錄下來、先不要自動點，留給人工介入）。
    """
    if not buttons:
        return None

    if structured["kind"] == "random_event":
        # 岔路事件：目前策略是選哪個都行，固定選第二個選項，純粹讓流程繼續
        # （熊 2026-08-22 指定改成第二個，原本是第一個；catalog 裡目前收錄
        # 的 5 個岔路事件都剛好只有兩個選項，這裡直接假設至少有 2 個，
        # 之後 catalog 補新事件如果只有 1 個選項，這裡會 IndexError，
        # 算是刻意保留的提醒，不用特別防禦）。
        second_option = structured["options"][1]
        # 拿 button data 要去實際 buttons 清單裡找，而不是直接信任 catalog
        # （catalog 只是參考資料，實際點擊一律以 monitor 當下記錄的 data 為準，
        # 避免遊戲版本更新後 catalog 沒同步更新導致點錯）。
        matched = _find_button_by_text(buttons, second_option["text"])
        if matched:
            return {
                "data": matched["data"],
                "button_text": matched["text"],
                "reason": f"隨機岔路事件（{structured['event_id']}），策略：固定選第二個選項",
            }
        return None

    # kind == "main_menu"
    learned_count = structured["learned_count"]
    round_progress = structured["round_progress"]
    is_last_round = round_progress is not None and round_progress[0] >= round_progress[1]

    insight_progress = structured["insight_progress"]
    # 目前領悟值超過「上限 - 最小增量」，代表下一次旋技特訓不管骰到
    # 多少都保證滿值拿到技能——保底已經穩了，不用急著這回合就用掉。
    guaranteed_next_roll = (
        insight_progress is not None
        and insight_progress[0] > insight_progress[1] - MIN_INSIGHT_GAIN_WITH_SUFFICIENT_STAMINA
    )

    if learned_count == 0 and is_last_round:
        # 這個 session 最後一回合了還沒拿到技能——這是最後一次機會，
        # 強制選旋技特訓，不管其他判斷怎麼說（包括下面的保底判斷：
        # 就算領悟值已經過保底線，此時也該用掉而不是繼續休息），
        # 避免這次培育白做。
        target_data = "tr_skill"
        reason = (f"第 {round_progress[0]}/{round_progress[1]} 回合"
                   "（本 session 最後一回合），尚未取得技能，最後機會強制選旋技特訓")
    elif learned_count == 0 and guaranteed_next_roll:
        # 還沒拿到技能，但領悟值已經過保底線：先選休息（不增加領悟值），
        # 把這個穩拿的機會留到真的需要的時候（例如最後一回合）才用掉，
        # 不用每回合都急著選旋技特訓。
        target_data = "rest"
        reason = (f"領悟 {insight_progress[0]}/{insight_progress[1]}，已過保底門檻"
                   "（再選旋技特訓必定滿值拿到技能），先休息保留這個機會，"
                   "留到真的需要時（例如最後一回合）再用")
    elif learned_count == 0:
        target_data = "tr_skill"
        reason = "尚未取得任何技能，選旋技特訓以取得第一個普通技能"
    else:
        target_data = "rest"
        reason = f"已取得 {learned_count} 個技能，達成目標，之後一律選休息避免額外成長"

    matched = _find_button_by_data(buttons, target_data)
    if matched:
        return {
            "data": matched["data"],
            "button_text": matched["text"],
            "reason": reason,
        }
    return None


def _find_button_by_data(buttons, action_code):
    """catalog 裡存的是乾淨的行動代碼（例如 "tr_skill"），但實際按鈕的 data
    是完整字串（例如 "sat:190739112:tr_skill"，前面帶 sender_id），所以用
    「data 是否以 :action_code 結尾」來比對，而不是整串相等。
    """
    suffix = ":" + action_code
    for b in buttons:
        data = b.get("data") or ""
        if data == action_code or data.endswith(suffix):
            return b
    return None


def _find_button_by_text(buttons, text):
    for b in buttons:
        if b.get("text") == text:
            return b
    return None


def decide(ctx):
    """action_dispatcher.py 的統一觸發清單入口。

    2026-09 改版：不再自己判斷「這是不是群星計畫訊息」（原本會呼叫
    classify_message 等函式直接分析 ctx.text），改成直接問 Parser 已經
    判定好的 ctx.shape——不是這個 shape 就代表「這則訊息根本不歸我管」，
    安靜回傳 None，不印任何 log（對照 triggers/actions.py 的說明：這跟
    「判斷過但沒動作」的 actions.none() 是兩種不同語意）。修法前，任何
    不相關的按鈕訊息（例如「⚔️ 旋鬥擂台」這類戰鬥畫面）都會落到最後的
    fallback，誤印「策略無法判斷要選哪個按鈕」的警告，根源就是沒有先做
    這一層「這是不是我的訊息」的過濾，現在改成一開始就篩掉。

    開關關閉時 stop=False（放行給 reaction_rules 兜底）這個跟主塔戰鬥
    不同的行為，是原本就有的差異，不是這次整理造成的不一致，先照舊保留，
    之後熊想統一成同一種行為再說。
    """
    if not ctx.buttons or ctx.shape != "satellite_training_round":
        return None

    if not ctx.is_enabled(SYSTEM_KEY):
        return actions.none(
            log="[群星計畫] 🔕 自動點擊已關閉（終端機輸入 /auto 查看開關狀態），"
                "已收到訊息但不會自動點擊，請自行手動選擇",
            stop=False,
        )

    structured = ctx.structured

    if ctx.awaiting_training_reply and structured["kind"] == "main_menu":
        session_kind = structured["session_kind"]
        if session_kind == "new":
            print("[群星計畫] 🆕 開始新一輪培育（新建衛星）")
        elif session_kind == "continuing":
            print("[群星計畫] ▶️ 續練進行中的衛星")

    action = decide_action(structured, ctx.buttons)
    if action:
        return actions.click_button(
            chat_id=ctx.chat_id, message_id=ctx.message_id,
            data=action["data"], button_text=action["button_text"], reason=action["reason"],
        )

    # 到這裡代表：Parser 已經確認這是群星計畫的畫面（main_menu 或
    # random_event），但實際按鈕比對失敗——這才是真正值得印出來的異常，
    # 不會再對「⚔️ 旋鬥擂台」這類不相關訊息誤發警告。
    return actions.none(
        log=f"[群星計畫] ⚠️ 判定為群星計畫訊息，但無法決定要選哪個按鈕：{ctx.text[:40]}...",
        stop=False,
    )