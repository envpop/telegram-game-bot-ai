# -*- coding: utf-8 -*-
"""
message_buffer.py

處理 TG 自動分則的多頁清單（目前已知：陀螺收藏／衛星圖鑑／綁定一覽）。

問題背景：
    inventory_parsers.py 裡已經有 merge_message_parts() / is_complete /
    declared_count 這套「偵測+合併」機制，但沒有人在正確時機呼叫它們——
    每則 TG 訊息一進來就直接單獨送進 MessageRouter.parse()，所以清單
    第一頁一到就被當成「完整的一則」處理掉了。

=== 2026-08-17 修正：分則邊界可能切在行中間，單一種拼接規則不夠 ===
    實測發現衛星圖鑑分則時，TG 的切割點不保證落在換行字元上——可能切在
    一行技能列表的正中間，導致「原本連續的一行」被硬拆成兩段，重新拼接
    後 regex 誤判產生錯誤資料（例如把技能名字誤認成衛星名字）。

    麻煩的是「補回換行」跟「不補換行」兩種修法都可能是錯的，取決於實際
    切割點：
        - 如果切點真的在行中間 → 不該補換行（補了會把一行拆成兩行）
        - 如果切點在乾淨的換行處，但 Telegram 傳輸時 trim 掉了訊息尾端
          的空白字元（已知行為）→ 應該補回換行（不補會把兩行黏成一行）

    純看文字本身無法判斷是哪一種情況，所以改成兩種拼接方式都試，各自
    丟給 parse_fn() 驗證，挑「解析結果比較完整」的那個——判斷完整度用
    is_complete（優先）跟 total_count（其次，數字愈大代表拼得愈正確，
    因為拼錯通常會讓某幾筆解析失敗、總數變少或跑出異常資料）。這個判斷
    不需要知道 TG 的實際切割規則，兩種候選都算一次、挑比較好的即可。

解法：
    在 monitor.ON_RECORD_CALLBACK 跟 MessageRouter.parse() 之間插一層
    緩衝。只針對「已知會分頁、宣告數量在文字裡」的訊息類型
    （_PAGINATED_SIGNATURES 清單）：第一段先試解析，declared_count 對
    不上 → 進入等待狀態；後續同一個 chat 的新訊息先嘗試串接、重新解析，
    直到 is_complete 為 True 才真正呼叫 on_flush()（通常就是
    MessageRouter.parse() 那條既有路徑）。

    不能放進 monitor.py：它的分層原則是「永遠不 import parser」，這裡
    需要呼叫 inventory_parsers 判斷 is_complete，兩者互斥，只能放在
    monitor 外面（main.py 這一層）。

    只處理 event_type == "new"：TG 自動分則一定是新訊息，不會是編輯
    （主塔戰鬥那種「編輯同一則推進」是完全不同的機制，不會觸發分則）。

用法（main.py）：

    from message_buffer import MessageBuffer

    async def on_record(record):
        parsed = router.parse(record)
        ...（原本的 dispatch/display 邏輯）

    message_buffer = MessageBuffer(on_flush=on_record)
    monitor.ON_RECORD_CALLBACK = message_buffer.handle   # 原本直接指到 on_record，改指到這裡
"""

import asyncio
import logging

from inventory_parsers import (
    is_my_tops_message, parse_my_tops,
    is_satellite_catalog_message, parse_satellite_catalog,
    is_bindings_message, parse_bindings,
)

logger = logging.getLogger(__name__)

# 訊息 signature -> parse 函式。只涵蓋目前已知「宣告數量、可能被 TG 拆成
# 多則」的清單類訊息。之後如果又出現新的多頁清單，這裡加一行即可，
# 不用動其他程式碼。
_PAGINATED_SIGNATURES = [
    (is_my_tops_message, parse_my_tops),
    (is_satellite_catalog_message, parse_satellite_catalog),
    (is_bindings_message, parse_bindings),
]

# 同一個 chat 等續頁的最長等待時間。超過還沒等到下一段，就把現有的直接
# 送出去——寧可顯示不完整（is_complete=False 會留在 structured 裡，
# 下游可以自己決定要不要提醒），也不要無限期卡住、後面的訊息全部塞車。
FLUSH_TIMEOUT_SECONDS = 3.0


def _match_paginated_signature(text):
    for sig_fn, parse_fn in _PAGINATED_SIGNATURES:
        if sig_fn(text):
            return parse_fn
    return None


def _merge_no_separator(parts):
    """候選A：完全原樣接回，不加任何字元——適用於「TG 切割點落在行中間」
    的情況（沒有換行可以補，補了反而是多出來的錯誤字元）。"""
    return "".join(p for p in parts if p)


