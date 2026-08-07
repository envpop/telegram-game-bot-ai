"""action_dispatcher.py —— 根據 parser 結果協調各自動化處理器。"""

import backpack_watcher
import executor
import inventory_parsers
import satellite_training_strategy
from reaction_rules import ReactionRuleEngine


class ActionDispatcher:
    """接收 parser 的結構化結果，決定是否呼叫 executor 或更新資料。"""

    def __init__(self, base_dir, rules_file, account_id_getter):
        self.base_dir = base_dir
        self.account_id_getter = account_id_getter
        self.rule_engine = ReactionRuleEngine(rules_file)
        # 使用者剛打「培育」後，等待 BOT 第一則回覆；key 是 chat_id。
        self._awaiting_training_reply = {}

    async def dispatch(self, record, parsed):
        if parsed is None:
            return

        source_type = parsed.get("source_type")
        if source_type == "user" and parsed.get("command") == "培育":
            self._awaiting_training_reply[record.get("chat_id")] = True

        if source_type not in ("server", "announcement"):
            return

        text = record.get("text") or ""
        was_awaiting_training_reply = self._awaiting_training_reply.pop(record.get("chat_id"), False)

        if self._handle_inventory_snapshot(source_type, text):
            return

        if await self._handle_backpack(source_type, text):
            return

        if self._handle_item_description(text):
            return

        if await self._handle_satellite_buttons(record, source_type, text, was_awaiting_training_reply):
            return

        await self.rule_engine.handle(text)

    @property
    def account_id(self):
        return self.account_id_getter()

    def _handle_inventory_snapshot(self, source_type, text):
        if source_type != "server":
            return False

        if inventory_parsers.is_my_tops_message(text):
            result = inventory_parsers.parse_my_tops(text)
            inventory_parsers.save_tops_snapshot(self.base_dir, self.account_id, result)
            print(f"[陀螺清單] 已更新，共 {result['total_count']} 顆")
            return True

        if inventory_parsers.is_satellite_catalog_message(text):
            result = inventory_parsers.parse_satellite_catalog(text)
            inventory_parsers.save_satellites_snapshot(self.base_dir, self.account_id, result)
            print(f"[衛星清單] 已更新，共 {result['total_count']} 顆")
            return True

        return False

    async def _handle_backpack(self, source_type, text):
        if source_type != "server" or not backpack_watcher.is_backpack_message(text):
            return False

        result = backpack_watcher.parse_backpack(text)
        backpack_watcher.save_inventory_snapshot(self.base_dir, self.account_id, result)

        new_items = backpack_watcher.find_new_items(self.base_dir, result)
        if new_items:
            print(f"[背包] 發現 {len(new_items)} 個沒看過的道具：{new_items}")
            backpack_watcher.mark_items_as_queried(self.base_dir, new_items)
            commands = [f"道具說明 {name}" for name in new_items]
            await executor.send_sequence(commands, interval_seconds=2, reason="背包新道具自動查詢")
        return True

    def _handle_item_description(self, text):
        desc = backpack_watcher.parse_item_description(text)
        if desc is None:
            return False

        backpack_watcher.save_item_description(self.base_dir, desc["display_name"], desc)
        print(f"[道具說明] 已記錄：{desc['display_name']} → {desc['description']}")
        return True

    async def _handle_satellite_buttons(self, record, source_type, text, was_awaiting_training_reply):
        buttons = record.get("buttons")
        if source_type != "server" or not buttons:
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
        else:
            print(f"[群星計畫] ⚠️ 策略無法判斷要選哪個按鈕，需要人工介入：{text[:40]}...")
        return True
