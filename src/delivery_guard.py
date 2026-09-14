"""短期保留的 Telegram 事件去重器。

網路重連時，Telethon 可能把剛收到的 update 再派送一次。這個模組在
``monitor`` 的入口就擋掉完全相同的訊息版本，避免重複事件不只觸發自動
動作，也重複寫入 raw log、資料同步與畫面輸出。
"""

from collections import OrderedDict
import time
from typing import Hashable


class DeliveryGuard:
    """只把同一訊息的最新內容保留一段有限時間的去重器。"""

    def __init__(self, ttl_seconds: float = 15 * 60, max_entries: int = 5_000):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._seen = OrderedDict()

    def is_duplicate(self, key: Hashable, fingerprint: Hashable, now: float | None = None) -> bool:
        """回傳是否為重複派送，並在不是時記住這個訊息版本。

        ``key`` 通常是 ``(chat_id, message_id)``；``fingerprint`` 必須涵蓋
        會影響判斷的內容。訊息被正常編輯成不同內容時會更新 fingerprint，
        因而不會被誤擋。
        """
        now = time.monotonic() if now is None else now
        self._discard_expired(now)

        previous = self._seen.get(key)
        if previous is not None and previous[0] == fingerprint:
            self._seen.move_to_end(key)
            return True

        self._seen[key] = (fingerprint, now)
        self._seen.move_to_end(key)
        while len(self._seen) > self.max_entries:
            self._seen.popitem(last=False)
        return False

    def _discard_expired(self, now: float) -> None:
        while self._seen:
            _, (_, recorded_at) = next(iter(self._seen.items()))
            if now - recorded_at <= self.ttl_seconds:
                return
            self._seen.popitem(last=False)
