# -*- coding: utf-8 -*-
"""
world_boss_mode.py —— 世界王「投入程度」判斷與手動覆蓋

目的：世界王的攻擊行為分成三種投入程度，依條件切換，同時保留手動覆蓋的
空間——熊可以直接下指令蓋過條件判斷，不用等條件自然符合才能測試新行為，
兩種切換方式都要。

跟 auto_toggle.py 不一樣：auto_toggle 是每套系統一個「開/關」布林值，
這裡是「選哪個模式」，值域不只兩個，所以另外開一個檔案，不硬塞進
auto_toggle.py 裡。

=== 目前的三個模式(2026-09-03 確認) ===
    touch         —— 摸王：單次討伐，拿參加獎勵為主。既有行為，跟現有
                     world_boss_strategy.py 的「一個王的生命週期內至少
                     打一次」對齊，這裡只負責「選到這個模式」。
    furnace_loop  —— 爐火：獨立、有限度的中等投入。打完一輪次數 → 用爐火
                     (觀火/投爐)重置次數 → 再打一輪 → 就停，不追到王死。
                     是獨立模式，不是 full_clear 的中繼步驟。
    full_clear    —— 全程：連續討伐直到王死亡，過程中如果次數用完，一樣
                     會用爐火重置繼續打，但這只是「繼續打到死」的一部分，
                     不是額外選了 furnace_loop。目標是拿最高傷害貢獻。

三個模式目前都只是「選到了這個模式」的空殼，實際打法(什麼時候觀火/投爐、
連續出手怎麼排、要不要換手)還沒設計定案，等對應的 Strategy 邏輯確定後
再接手——本模組不做任何戰鬥判斷，只回答「現在該用哪個模式」。

新增模式時：
    1. MODES 加一個字串常數
    2. determine_mode() 的條件判斷加一段
    3. 實際行為交給對應的 Strategy handler 去實作(不在這支模組裡)
不用去動「怎麼判斷該用哪個模式」以外的地方，也不用改 Trigger/Action/
Executor 那幾層。

=== 為什麼直接用階數(stage)判斷，不用「今天第幾隻王」 ===
熊確認：不會 24 小時開著監控，抓不到 2~4 階的王就算了，沒差，所以階數
本身「不可靠」這件事不影響判斷——反正抓不到就放過。範圍訂 2~4 階，只是
抓一個概數，之後還會再調整，不是精確定義。
基準級(潮痕級)沒有階數(stage=None)，一律視為不符合條件、走預設 touch。

=== 條件對應(2026-09-03 確認) ===
    🎫 商會懸賞  → full_clear   (要拿首位傷害者的獎勵，值得全程壓上去)
    2~4 階      → furnace_loop (中等投入，值得多打一輪+爐火)
    都不符合    → touch        (預設)
兩個條件同時符合時，full_clear 優先(全程本來就涵蓋了 furnace_loop 會做的
事，投入程度更高的目標優先)。

=== 手動覆蓋 ===
覆蓋值存在 data/{帳號}/world_boss_mode.json，格式：
    {"override": "full_clear"}   或   {"override": "auto"}(預設，交給條件判斷)
覆蓋沒有到期時間，直到你自己用 /wbmode 指令改回 "auto" 或換成別的 mode
為止——故意不做「N 小時後自動恢復」，那是額外的複雜度，之後真的需要
再加，不要一開始就預先設計。

=== 「檢查配置是否具備足夠剋制性」不在這支模組裡 ===
熊確認：沒有完美剋制時，不降級模式、也不是照樣硬打，而是「挑選手上最好
的(屬性剋制優先，戰力最高其次)陀螺去應對」——這是「選陀螺」的邏輯，跟
「選投入程度」是兩件不同的事，不應該混在同一支模組裡判斷(職責分離)。
這支模組只回答模式，選陀螺的邏輯之後另外設計、可能會用到 roster_loader。

本模組只負責「回傳現在該用哪個 mode」，不呼叫 executor/scheduler，也不
知道每個 mode 底下實際要做什麼——那是 Strategy 的責任，符合 Architecture
Rules 對 Trigger/Strategy 的分工(判斷「要不要做/做哪個模式」是 Strategy
層的事，「怎麼做」交給對應的 mode handler 各自實作)。
"""
import json

