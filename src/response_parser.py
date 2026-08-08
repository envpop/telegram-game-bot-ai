"""
第三層（server 分支）：處理 source_type == SERVER 的訊息。

SourceClassifier 已經先分出三種 server 子類型：
    ServerSubtype.RESPONSE            一般指令回應
    ServerSubtype.EDIT                server 編輯既有訊息（清按鈕、推進劇情）
    ServerSubtype.AUTHOR_ANNOUNCEMENT 穿插在 server 回應中的作者更新公告

這裡先建立空殼、只負責分流到對應 route，不做內容結構化。之後要做
「回應內容結構化」（例如把背包欄位、戰鬥結果解析出來）時，各自往下長成
獨立的 parser（例如 InventoryResponseParser、BattleResultParser），
掛在這一層底下，不要塞回 CommandParser。
"""

from .source_classifier import ServerSubtype

_ROUTE_MAP = {
    ServerSubtype.RESPONSE: "server_response_flow",
    ServerSubtype.EDIT: "server_edit_flow",
    ServerSubtype.AUTHOR_ANNOUNCEMENT: "author_announcement_flow",
}


class ServerResponseParser:
    """負責解析『server 回應』內容本身（目前只分流，尚未做內容結構化）。"""

    def parse(self, record, source_subtype):
        # TODO: 依 source_subtype 進一步拆到對應的細部 parser：
        #   RESPONSE            -> 依訊息內容特徵（背包/行情/戰鬥...）分派到各自 parser
        #   EDIT                -> 通常對應「清除按鈕」或「劇情推進」，可能只需記錄 message_id 更新
        #   AUTHOR_ANNOUNCEMENT -> 內容格式接近公告，未來可能可以共用 flow_parser 裡的邏輯
        return {
            "route": _ROUTE_MAP.get(source_subtype, "server_response_flow"),
            "parsed": False,  # 尚未做內容結構化，先只回傳分流結果
        }
