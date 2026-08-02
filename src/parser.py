import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REGISTRY_FILE = BASE_DIR / "config" / "command_registry.json"


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
            if normalized.startswith(candidate + " "):
                remaining = normalized[len(candidate):].strip()
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

    def classify_command(self, command, arguments, info=None):
        info = info or {}
        category = info.get("category")
        intent = info.get("intent")
        expected = info.get("arguments", [])
        arg_count = len(arguments)
        issues = []
        valid_shape = True

        if len(expected) > arg_count:
            valid_shape = False
            issues.append("missing_arguments")

        if command == "戰鬥陀螺":
            command_type = "registry_reference"
            flow_type = "command_catalog"
        elif intent in {"query", "query_only"}:
            command_type = "query"
            flow_type = info.get("flow_type")
        elif intent in {"action", "execute"}:
            command_type = "action"
            flow_type = info.get("flow_type")
        elif intent == "chart":
            command_type = "chart"
            flow_type = info.get("flow_type")
        elif category in {"announcement", "notice"}:
            command_type = "announcement"
            flow_type = info.get("flow_type")
        elif arg_count == 0:
            command_type = "simple_command"
            flow_type = info.get("flow_type")
        else:
            command_type = "parameterized_command"
            flow_type = info.get("flow_type")

        chart_candidate = flow_type == "market_chart"

        return {
            "command_type": command_type,
            "flow_type": flow_type,
            "argument_count": arg_count,
            "valid_shape": valid_shape,
            "issues": issues,
            "chart_candidate": chart_candidate,
        }

    def parse_text(self, text):
        original_text = text or ""
        normalized_text = self.normalize_text(original_text)
        result = {
            "raw_text": original_text,
            "normalized_text": normalized_text,
            "event_type": "text",
            "recognized": False,
            "command": None,
            "raw_command": None,
            "matched_by": None,
            "arguments": [],
            "arguments_text": "",
            "category": None,
            "intent": None,
            "command_type": None,
            "flow_type": None,
            "argument_count": 0,
            "valid_shape": False,
            "issues": [],
            "chart_candidate": False,
        }

        if not normalized_text:
            result["event_type"] = "empty"
            return result

        match = self.find_command(original_text)
        if match is None:
            result["event_type"] = "possible_command"
            return result

        canonical = match["canonical_command"]
        info = self.commands.get(canonical, {})
        argument_text = match["remaining"]
        arguments = self.split_arguments(argument_text)
        shape = self.classify_command(canonical, arguments, info)

        result.update({
            "event_type": "command",
            "recognized": True,
            "command": canonical,
            "raw_command": match["raw_command"],
            "matched_by": match["matched_by"],
            "arguments": arguments,
            "arguments_text": argument_text,
            "category": info.get("category"),
            "intent": info.get("intent"),
            "command_type": shape["command_type"],
            "flow_type": shape["flow_type"],
            "argument_count": shape["argument_count"],
            "valid_shape": shape["valid_shape"],
            "issues": shape["issues"],
            "chart_candidate": shape["chart_candidate"],
        })
        return result

    def parse_record(self, record):
        record = record or {}
        text = record.get("text") or ""
        buttons = record.get("buttons") or []
        media = record.get("media")
        image_path = record.get("image_path")
        sender_id = record.get("sender_id")
        chat_id = record.get("chat_id")
        chat_name = record.get("chat_name")
        event_type = record.get("event_type") or "new"

        has_text = bool(str(text).strip())
        has_buttons = bool(buttons)
        has_media = media is not None or bool(image_path)

        if has_text and has_buttons:
            payload_type = "mixed"
        elif has_text:
            payload_type = "text"
        elif has_buttons:
            payload_type = "button"
        elif has_media:
            payload_type = "media"
        else:
            payload_type = "empty"

        if sender_id == chat_id:
            source_type = "server_announcement"
        elif has_text and str(chat_name).strip():
            source_type = "dialog_message"
        else:
            source_type = "unknown"

        parsed = None
        route = "ignore"
        flow_type = None
        flow_stage = None
        chart_candidate = False

        if payload_type in {"text", "mixed"}:
            parsed = self.parse_text(text)
            flow_type = parsed.get("flow_type")
            chart_candidate = parsed.get("chart_candidate", False)
            if parsed.get("recognized"):
                route = "parse"
                if chart_candidate:
                    route = "chart_worker"
                    flow_stage = "command"

        if payload_type == "media":
            route = "chart_worker" if image_path else "ignore"
            flow_stage = "chart"
            chart_candidate = True

        if flow_type == "command_catalog":
            flow_stage = "catalog"

        normalized_text = self.normalize_text(text)

        return {
            "recorded_at": record.get("recorded_at"),
            "event_type": event_type,
            "chat_id": chat_id,
            "chat_name": chat_name,
            "sender_id": sender_id,
            "message_id": record.get("message_id"),
            "message_date": record.get("message_date"),
            "raw_text": text,
            "normalized_text": normalized_text,
            "buttons": buttons,
            "media": media,
            "image_path": image_path,
            "payload_type": payload_type,
            "source_type": source_type,
            "flow_type": flow_type,
            "flow_stage": flow_stage,
            "chart_candidate": chart_candidate,
            "route": route,
            "parsed": parsed,
            "raw_record": record,
        }


if __name__ == "__main__":
    parser = CommandParser()
    tests = [
        "培育",
        "行情",
        "行情 旋核",
        "行情 錢莊",
        "入資 旋核 100",
        "撤資 錢莊 200",
        "我的槓桿",
        "平倉 12345",
        "溫泉旅行 小雫",
        "戰鬥陀螺",
        "完全不知道這是什麼",
    ]

    for text in tests:
        result = parser.parse_text(text)
        print("=" * 60)
        print(text)
        print(result)