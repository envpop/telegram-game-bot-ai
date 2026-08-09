"""
parser.py —— 轉接器（adapter）

實際的解析邏輯已經搬進 parsing/ 資料夾（message_router.py /
source_classifier.py / command_parser.py / response_parser.py /
flow_parser.py / config.py / response_shapes/）。

這個檔案只負責把舊的 import 路徑接到新的位置，讓 main.py 裡原本的

    from parser import MessageRouter

不用跟著改。之後如果要直接用新架構，也可以改成

    from parsing import MessageRouter

兩種寫法效果相同，這個檔案只是過渡期的轉接器，之後如果所有呼叫端都
確定改用 `from parsing import ...`，這個檔案可以直接刪除。
"""

from parsing import (
    MessageRouter,
    CommandParser,
    SourceType,
    AnnouncementSubtype,
    ServerSubtype,
    UserSubtype,
)

__all__ = [
    "MessageRouter",
    "CommandParser",
    "SourceType",
    "AnnouncementSubtype",
    "ServerSubtype",
    "UserSubtype",
]