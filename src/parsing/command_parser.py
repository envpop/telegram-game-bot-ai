"""
第二層：指令解析器

只在 SourceClassifier 判斷 source_type == USER 之後才會被呼叫。
負責把使用者輸入的自由文字，解析成「指令名稱 + 參數 + intent/flow_type」。

server 回應、公告、按鈕流程一律不經過這裡 —— 它們沒有「指令」的概念，
硬塞進來只會讓 CommandParser 長成什麼都要懂的巨獸。各自的內容解析
請看 response_parser.py / flow_parser.py。
"""
import json
import re
from pathlib import Path

from .config import REGISTRY_FILE
from .source_classifier import UserSubtype


class CommandParser:
    def __init__(self, registry_file=REGISTRY_FILE):
        self.registry_file = Path(registry_file)
        self.registry = self.load_registry()
        self.commands = self.registry.get("commands", {})
        self.alias_map = {}
        for command, info in self.commands.items():
            for alias in info.get("aliases", []):
                self.alias_map[alias] = command
        self.candidates = self._build_candidates()

    def _build_candidates(self):
        candidates = []
        for command in self.commands.keys():
            candidates.append((command, command, "canonical"))
        for alias, command in self.alias_map.items():
            candidates.append((alias, command, "alias"))
        candidates.sort(key=lambda x: len(x[0]), reverse=True)
        return candidates

    def load_registry(self):
        with self.registry_file.open("r", encoding="utf-8") as f:
            return json.load(f)

    def normalize_text(self, text):
        text = text or ""
        text = text.strip()
        text = re.sub(r"\u3000", " ", text)
        text = re.sub(r"[：:]", " ", text)
        text = re.sub(r"[，,]", " ", text)
        text = re.sub(r"[／/]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    # 無空格時，緊接在指令後面的字元必須符合這個 pattern，才視為「指令+參數」
    # 而不是另一個剛好以同樣文字開頭的中文指令（例如「行情」跟「行情表」）。
    # 只允許純數字/英文緊貼指令，因為中文參數（名字/代稱等）沒有空格會跟指令詞黏在一起、無法切分邊界。
    _BARE_ARG_PATTERN = re.compile(r"^[A-Za-z0-9]")

    def find_command(self, text):
        normalized = self.normalize_text(text)
        if not normalized:
            return None

        for candidate, canonical, matched_by in self.candidates:
            if normalized == candidate:
                return {
                    "raw_command": candidate,
                    "canonical_command": canonical,
                    "matched_by": matched_by,
                    "remaining": "",
                }

            if not normalized.startswith(candidate):
                continue

            rest = normalized[len(candidate):]

            # 情況一：指令後面直接接空格，例如「出戰 1」
            if rest.startswith(" "):
                remaining = rest.strip()
                return {
                    "raw_command": candidate,
                    "canonical_command": canonical,
                    "matched_by": matched_by,
                    "remaining": remaining,
                }

            # 情況二：指令後面無空格直接接純數字/英文參數，例如「出戰1」
            if rest and self._BARE_ARG_PATTERN.match(rest):
                remaining = rest.strip()
                return {
                    "raw_command": candidate,
                    "canonical_command": canonical,
                    "matched_by": matched_by,
                    "remaining": remaining,
                }
        return None

    def split_arguments(self, argument_text):
        if not argument_text:
            return []
        parts = re.split(r"\s+", argument_text.strip())
        return [p for p in parts if p]

    def resolve_behavior(self, command, info, arguments):
        intent = info.get("intent")
        flow_type = info.get("flow_type")
        arg_count = len(arguments)

        if command == "戰鬥陀螺" or flow_type == "command_catalog" or intent == "catalog":
            route = "catalog_worker"
            match_state = "catalog_command"
        elif intent == "chart" or flow_type == "market_chart":
            route = "chart_worker"
            match_state = "recognized"
        else:
            route = "parse"
            match_state = "recognized"

        if command == "行情" and arg_count == 0 and intent == "chart":
            valid_shape = True
        else:
            expected = info.get("arguments", [])
            valid_shape = len(expected) <= arg_count

        return {
            "intent": intent,
            "flow_type": flow_type,
            "route": route,
            "match_state": match_state,
            "valid_shape": valid_shape,
        }

    def parse_user_text(self, text):
        """只處理『使用者輸入』的文字，回傳指令解析結果（不含來源資訊，由上層合併）。"""
        normalized_text = self.normalize_text(text)

        base = {
            "normalized_text": normalized_text,
            "recognized": False,
            "command": None,
            "raw_command": None,
            "matched_by": None,
            "arguments": [],
            "category": None,
            "intent": None,
            "flow_type": None,
            "route": "review_queue",
            "match_state": "unknown_command_candidate",
            "valid_shape": False,
            "user_subtype": UserSubtype.COMMAND_UNRECOGNIZED,
        }

        if not normalized_text:
            base["route"] = "ignore"
            base["match_state"] = "empty"
            base["user_subtype"] = UserSubtype.EMPTY_INPUT
            return base

        match = self.find_command(text)
        if match is None:
            # 指令表內找不到 -> 可能是打錯字，或表內沒出現過的 alias
            return base

        canonical = match["canonical_command"]
        info = self.commands.get(canonical, {})
        argument_text = match["remaining"]
        arguments = self.split_arguments(argument_text)
        behavior = self.resolve_behavior(canonical, info, arguments)

        base.update({
            "recognized": True,
            "command": canonical,
            "raw_command": match["raw_command"],
            "matched_by": match["matched_by"],
            "arguments": arguments,
            "category": info.get("category"),
            "intent": behavior["intent"],
            "flow_type": behavior["flow_type"],
            "route": behavior["route"],
            "match_state": behavior["match_state"],
            "valid_shape": behavior["valid_shape"],
            "user_subtype": UserSubtype.COMMAND_RECOGNIZED,
        })
        return base
