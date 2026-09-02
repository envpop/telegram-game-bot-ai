"""action_dispatcher.py —— 根據 parser 結果協調各自動化處理器。"""

import auto_toggle
import profile_sync_strategy
from reaction_rules import ReactionRuleEngine
from triggers import actions
from triggers.context import TriggerContext
from triggers import runtime_state


class ActionDispatcher:
    """接收 parser 的結構化結果，決定是否呼叫 executor 或更新資料。

    announcement_strategies：公告頻道的判斷模組清單，每個模組要提供
        load_catalog(base_dir) -> dict
        decide_action(text, catalog, base_dir, account_id) -> {"mode", ...}
    這一路維持原本的清單+迴圈用法不變（跟原本 main.py 裡 ANNOUNCEMENT_STRATEGIES 一致）。

    server_triggers：server 訊息的判斷模組清單，每個模組要提供
        decide(ctx) -> triggers.actions.Action | None
    ctx 是 triggers.context.TriggerContext，把這則訊息＋帳號＋惰性狀態存取
    打包成統一入參，模組內部自己判斷「這則訊息歸不歸我管」。清單依序嘗試，
    第一個回傳非 None 的 Action 就執行；action.stop 決定要不要繼續往下一個
    trigger 試（見 triggers/actions.py 說明）。都沒有 Action 命中，最後交給
    reaction_rules 兜底。之後要新增新的觸發模組，照這個介面寫一支放進清單，
    這裡的迴圈跟 dispatch() 都不用改。
    """

    def __init__(self, base_dir, rules_file, account_id_getter,
                 announcement_strategies=None, server_triggers=None):
        self.base_dir = base_dir
        self.account_id_getter = account_id_getter
        self.announcement_strategies = announcement_strategies or []
        self.server_triggers = server_triggers or []
        self.rule_engine = ReactionRuleEngine(rules_file)

    @property
    def account_id(self):
        return self.account_id_getter()

    async def dispatch(self, record, parsed):
        if parsed is None:
            return

        source_type = parsed.get("source_type")
        if source_type == "user" and parsed.get("command") == "培育":
            # 使用者剛打「培育」，記下來等下一則 server/announcement 訊息
            # 消費（見下方 consume）。跟訊息本身的 source_type 分支無關，
            # 所以在最前面、還沒篩選 source_type 之前就要記錄。
            runtime_state.mark("awaiting_training_reply", record.get("chat_id"))

        if source_type not in ("server", "announcement"):
            return

        # 重複派送防護：同一則訊息（同 chat_id + message_id）如果文字內容
        # 跟上次處理過的一模一樣，視為重複事件（常見成因：連線重連時
        # Telethon 對同一次編輯重複觸發），直接略過，不再跑一次完整流程。
        # 文字不同（遊戲把同一則訊息編輯成下一回合新內容）則正常放行，
        # 不受影響。見 runtime_state.is_duplicate_delivery() 說明。
        if runtime_state.is_duplicate_delivery(
            record.get("chat_id"), record.get("message_id"), record.get("text")
        ):
            print(f"[dispatch] ⏭️ 偵測到重複派送（同一則訊息、內容完全相同），"
                  f"略過重複處理：chat={record.get('chat_id')} msg={record.get('message_id')}")
            return

        # 不管這則訊息最後被哪支 trigger 處理，這個旗標都只消費一次——
        # 旗標只代表「等到下一則回覆了沒」，不是「是不是培育訊息」，
        # 這樣才不會因為旗標卡住殘留到很久之後某次不相關的訊息才誤判
        # （行為跟搬移前一致，只是消費的地方統一到 runtime_state）。
        was_awaiting_training_reply = runtime_state.consume(
            "awaiting_training_reply", record.get("chat_id")
        )

        if source_type == "announcement":
            await self._handle_announcement(record.get("text") or "")
            return  # 公告頻道：不管有沒有動作，都不會再往下走一般觸發規則

        # ---- 以下 source_type 只會是 "server" ----

        if await self._handle_profile_sync(parsed):
            return

        ctx = TriggerContext(
            record=record,
            parsed=parsed,
            base_dir=self.base_dir,
            account_id=self.account_id,
            awaiting_training_reply=was_awaiting_training_reply,
        )

        if runtime_state.is_active("suspend_triggers"):
            # 全域暫停中（目前唯一來源：櫻花窗口期間，見 sakura_strategy.py）。
            # 略過整個自動判斷鏈（觸發清單＋兜底的 reaction_rules），避免
            # 跟窗口期間排定的一長串連刷指令互相干擾。profile_sync 在上面
            # 已經跑過、不受影響——純資料同步沒有主動送指令的風險。
            print(f"[dispatch] ⏸️ 自動觸發暫停中（櫻花窗口期間），略過本則訊息判斷：chat={ctx.chat_id}")
            return

        for trigger in self.server_triggers:
            action = trigger.decide(ctx)
            if action is None:
                continue  # 這則訊息不歸這支 trigger 管，安靜地換下一個試
            await actions.execute(action)
            if action.stop:
                return

        await self.rule_engine.handle(ctx.chat_name, ctx.text)

    # ---- 公告頻道（世界王等）----
    async def _handle_announcement(self, text):
        if runtime_state.is_active("suspend_triggers"):
            print("[公告觸發] ⏸️ 自動觸發暫停中（櫻花窗口期間），略過本則公告判斷")
            return False

        # 2026-08-29 修正：改成「每個策略各自判斷、命中的都執行」，不再是
        # 「第一個命中就停」。世界王的公告常常同時帶著「王降臨」跟「召喚
        # 護衛」兩件事在同一則訊息裡，這兩個判斷（要不要打王、要不要清
        # 護衛）本來就互相獨立，不該因為其中一個策略先判斷出動作，
        # 就讓另一個策略完全沒機會被檢查到（熊 2026-08-29 反映）。
        handled_any = False
        for strategy in self.announcement_strategies:
            system_key = getattr(strategy, "SYSTEM_KEY", None)
            if system_key and not auto_toggle.is_enabled(self.base_dir, system_key):
                label = auto_toggle.SYSTEM_KEYS.get(system_key, system_key)
                print(f"[{label}] 🔕 自動發送已關閉，略過判斷（終端機輸入 /auto 查看開關狀態）")
                continue
            catalog = strategy.load_catalog(self.base_dir)
            action = strategy.decide_action(text, catalog, self.base_dir, self.account_id)
            if action["mode"] == "now":
                trigger_action = actions.send_now(
                    action["command"],
                    chat_id=action["chat_id"],
                    reason=action["reason"],
                )
                await actions.execute(trigger_action)
                handled_any = True

            elif action["mode"] == "scheduled":
                # repeat/interval 是選填（world_boss 目前只用單次延遲送出，
                # 不用設；sakura_strategy 用來排一長串重複指令，見該檔說明）。
                trigger_action = actions.schedule(
                    steps=[action["command"]],
                    delay_seconds=action.get("delay_seconds", 0.0),
                    chat_id=action.get("chat_id"),
                    reason=action.get("reason"),
                )
                await actions.execute(trigger_action)
                # 排程完成訊息（含 job_id）已由 triggers/actions.py 的
                # _run_schedule 印出，這裡不重複印，避免 job_id 拿不到的問題
                # （2026-09-02 修正：dispatcher 沒有管道拿到 job_id，execute() 設計上回傳 None）
                handled_any = True
        return handled_any  # 沒有任何策略模組判斷出動作，純資訊公告

    # ---- 陀螺／衛星／背包／道具說明：四種資料同步都交給 profile_sync_strategy 統一處理 ----
    # 這支不算進 server_triggers 清單——它是單一職責的持久化協調者（owns all
    # persistence），不是「判斷要不要觸發遊戲內動作」的觸發家族成員，介面
    # 也不一樣（回傳 handled/log/commands，不是 Action），保持原本獨立的
    # 前置步驟寫法，不勉強塞進統一介面。
    async def _handle_profile_sync(self, parsed):
        sync_result = profile_sync_strategy.handle_server_message(parsed, self.base_dir, self.account_id)
        if not sync_result["handled"]:
            return False
        print(sync_result["log"])
        if sync_result["commands"]:
            action = actions.send_sequence(
                sync_result["commands"],
                interval_seconds=2,
                reason=sync_result["commands_reason"],
            )
            await actions.execute(action)
        return True