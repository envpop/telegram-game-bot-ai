"""action_dispatcher.py —— 根據 parser 結果協調各自動化處理器。"""

import executor
import profile_sync_strategy
import satellite_training_strategy
import scheduler
import world_boss_strategy
from reaction_rules import ReactionRuleEngine


class ActionDispatcher:
    """接收 parser 的結構化結果，決定是否呼叫 executor 或更新資料。

    announcement_strategies：公告頻道的判斷模組清單，每個模組要提供
        load_catalog(base_dir) -> dict
        decide_action(text, catalog, base_dir, account_id) -> {"mode", ...}
    之後要新增新的公告種類，照這個介面寫一支新模組、把模組加進清單就好，
    這裡的迴圈不用改（跟原本 main.py 裡 ANNOUNCEMENT_STRATEGIES 的用法一致）。
    """

    def __init__(self, base_dir, rules_file, account_id_getter,
                 announcement_strategies=None, click_fn=None):
        self.base_dir = base_dir
        self.account_id_getter = account_id_getter
        self.announcement_strategies = announcement_strategies or []
        self.click_fn = click_fn
        self.rule_engine = ReactionRuleEngine(rules_file, click_fn=click_fn)
        # 使用者剛打「培育」後，等待 BOT 第一則回覆；key 是 chat_id。
        self._awaiting_training_reply = {}

    @property
    def account_id(self):
        return self.account_id_getter()

    async def dispatch(self, record, parsed):
        if parsed is None:
            return

        source_type = parsed.get("source_type")
        if source_type == "user" and parsed.get("command") == "培育":
            self._awaiting_training_reply[record.get("chat_id")] = True

        if source_type not in ("server", "announcement"):
            return

        text = record.get("text") or ""
        chat_name = record.get("chat_name") or ""
        was_awaiting_training_reply = self._awaiting_training_reply.pop(record.get("chat_id"), False)

        if source_type == "announcement":
            await self._handle_announcement(text)
            return  # 公告頻道：不管有沒有動作，都不會再往下走一般觸發規則

        # ---- 以下 source_type 只會是 "server" ----

        if await self._handle_profile_sync(text):
            return

        if await self._handle_world_boss_status_query(text):
            return

        if await self._handle_satellite_buttons(record, text, was_awaiting_training_reply):
            return

        await self.rule_engine.handle(chat_name, text)

    # ---- 公告頻道（世界王等）----
    async def _handle_announcement(self, text):
        for strategy in self.announcement_strategies:
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
                job_id = scheduler.schedule(job, executor.send_now, self.click_fn)
                print(f"[公告觸發] ⏳ {action['reason']}，已排程 {job_id}"
                      f"（{action['delay_seconds']:.0f} 秒後執行，"
                      f"可用 /sched list 查看、/sched cancel {job_id} 取消）")
                return True
        return False  # 沒有任何策略模組判斷出動作，純資訊公告

    # ---- 陀螺／衛星／背包／道具說明：四種資料同步都交給 profile_sync_strategy 統一處理 ----
    async def _handle_profile_sync(self, text):
        sync_result = profile_sync_strategy.handle_server_message(text, self.base_dir, self.account_id)
        if not sync_result["handled"]:
            return False
        print(sync_result["log"])
        if sync_result["commands"]:
            await executor.send_sequence(
                sync_result["commands"], interval_seconds=2, reason=sync_result["commands_reason"]
            )
        return True

    # ---- 世界王查詢回覆（第三道保險）----
    async def _handle_world_boss_status_query(self, text):
        wb_catalog = world_boss_strategy.load_catalog(self.base_dir)
        wb_action = world_boss_strategy.decide_action_from_status_query(
            text, wb_catalog, self.base_dir, self.account_id
        )
        if wb_action["mode"] == "now":
            await executor.send_now(wb_action["command"], chat_id=wb_action["chat_id"], reason=wb_action["reason"])
            return True
        return False

    # ---- 群星計畫（衛星培育）：帶按鈕的訊息，交給策略層決定要點哪顆按鈕 ----
    async def _handle_satellite_buttons(self, record, text, was_awaiting_training_reply):
        buttons = record.get("buttons")
        if not buttons:
            return False

        if was_awaiting_training_reply:
            catalog = satellite_training_strategy.load_catalog(self.base_dir)
            session_kind = satellite_training_strategy.classify_session_start(text, catalog)
            if session_kind == "new":
                print("[群星計畫] 🆕 開始新一輪培育（新建衛星）")
            elif session_kind == "continuing":
                print("[群星計畫] ▶️ 續練進行中的衛星")

        action = satellite_training_strategy.decide_action(text, buttons, self.base_dir)
        if action:
            await executor.click_button(
                chat_id=record.get("chat_id"),
                message_id=record.get("message_id"),
                data=action["data"],
                button_text=action["button_text"],
                reason=action["reason"],
            )
            return True

        # 判斷不出來：可能真的是衛星培育但策略沒把握，也可能根本不是衛星培育
        # （例如戰鬥選擇戰術的按鈕）。印出提醒，但不要 return True 吃掉這則訊息——
        # 放行讓 reaction_rules 有機會接手（例如戰鬥用 click: 規則自動應戰）。
        print(f"[群星計畫] ⚠️ 策略無法判斷要選哪個按鈕，若非群星計畫訊息可忽略：{text[:40]}...")
        return False