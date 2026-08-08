"""
第一層：來源分類

在做任何『指令』或『內容』解析之前，先回答一個問題：
    這筆 raw 訊息是誰發的、屬於哪種來源？

分類結果只有四種 source_type，且彼此互斥：
    EMPTY / ANNOUNCEMENT / SERVER / USER

這一層刻意做得很薄：只看 chat_id / sender_id / event_type / 是否有文字或媒體，
完全不理解訊息「內容」在講什麼。內容層級的判斷（例如這是背包回應還是戰鬥
結果）屬於下一層各自的 parser，不在這裡處理。
"""

from .config import ANNOUNCEMENT_CHAT_ID, EVENT_TYPE_EDITED, EVENT_TYPE_NEW


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
        # 排除法（比對該指令預期的回應數量/樣式，多出來的才是公告）需要額外資訊，
        # 目前尚未建立。先留一個可插拔的介面：之後要實作排除法時，
        # 寫成 function(record) -> bool 傳進來即可，不需要再改這個類別。
        # 目前預設一律不判定為公告，全部歸類成一般 server 回應。
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
