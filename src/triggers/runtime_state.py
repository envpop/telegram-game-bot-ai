# -*- coding: utf-8 -*-
"""
runtime_state.py —— 觸發模組共用的「跨訊息暫存狀態」存取層。

純記憶體，不落地存檔——這類狀態本來就只在 process 存活期間有意義，
重啟後自然歸零是對的行為（不像 tops.json 這種要跨重啟保留的資料）。

=== 為什麼要有這個 ===
搬移前，「使用者剛打培育指令，等待 BOT 第一則回覆」這個狀態直接掛在
ActionDispatcher 實例的 self._awaiting_training_reply 字典上，是為了這
一個用途量身寫的。之後複雜觸發變多（例如：櫻花道具的 5 分鐘免冷卻窗口，
需要一個「暫停到某個時間點」的狀態），如果每種狀態都各自長一個實例屬性，
ActionDispatcher 會越長越雜。

先抽成一個共用的 key-value 存取層，之後新增一種狀態只要換一個 key，
不用改 ActionDispatcher、不用改 TriggerContext。

=== 兩種用法 ===
一次性旗標（mark / consume）：用完就清掉，例如「等待下一則回覆」。
    runtime_state.mark("awaiting_training_reply", chat_id)
    runtime_state.consume("awaiting_training_reply", chat_id)  # 取出並清除

有效期限狀態（set_until / is_active）：不需要手動消費，過期自動視為不存在，
例如「櫻花模式期間暫停其他 trigger 到某個時間點」。
    runtime_state.set_until("suspend_triggers", "global", until_epoch_seconds)
    runtime_state.is_active("suspend_triggers", "global")

key 建議用穩定的固定字串（例如 "awaiting_training_reply"），sub_key 用會
變動的識別碼（例如 chat_id）；不需要區分 sub_key 的狀態（例如全域暫停），
sub_key 傳 None 或固定字串都可以。

=== 重複派送偵測 ===
2026-08-22 發現：monitor.py 對同一則訊息的編輯事件完全沒有防重複機制，
如果 Telethon（例如連線重連時）對同一次編輯重複觸發事件，會造成
action_dispatcher.py 對同一次內容跑兩次完整流程，導致像「結業」這種
send_now 指令被送出兩次。is_duplicate_delivery() 用來擋掉這種情況：
同一個 (chat_id, message_id) 如果文字內容跟上次處理過的完全一樣，
視為重複派送；文字不同（例如遊戲把同一則訊息編輯成下一回合的新內容，
這是正常流程）則正常放行。
"""
import time

_flags = {}   # (key, sub_key) -> True
_timed = {}   # (key, sub_key) -> expires_at（epoch seconds）

# (chat_id, message_id) -> 上次處理過的文字內容。用 dict 手動維護簡易上限，
# 避免長時間執行下無限增長（bot 是常駐 process，這裡不加上限會慢慢吃記憶體）。
_last_processed_text = {}
_MAX_TRACKED_MESSAGES = 2000


def mark(key, sub_key=None) -> None:
    """設定一次性旗標。"""
    _flags[(key, sub_key)] = True


def consume(key, sub_key=None) -> bool:
    """取出並清除旗標，回傳原本是否為 True（沒設定過也回傳 False，不會噴例外）。"""
    return _flags.pop((key, sub_key), False)


def set_until(key, sub_key, expires_at: float) -> None:
    """設定一個會在 expires_at（epoch seconds）之後自動失效的狀態。"""
    _timed[(key, sub_key)] = expires_at


def clear(key, sub_key=None) -> None:
    """手動清除，不管是一次性旗標還是有效期限狀態都會清掉。"""
    _flags.pop((key, sub_key), None)
    _timed.pop((key, sub_key), None)


def is_active(key, sub_key=None) -> bool:
    """有效期限狀態是否還在生效中（過期或從沒設定過都回傳 False）。
    過期時順手清掉，避免字典裡累積用不到的舊資料。"""
    expires_at = _timed.get((key, sub_key))
    if expires_at is None:
        return False
    if time.time() >= expires_at:
        _timed.pop((key, sub_key), None)
        return False
    return True


def is_duplicate_delivery(chat_id, message_id, text) -> bool:
    """判斷這則訊息是不是「內容完全相同」的重複派送。

    同一個 message_id 被編輯成新內容是正常流程（遊戲每回合都編輯同一則
    訊息），只有文字內容也完全相同才視為重複——這種情況正常不該發生，
    出現通常代表 Telethon／連線層對同一次編輯重複觸發了事件。

    是重複的話回傳 True（呼叫端應該直接略過，不執行任何動作）；不是
    重複的話回傳 False，並記住這次內容供下次比對。
    """
    key = (chat_id, message_id)
    if _last_processed_text.get(key) == text:
        return True

    _last_processed_text[key] = text
    if len(_last_processed_text) > _MAX_TRACKED_MESSAGES:
        # 超過上限，丟掉最舊的一筆（dict 從 Python 3.7 起保證插入順序，
        # 第一個 key 就是最舊插入的）。不追求精確 LRU，這裡只是防止
        # 無限增長，簡單丟最舊的就夠用。
        oldest_key = next(iter(_last_processed_text))
        _last_processed_text.pop(oldest_key, None)

    return False