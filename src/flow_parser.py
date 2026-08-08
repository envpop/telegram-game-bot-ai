"""
第三層（公告 / 按鈕流程分支）。

負責 source_type == ANNOUNCEMENT，以及未來要處理的『按鈕流程』（連續劇情、
選項按鈕構成的多步驟互動）。這兩者都不是「指令」，本質上是被動接收的
系統輸出或狀態機，不該塞進 CommandParser。
"""

from .source_classifier import AnnouncementSubtype  # noqa: F401  (保留給未來細分邏輯使用)


class AnnouncementFlowParser:
    """負責解析公告頻道內容（目前只分流，尚未做內容結構化）。"""

    def parse(self, record, source_subtype):
        # TODO: 依內容關鍵字細分 system_event vs environment_change
        return {
            "route": "announcement_flow",
            "parsed": False,
        }


class ButtonFlowParser:
    """
    負責追蹤『按鈕流程』狀態（例如劇情選項、連續多步驟的互動按鈕）。

    目前尚未有資料模型，先保留介面：之後應該用 message_id 或某種
    session key 把同一個流程的多筆訊息串起來，記錄目前走到哪一步。
    MessageRouter 尚未接上這個 parser（按鈕流程目前仍走
    server_response_flow / server_edit_flow），待流程資料模型確定後再接上。
    """

    def parse(self, record):
        # TODO: 尚未實作
        return {
            "route": "button_flow",
            "parsed": False,
        }
