"""action_dispatcher.py —— 根據 parser 結果協調各自動化處理器。"""

import auto_toggle
import executor
import guard_clear_strategy
import main_tower_battle_strategy
import profile_sync_strategy
import satellite_training_strategy
import scheduler
import world_boss_strategy
from reaction_rules import ReactionRuleEngine
from roster_loader import load_roster


class ActionDispatcher:
    """接收 parser 的結構化結果，決定是否呼叫 executor 或更新資料。

    announcement_strategies：公告頻道的判斷模組清單，每個模組要提供
        load_catalog(base_dir) -> dict
        decide_action(text, catalog, base_dir, account_id) -> {"mode", ...}
    之後要新增新的公告種類，照這個介面寫一支新模組、把模組加進清單就好，
    這裡的迴圈不用改（跟原本 main.py 裡 ANNOUNCEMENT_STRATEGIES 的用法一致）。
    """

    def __init__(self, base_dir, rules_file, account_id_getter,
                 announcement_strategies=None):
        self.base_dir = base_dir
        self.account_id_getter = account_id_getter
        self.announcement_strategies = announcement_strategies or []
        self.rule_engine = ReactionRuleEngine(rules_file)
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

        if await self._handle_profile_sync(parsed):
            return

        if await self._handle_world_boss_status_query(text):
            return

        if await self._handle_main_tower_battle(record, parsed):
            return

        if await self._handle_guard_clear(record, parsed):
            return

        if await self._handle_satellite_buttons(record, text, was_awaiting_training_reply):
            return

        await self.rule_engine.handle(chat_name, text)

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
    async def _handle_profile_sync(self, parsed):
        # 改吃整包 parsed（不只 text）：陀螺清單／衛星圖鑑／綁定一覽三種
        # 已經由 response_parser.py 解析過（parsed['shape']/['structured']），
        # profile_sync_strategy 直接讀這裡的結果存檔，不再重新解析一次
        # 原文——跟 _handle_main_tower_battle() 信任上游 shape 判斷的原則
        # 一致。parsed['raw_text'] 一律都有（見 message_router._base_result()），
        # 背包／道具說明這兩種還沒有 shape，profile_sync_strategy 內部會
        # 退回讀這個欄位直接判斷。
        sync_result = profile_sync_strategy.handle_server_message(parsed, self.base_dir, self.account_id)
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
        if not auto_toggle.is_enabled(self.base_dir, "world_boss"):
            return False  # 開關關閉：不送出補刀指令，但仍放行讓其他 handler 有機會處理這則訊息

        wb_catalog = world_boss_strategy.load_catalog(self.base_dir)
        wb_action = world_boss_strategy.decide_action_from_status_query(
            text, wb_catalog, self.base_dir, self.account_id
        )
        if wb_action["mode"] == "now":
            await executor.send_now(wb_action["command"], chat_id=wb_action["chat_id"], reason=wb_action["reason"])
            return True
        return False

    # ---- 主塔進階戰鬥：每回合選擇戰術，交給策略層決定要點哪顆按鈕 ----
    async def _handle_main_tower_battle(self, record, parsed):
        buttons = record.get("buttons")
        if not buttons:
            return False

        # 用 response_parser 已經判斷好的 shape 來確認，而不是重新對文字做
        # pattern matching——shape 比對邏輯只在 main_tower_battle_prompt.py
        # 一個地方維護，這裡單純信任上游結果。
        if parsed.get("shape") != "main_tower_battle_prompt":
            return False

        if not auto_toggle.is_enabled(self.base_dir, "main_tower_battle"):
            print(f"[主塔戰鬥] 🔕 自動點擊已關閉（終端機輸入 /auto 查看開關狀態），"
                  f"已收到訊息但不會自動點擊，請自行手動選擇")
            return True

        structured = parsed.get("structured")
        action = main_tower_battle_strategy.decide_action(structured, buttons)
        if action:
            await executor.click_button(
                chat_id=record.get("chat_id"),
                message_id=record.get("message_id"),
                data=action["data"],
                button_text=action["button_text"],
                reason=action["reason"],
            )
            return True

        # 判斷不出來（例如關鍵數值解析失敗、或必殺技按鈕沒對到），印出提醒但
        # 不吃掉這則訊息，讓熊自己手動選——寧可少點一次，也不要亂點。
        print(f"[主塔戰鬥] ⚠️ 策略無法判斷要選哪個戰術按鈕：{(record.get('text') or '')[:40]}...")
        return True  # 已確認是主塔戰鬥訊息，不用再往下讓其他 handler 誤判

    # ---- 清護衛（半自動）：查詢結果判斷出手／換陀螺、結果訊息判斷是否繼續、
    #      沒一擊拆掉時的按鈕戰鬥沿用 main_tower_battle_strategy（更保守門檻）----
    async def _handle_guard_clear(self, record, parsed):
        shape = parsed.get("shape")
        if shape not in ("guard_status", "guard_clear_outcome", "guard_battle_prompt"):
            return False

        if not auto_toggle.is_enabled(self.base_dir, guard_clear_strategy.SYSTEM_KEY):
            return False  # 不吃掉訊息：關閉時顯示照常，只是不自動出手

        if shape == "guard_status":
            roster = load_roster(self.base_dir, self.account_id)
            action = guard_clear_strategy.decide_action(parsed, roster)
            if action is None:
                return False  # 不是「還有護衛」的查詢結果，交給其他 handler
            if action["mode"] == "none":
                print(f"[清護衛] {action['reason']}")
                return False  # 沒有動作可送，不吃掉這則訊息
            await executor.send_sequence(action["commands"], interval_seconds=2, reason=action["reason"])
            print(f"[清護衛] ✅ {action['reason']}")
            return True

        if shape == "guard_clear_outcome":
            action = guard_clear_strategy.decide_after_outcome(parsed)
            if action["mode"] == "none":
                print(f"[清護衛] {action['reason']}")
                return False
            await executor.send_now(action["commands"][0], reason=action["reason"])
            print(f"[清護衛] 🔁 {action['reason']}")
            return True

        # shape == "guard_battle_prompt"：沒一擊拆掉，進入按鈕戰鬥模式，
        # 沿用主塔戰鬥的決策邏輯，但門檻更保守（見 guard_clear_strategy.py
        # 的 GUARD_CRITICAL_HP_RATIO / GUARD_SHIELD_PHASE_THRESHOLD 說明）。
        buttons = record.get("buttons")
        if not buttons:
            return False

        structured = parsed.get("structured")
        action = main_tower_battle_strategy.decide_action(
            structured, buttons,
            critical_hp_ratio=guard_clear_strategy.GUARD_CRITICAL_HP_RATIO,
            shield_phase_threshold=guard_clear_strategy.GUARD_SHIELD_PHASE_THRESHOLD,
        )
        if action:
            await executor.click_button(
                chat_id=record.get("chat_id"),
                message_id=record.get("message_id"),
                data=action["data"],
                button_text=action["button_text"],
                reason=action["reason"],
            )
            return True

        print(f"[護衛戰鬥] ⚠️ 策略無法判斷要選哪個戰術按鈕：{(record.get('text') or '')[:40]}...")
        return True  # 已確認是護衛戰鬥訊息，不用再往下讓其他 handler 誤判

    # ---- 群星計畫（衛星培育）：帶按鈕的訊息，交給策略層決定要點哪顆按鈕 ----
    async def _handle_satellite_buttons(self, record, text, was_awaiting_training_reply):
        buttons = record.get("buttons")
        if not buttons:
            return False

        if not auto_toggle.is_enabled(self.base_dir, "satellite_training"):
            print(f"[群星計畫] 🔕 自動點擊已關閉（終端機輸入 /auto 查看開關狀態），"
                  f"已收到訊息但不會自動點擊，請自行手動選擇")
            return False  # 不吃掉這則訊息，維持原本「放行給 reaction_rules」的行為

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