import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REGISTRY_FILE = BASE_DIR / "config" / "command_registry.json"


# ============================================================
# 來源分類設定（可依實際觀察持續調整 / 擴充）
# ============================================================

# 專門「廣播」遊戲事件的公告頻道，目前確認只有一個，且固定不變
ANNOUNCEMENT_CHAT_ID = -1004431989174  # 摸摸熊戰鬥陀螺

# raw 資料中實際觀察到的 event_type 值（注意是 "edited"，不是 "edit"）
EVENT_TYPE_NEW = "new"
EVENT_TYPE_EDITED = "edited"


class SourceType:
    EMPTY = "empty"
    ANNOUNCEMENT = "announcement"
    SERVER = "server"
    USER = "user"


class AnnouncementSubtype:
    SYSTEM_EVENT = "system_event"          # 系統定時／特定事件公告（如王的相位變化）
    ENV_CHANGE = "environment_change"      # 其他玩家進度／指令造成的環境變化公告
    UNKNOWN = "unknown"                    # 尚無法細分，先歸類待補規則


class ServerSubtype:
    RESPONSE = "response"                  # 一般指令回應（event_type == new）
    EDIT = "edit"                          # server 編輯既有訊息（event_type == edited，常見於清除按鈕、推進劇情）
    AUTHOR_ANNOUNCEMENT = "author_update"  # 穿插在 server 回應中的作者更新公告（見下方說明）


class UserSubtype:
    COMMAND_RECOGNIZED = "command_recognized"
    COMMAND_UNRECOGNIZED = "command_unrecognized"  # 打錯字，或指令表內沒有的 alias
    EMPTY_INPUT = "empty_input"


# ============================================================
# 第一層：來源分類器 —— 在指令解析之前先判斷 raw 訊息屬於哪種來源
# ============================================================

class SourceClassifier:
    """負責把一筆 raw 訊息分類成 公告 / server / 使用者輸入 三種來源之一。"""

    def __init__(self, announcement_chat_id=ANNOUNCEMENT_CHAT_ID,
                 self_user_ids=None, announcement_detector=None):
        # 公告頻道固定只有一個，但保留可傳入覆寫的彈性（例如測試用假頻道）
        self.announcement_chat_id = announcement_chat_id

        # 「自己」目前使用的帳號 ID 清單。不影響 user/server 的結構性判斷
        # （判斷 user 只看 sender_id != chat_id，不依賴特定 ID），
        # 純粹用來標記 is_self，未來換帳號、切換使用者時只要換傳入的清單即可，不用動判斷邏輯。
        self.self_user_ids = set(self_user_ids) if self_user_ids else None

        # 「作者更新公告」目前沒有穩定的內容特徵可判斷（不能保證每次都有「新系統」等關鍵字）。
        # 你提到應該用排除法（例如比對該指令預期的回應數量/樣式，多出來的才是公告），
        # 但這需要「該指令預期會有幾則回應」這類額外資訊，目前尚未建立。
        # 先留一個可插拔的介面：之後要實作排除法時，寫成 function(record) -> bool 傳進來即可，
        # 不需要再改這個類別。目前預設一律不判定為公告，全部歸類成一般 server 回應。
        self.announcement_detector = announcement_detector

    def classify(self, record):
        text = (record.get("text") or "").strip()
        media = record.get("media")
        chat_id = record.get("chat_id")
        sender_id = record.get("sender_id")
        event_type = record.get("event_type", EVENT_TYPE_NEW)

        # 純文字為空，但帶圖片/媒體的訊息不算空（例如劇情用的插圖訊息）
        if not text and not media:
            return {"source_type": SourceType.EMPTY, "source_subtype": None, "is_self": None}

        # 1. 獨立的廣播公告頻道（chat_id 本身就代表公告頻道，固定只有一個）
        if chat_id == self.announcement_chat_id:
            subtype = self._classify_announcement(text)
            return {"source_type": SourceType.ANNOUNCEMENT, "source_subtype": subtype, "is_self": None}

        # 2. 互動頻道內，server 自己發出的訊息（sender_id == chat_id）
        if sender_id is not None and chat_id is not None and sender_id == chat_id:
            if event_type == EVENT_TYPE_EDITED:
                return {"source_type": SourceType.SERVER, "source_subtype": ServerSubtype.EDIT, "is_self": None}

            # TODO: 排除法尚未實作，目前一律視為 RESPONSE。
            # 需要時可傳入 announcement_detector(record) -> bool 來覆寫這個判斷。
            if self.announcement_detector is not None and self.announcement_detector(record):
                return {"source_type": SourceType.SERVER, "source_subtype": ServerSubtype.AUTHOR_ANNOUNCEMENT, "is_self": None}
            return {"source_type": SourceType.SERVER, "source_subtype": ServerSubtype.RESPONSE, "is_self": None}

        # 3. 其餘視為使用者輸入
        is_self = (sender_id in self.self_user_ids) if self.self_user_ids is not None else None
        return {"source_type": SourceType.USER, "source_subtype": None, "is_self": is_self}

    def _classify_announcement(self, text):
        # TODO: 之後可依內容關鍵字（例如是否含玩家名稱 vs 王的名稱）再細分
        # system_event（系統定時／事件觸發） vs environment_change（玩家行為造成的環境變化）
        return AnnouncementSubtype.UNKNOWN


