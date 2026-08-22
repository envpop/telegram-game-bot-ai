"""action_dispatcher.py —— 根據 parser 結果協調各自動化處理器。"""

import auto_toggle
import executor
import profile_sync_strategy
import scheduler
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
        for strategy in self.announcement_strategies:
            system_key = getattr(strategy, "SYSTEM_KEY", None)
            if system_key and not auto_toggle.is_enabled(self.base_dir, system_key):
                label = auto_toggle.SYSTEM_KEYS.get(system_key, system_key)
                print(f"[{label}] 🔕 自動發送已關閉，略過判斷（終端機輸入 /auto 查看開關狀態）")
                continue
            catalog = strategy.load_catalog(self.base_dir)
            action = strategy.decide_action(text, catalog, self.base_dir, self.account_id)
            if action["mode"] == "now":
                await executor.send_now(action["command"], chat_id=action["chat_id"], reason=action["reason"])
                return True
            if action["mode"] == "scheduled":
                job = scheduler.ScheduledJob(
                    steps=[action["command"]],
                    delay_seconds=action["delay_seconds"],
                    chat_id=action["chat_id"],
                    reason=action["reason"],
                )
                job_id = scheduler.schedule(job)
                print(f"[公告觸發] ⏳ {action['reason']}，已排程 {job_id}"
                      f"（{action['delay_seconds']:.0f} 秒後執行，"
                      f"可用 /sched list 查看、/sched cancel {job_id} 取消）")
                return True
        return False  # 沒有任何策略模組判斷出動作，純資訊公告

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
            await executor.send_sequence(
                sync_result["commands"], interval_seconds=2, reason=sync_result["commands_reason"]
            )
        return True