def _merge_with_newline(parts):
    """候選B：段落之間補一個換行——適用於「TG 切割點本來就在乾淨的換行
    處，但傳輸時訊息尾端的換行被 Telegram trim 掉了」的情況（已知 TG
    行為：trim 掉每則訊息前後的空白字元）。"""
    return "\n".join(p.rstrip("\n") for p in parts if p)


def _pick_best_merge(parts, parse_fn):
    """兩種拼接候選都試著解析，挑「看起來比較完整」的那個。

    純看文字本身無法判斷 TG 實際切在哪裡，所以不猜規則，直接讓
    parse_fn() 的結果說話：
        1. 優先選 is_complete 為 True 的（代表這個拼法讓資料結構完整，
           包含 parse_satellite_catalog() 那種「最後一筆缺槽位」的
           額外檢查也會反映在這個欄位上）
        2. 都不完整（還在等更多分則，這是正常情況）時，選 total_count
           較大的——拼錯的那個候選通常會讓某幾筆卡在中間解析失敗，
           總數會比較少
    """
    candidates = [_merge_no_separator(parts), _merge_with_newline(parts)]
    best_text, best_result = None, None

    for text in candidates:
        result = parse_fn(text)
        if best_result is None:
            best_text, best_result = text, result
            continue

        if result.get("is_complete") and not best_result.get("is_complete"):
            best_text, best_result = text, result
        elif (
            result.get("is_complete") == best_result.get("is_complete")
            and result.get("total_count", 0) > best_result.get("total_count", 0)
        ):
            best_text, best_result = text, result

    return best_text, best_result


class MessageBuffer:
    def __init__(self, on_flush, flush_timeout=FLUSH_TIMEOUT_SECONDS):
        self._on_flush = on_flush
        self._flush_timeout = flush_timeout
        self._pending = {}  # chat_id -> {"parts": [...], "parse_fn": fn, "first_record": dict, "timer": Task}

    async def handle(self, record):
        """monitor.ON_RECORD_CALLBACK 直接指到這個函式（取代原本直接指到
        on_record），介面（一個 async function，吃一個 record）完全不變，
        呼叫端不用另外改寫法。"""
        if record.get("event_type") != "new":
            await self._on_flush(record)
            return

        chat_id = record.get("chat_id")
        text = record.get("text") or ""
        pending = self._pending.get(chat_id)

        if pending is None:
            parse_fn = _match_paginated_signature(text)
            if parse_fn is None:
                await self._on_flush(record)
                return

            result = parse_fn(text)
            if result.get("is_complete") is not False:
                # 完整的一則（is_complete 是 True，或 None 代表沒抓到宣告
                # 數量、無從判斷不完整），不需要進緩衝狀態
                await self._on_flush(record)
                return

            self._start_pending(chat_id, record, parse_fn, text)
            return

        # 已經在等續頁：把這則接進去，兩種拼接候選都試，挑比較完整的
        pending["parts"].append(text)
        pending["timer"].cancel()
        merged_text, result = _pick_best_merge(pending["parts"], pending["parse_fn"])

        if result.get("is_complete") is not False:
            pending["merged_text"] = merged_text  # 給 _flush_pending 用，不重算一次
            await self._flush_pending(chat_id)
            return

        pending["timer"] = asyncio.create_task(self._flush_after_timeout(chat_id))

    def _start_pending(self, chat_id, record, parse_fn, text):
        entry = {"parts": [text], "parse_fn": parse_fn, "first_record": record}
        entry["timer"] = asyncio.create_task(self._flush_after_timeout(chat_id))
        self._pending[chat_id] = entry

    async def _flush_after_timeout(self, chat_id):
        try:
            await asyncio.sleep(self._flush_timeout)
        except asyncio.CancelledError:
            return
        pending = self._pending.get(chat_id)
        if pending is None:
            return
        logger.warning(
            "[訊息緩衝] chat=%s 等續頁逾時（%.1fs），直接送出目前累積的 %d 段（可能不完整）",
            chat_id, self._flush_timeout, len(pending["parts"]),
        )
        await self._flush_pending(chat_id)

    async def _flush_pending(self, chat_id):
        pending = self._pending.pop(chat_id, None)
        if pending is None:
            return
        pending["timer"].cancel()
        # 逾時強制 flush 的情況（_flush_after_timeout 呼叫過來）不會有
        # 事先算好的 merged_text，這裡補算一次；正常完成的情況已經在
        # handle() 裡選過最佳候選，直接複用，不重算。
        merged_text = pending.get("merged_text")
        if merged_text is None:
            merged_text, _ = _pick_best_merge(pending["parts"], pending["parse_fn"])
        record = dict(pending["first_record"])
        record["text"] = merged_text
        await self._on_flush(record)