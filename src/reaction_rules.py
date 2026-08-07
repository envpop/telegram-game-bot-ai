"""reaction_rules.py —— 載入與執行一般文字觸發規則。"""

import json
import time

import executor


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

    async def handle(self, text):
        """若文字命中規則就處理；回傳 True 表示至少命中一條規則。"""
        matched = False
        for rule in self.rules:
            if rule["match_pattern"] not in text:
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

        if risk_level == "safe" and rule.get("auto_execute") and action:
            print(f"[反應] 命中規則「{rule_id}」→ 自動執行：{action}")
            await executor.send_now(action, reason=f"規則:{rule_id}")
            return

        print(f"[反應] ⚠️ 命中規則「{rule_id}」，但風險等級為 {risk_level}，"
              f"需要你自行確認並手動執行：{action or '(未指定動作，請自行判斷)'}")