from data_store import account_dir

TOUCH = "touch"
FURNACE_LOOP = "furnace_loop"
FULL_CLEAR = "full_clear"
AUTO = "auto"  # 手動覆蓋的特殊值，代表「不要覆蓋，照條件判斷」

MODES = [TOUCH, FURNACE_LOOP, FULL_CLEAR]

_STATE_FILENAME = "world_boss_mode.json"


def _state_file(base_dir, account_id):
    return account_dir(base_dir, account_id) / _STATE_FILENAME


def get_override(base_dir, account_id) -> str:
    f = _state_file(base_dir, account_id)
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data.get("override", AUTO)
    except (FileNotFoundError, json.JSONDecodeError):
        return AUTO


def set_override(base_dir, account_id, mode: str) -> None:
    if mode != AUTO and mode not in MODES:
        raise ValueError(f"未知的 mode：{mode}（合法值：{AUTO}, {', '.join(MODES)}）")
    f = _state_file(base_dir, account_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"override": mode}, ensure_ascii=False, indent=2), encoding="utf-8")


def determine_mode(base_dir, account_id, stage, has_ticket_bounty: bool):
    """回傳 (mode, reason)。reason 只是給 log/顯示用來核對「為什麼選這個
    mode」，不影響判斷邏輯本身、也不是拿來給遊戲規則用的欄位。

    stage：世界王的階數(int)，基準級(潮痕級)沒有階數時傳 None。
    條件判斷順序：手動覆蓋 > 商會懸賞(🎫→full_clear) > 2~4 階(→furnace_loop)
    > 預設(touch)。同時符合 🎫 跟 2~4 階時，full_clear 優先。
    有覆蓋就整段跳過條件判斷——覆蓋的意義就是「先不管條件，我說了算」。
    """
    override = get_override(base_dir, account_id)
    if override != AUTO:
        return override, "manual_override"

    if has_ticket_bounty:
        return FULL_CLEAR, "ticket_bounty"

    if stage is not None and 2 <= stage <= 4:
        return FURNACE_LOOP, f"stage_{stage}"

    return TOUCH, "default"


# ============================================================
# 終端機指令：/wbmode
# ============================================================
# 統一介面：async def handle_command(text, base_dir, account_id) -> None
# 跟 auto_toggle.py 的 /auto 指令同一套介面，main.py 用登記表統一呼叫。

_LABELS = {AUTO: "自動判斷", TOUCH: "摸王(單次)", FURNACE_LOOP: "爐火(中等投入)", FULL_CLEAR: "全程(最高傷害)"}

_USAGE = ("[錯誤] /wbmode 用法：\n"
          "  /wbmode              查看目前的手動覆蓋狀態\n"
          "  /wbmode auto         取消覆蓋，改回條件自動判斷\n"
          "  /wbmode touch        強制切換成摸王(單次)\n"
          "  /wbmode furnace_loop 強制切換成爐火(中等投入)\n"
          "  /wbmode full_clear   強制切換成全程(最高傷害)")


async def handle_command(text, base_dir, account_id):
    parts = text.split()
    if len(parts) == 1:
        override = get_override(base_dir, account_id)
        print(f"[世界王模式] 手動覆蓋：{_LABELS[override]}（{override}）")
        return
    if len(parts) == 2 and (parts[1] == AUTO or parts[1] in MODES):
        set_override(base_dir, account_id, parts[1])
        print(f"[世界王模式] 已設定：{_LABELS[parts[1]]}（{parts[1]}）")
        return
    print(_USAGE)
