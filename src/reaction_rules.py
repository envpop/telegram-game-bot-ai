"""reaction_rules.py —— 載入與執行一般文字觸發規則。

action 欄位可以照舊寫一般文字指令（沿用原本行為，直接 send_now），
也可以寫成 "/sched ..." 語法，這樣就能用上 delay/rep/interval/alias/click: 等能力，
例如 "/sched click:強攻" 或 "/sched delay=3s alias=備戰 T0001 T0002"。
click: 步驟怎麼找到按鈕、怎麼點，交給 scheduler 內部預設呼叫的
executor.click_button_by_text 處理，這裡不用管。

watch_chat 是規則的可選欄位：規則沒寫就跟以前一樣不限聊天室；
要限定某個頻道才觸發，規則裡加一行 "watch_chat": "摸摸熊戰鬥陀螺" 即可。
"""

import json
import time

import executor
import scheduler


class ReactionRuleEngine:
    """套用 reaction_rules.json 的安全自動回應規則。"""

    def __init__(self, rules_file):
        self.rules_file = rules_file
        self.rules = self.load_rules()
        self._last_fired_at = {}

    def load_rules(self):
        with open(self.rules_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("rules", [])

    async def handle(self, chat_name, text):
        """若文字命中規則就處理；回傳 True 表示至少命中一條規則。"""
        matched = False
        for rule in self.rules:
            if rule["match_pattern"] not in text:
                continue

            watch_chat = rule.get("watch_chat")
            if watch_chat and watch_chat != chat_name:
                continue

            matched = True
            rule_id = rule["id"]
            cooldown = rule.get("cooldown_seconds", 60)
            last_fired = self._last_fired_at.get(rule_id, 0)
            if time.time() - last_fired < cooldown:
                continue

            self._last_fired_at[rule_id] = time.time()
            await self._execute_rule(rule)
        return matched

    async def _execute_rule(self, rule):
        rule_id = rule["id"]
        risk_level = rule.get("risk_level")
        action = rule.get("action")

        if not (risk_level == "safe" and rule.get("auto_execute") and action):
            print(f"[反應] ⚠️ 命中規則「{rule_id}」，但風險等級為 {risk_level}，"
                  f"需要你自行確認並手動執行：{action or '(未指定動作，請自行判斷)'}")
            return

        print(f"[反應] 命中規則「{rule_id}」→ 自動執行：{action}")

        if not action.strip().startswith("/sched"):
            await executor.send_now(action, reason=f"規則:{rule_id}")
            return

        try:
            parsed_action = scheduler.parse_sched(action.strip())
        except scheduler.SchedParseError as e:
            print(f"[反應] ⚠️ 規則「{rule_id}」的 /sched 動作語法錯誤：{e}")
            return
        if isinstance(parsed_action, scheduler.SchedControl):
            print(f"[反應] ⚠️ 規則「{rule_id}」的動作不能是 list/cancel 這類管理指令：{action}")
            return

        job_id = scheduler.schedule(parsed_action)
        print(f"[反應] 已排程 {job_id}：{parsed_action.summary}")