# ============================================================
# 第二層：指令解析器 —— 只在 source_type == user 時才會被呼叫
# ============================================================

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


# ============================================================
# 第三層：整合入口 —— 先分流來源，只有 user 才會進 CommandParser
# ============================================================

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

    def parse(self, record):
        original_text = record.get("text") or ""
        classification = self.classifier.classify(record)
        source_type = classification["source_type"]
        source_subtype = classification["source_subtype"]

        result = {
            "raw_text": original_text,
            "message_id": record.get("message_id"),
            "chat_id": record.get("chat_id"),
            "sender_id": record.get("sender_id"),
            "event_type": record.get("event_type"),
            "has_buttons": bool(record.get("buttons")),
            "has_media": bool(record.get("media")),
            "source_type": source_type,
            "source_subtype": source_subtype,
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

        if source_type == SourceType.EMPTY:
            result["route"] = "ignore"
            return result

        if source_type == SourceType.ANNOUNCEMENT:
            result["route"] = "announcement_flow"
            return result

        if source_type == SourceType.SERVER:
            if source_subtype == ServerSubtype.EDIT:
                result["route"] = "server_edit_flow"
            elif source_subtype == ServerSubtype.AUTHOR_ANNOUNCEMENT:
                result["route"] = "author_announcement_flow"
            else:
                result["route"] = "server_response_flow"
            return result

        # source_type == USER -> 只有這裡才做指令解析
        parsed = self.command_parser.parse_user_text(original_text)
        result.update(parsed)
        return result


if __name__ == "__main__":
    router = MessageRouter()

    tests = [
        {
            "event_type": "new", "chat_id": -1004431989174, "chat_name": "摸摸熊戰鬥陀螺",
            "sender_id": -1004431989174, "message_id": 1059,
            "text": "🌗💥「深海級・無面」的形體崩解重組——進入【終相】(相位 3/3)!",
            "buttons": [],
        },
        {
            "event_type": "new", "chat_id": 8707720905, "chat_name": "摸熊神社",
            "sender_id": 6443180435, "message_id": 829, "text": "背包", "buttons": [],
        },
        {
            "event_type": "new", "chat_id": 8707720905, "chat_name": "摸熊神社",
            "sender_id": 8707720905, "message_id": 830,
            "text": "🐚 新系統｜奇聞軼事錄...", "buttons": [],
        },
        {
            "event_type": "new", "chat_id": 8707720905, "chat_name": "摸熊神社",
            "sender_id": 8707720905, "message_id": 831,
            "text": "🎒 你的背包\n...", "buttons": [],
        },
        {
            "event_type": "edited", "chat_id": 8707720905, "chat_name": "摸熊神社",
            "sender_id": 8707720905, "message_id": 831,
            "text": "🎒 你的背包(已更新)\n...", "buttons": [],
        },
        {
            "event_type": "new", "chat_id": 8707720905, "chat_name": "摸熊神社",
            "sender_id": 6443180435, "message_id": 832, "text": "背胞", "buttons": [],
        },
        {
            # 文字為空但帶圖片的劇情訊息，不應被歸類成 empty
            "event_type": "new", "chat_id": 8707720905, "chat_name": "摸熊神社",
            "sender_id": 8707720905, "message_id": 824, "text": "",
            "buttons": [{"row": 1, "column": 1, "text": "▶ 繼續"}],
            "media": {"type": "MessageMediaPhoto", "has_photo": True},
        },
    ]

    for record in tests:
        result = router.parse(record)
        print("=" * 60)
        print(record.get("text", "")[:30])
        print(result)