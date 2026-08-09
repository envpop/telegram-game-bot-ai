"""
入口：MessageRouter.parse(record)

每一筆 raw 訊息都先進這裡。這一層只做兩件事：
    1. 呼叫 SourceClassifier 做來源分類（空訊息 / 公告 / server / user）
    2. 依分類結果分流到對應的下一層 parser

    EMPTY                -> 直接 ignore，不進任何 parser
    ANNOUNCEMENT         -> AnnouncementFlowParser
    SERVER               -> ServerResponseParser
    USER                 -> CommandParser（整個系統裡唯一會做
                            「指令名稱 + 參數 + intent/flow_type」解析的地方）

MessageRouter 本身不理解「背包」「行情」這些遊戲內容是什麼，也不判斷
訊息文字寫了什麼 —— 那些邏輯屬於下一層各自的 parser。
"""

from .config import ANNOUNCEMENT_CHAT_ID, REGISTRY_FILE
from .source_classifier import SourceClassifier, SourceType
from .command_parser import CommandParser
from .response_parser import ServerResponseParser
from .flow_parser import AnnouncementFlowParser


class MessageRouter:
    def __init__(self, registry_file=REGISTRY_FILE,
                 announcement_chat_id=ANNOUNCEMENT_CHAT_ID,
                 self_user_ids=None, announcement_detector=None):
        self.classifier = SourceClassifier(
            announcement_chat_id=announcement_chat_id,
            self_user_ids=self_user_ids,
            announcement_detector=announcement_detector,
        )
        self.command_parser = CommandParser(registry_file)
        self.response_parser = ServerResponseParser()
        self.announcement_parser = AnnouncementFlowParser()

    def _base_result(self, record, classification):
        raw_text = record.get("text") or ""
        return {
            "raw_text": raw_text,
            "display_text": raw_text,  # 預設=原文；ServerResponseParser 對到已知 shape 時會覆寫
             "message_id": record.get("message_id"),
            "chat_id": record.get("chat_id"),
            "sender_id": record.get("sender_id"),
            "event_type": record.get("event_type"),
            "has_buttons": bool(record.get("buttons")),
            "has_media": bool(record.get("media")),
            "source_type": classification["source_type"],
            "source_subtype": classification["source_subtype"],
            "is_self": classification["is_self"],
            "normalized_text": None,
            "recognized": False,
            "command": None,
            "raw_command": None,
            "matched_by": None,
            "arguments": [],
            "category": None,
            "intent": None,
            "flow_type": None,
            "route": None,
            "match_state": None,
            "valid_shape": False,
        }

    def parse(self, record):
        classification = self.classifier.classify(record)
        source_type = classification["source_type"]
        source_subtype = classification["source_subtype"]
        result = self._base_result(record, classification)

        if source_type == SourceType.EMPTY:
            result["route"] = "ignore"
            return result

        if source_type == SourceType.ANNOUNCEMENT:
            result.update(self.announcement_parser.parse(record, source_subtype))
            return result

        if source_type == SourceType.SERVER:
            result.update(self.response_parser.parse(record, source_subtype))
            return result

        # source_type == USER -> 整個系統裡唯一會呼叫 CommandParser 的分支
        parsed = self.command_parser.parse_user_text(result["raw_text"])
        result.update(parsed)
        return result
