"""
第三層（server 分支）：處理 source_type == SERVER 的訊息。

SourceClassifier 已經先分出三種 server 子類型：
    ServerSubtype.RESPONSE            一般指令回應
    ServerSubtype.EDIT                server 編輯既有訊息（清按鈕、推進劇情）
    ServerSubtype.AUTHOR_ANNOUNCEMENT 穿插在 server 回應中的作者更新公告

RESPONSE 子類型底下，內容結構化採「shape 比對」的做法：response_shapes/
資料夾裡每個常用指令一個檔案，各自實作三個函式：
    signature(text) -> bool          這段文字是不是這個 shape
    parse(text) -> dict              抽成結構化資料
    format_for_display(parsed) -> str 組出給 display 用的文字

只涵蓋你們談過要做的「高頻指令」（戰鬥類、投資類），沒對到任何已知
shape 的一律 fallback 回傳原文，不影響尚未支援的指令。

RESPONSE（新訊息）跟 EDIT（原地編輯既有訊息）都會套 shape 比對——
2026-08-14 發現主塔進階戰鬥每一回合是編輯同一則訊息推進（event_type
== "edited"），不是每輪發新訊息，所以 EDIT 不能再排除在 shape 比對外，
否則戰鬥訊息永遠進不到 main_tower_battle_prompt.py。EDIT 底下沒對到
任何已知 shape 的（例如單純清按鈕、推進劇情的編輯）行為不變，一樣
fallback 顯示原文。AUTHOR_ANNOUNCEMENT 這個子類型維持不套 shape 比對，
純分流。
"""

from .source_classifier import ServerSubtype
from .response_shapes import market_contract
from .response_shapes import market_overview
from .response_shapes import market_quote
from .response_shapes import trade_confirmation
from .response_shapes import contract_overview
from .response_shapes import contract_quote
from .response_shapes import world_boss_status
from .response_shapes import world_boss_battle_report
from .response_shapes import main_tower_battle_prompt
from .response_shapes import top_record
from .response_shapes import guard_status
from .response_shapes import satellite_catalog
from .response_shapes import my_tops
from .response_shapes import bindings
from .response_shapes import guard_status
from .response_shapes import guard_clear_outcome
from .response_shapes import guard_battle_prompt
from .response_shapes import active_top_confirmation
from .response_shapes import sub_top_confirmation
from .response_shapes import sub_top_status


_ROUTE_MAP = {
    ServerSubtype.RESPONSE: "server_response_flow",
    ServerSubtype.EDIT: "server_edit_flow",
    ServerSubtype.AUTHOR_ANNOUNCEMENT: "author_announcement_flow",
}

# 會嘗試套 shape 比對的子類型。RESPONSE 是一般新訊息；EDIT 是原地編輯
# 既有訊息（主塔進階戰鬥每回合就是這種），兩者都可能是熱門指令的回應，
# 都要嘗試比對。AUTHOR_ANNOUNCEMENT 不在其中，維持純分流。
_SHAPE_MATCHABLE_SUBTYPES = (ServerSubtype.RESPONSE, ServerSubtype.EDIT)

# 已知的回應「形狀」，依序嘗試比對。新增一個常用指令的 parser，就在這裡加一行。
_KNOWN_SHAPES = [
    market_contract,
    market_overview,
    market_quote,
    trade_confirmation,
    contract_overview,
    contract_quote,
    top_record,
    world_boss_status,
    world_boss_battle_report,
    main_tower_battle_prompt,
    guard_status,
    satellite_catalog,
    my_tops,
    bindings,
    guard_status,
    guard_clear_outcome,
    guard_battle_prompt,
    active_top_confirmation,
    sub_top_confirmation,
    sub_top_status,
]


class ServerResponseParser:
    """負責解析『server 回應』內容本身：已知 shape 做結構化，其餘 fallback 顯示原文。"""

    def parse(self, record, source_subtype):
        raw_text = record.get("text") or ""
        result = {
            "route": _ROUTE_MAP.get(source_subtype, "server_response_flow"),
            "parsed": False,
            "shape": None,
            "structured": None,
            "display_text": raw_text,  # fallback：預設就是原文，未支援的指令行為不變
        }

        if source_subtype not in _SHAPE_MATCHABLE_SUBTYPES:
            return result

        for shape_module in _KNOWN_SHAPES:
            if shape_module.signature(raw_text):
                structured = shape_module.parse(raw_text)
                result.update({
                    "parsed": True,
                    "shape": shape_module.__name__.rsplit(".", 1)[-1],
                    "structured": structured,
                    "display_text": shape_module.format_for_display(structured),
                })
                break

        return result