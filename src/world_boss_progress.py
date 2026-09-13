"""
world_boss_progress.py —— 世界王「今天打過哪些王」的個人進度記錄

用王的名字（例如「海溝級・燭龍」）當 key，不用階數——階數推算容易因為
bot 中斷監控而算錯，王的名字是每則訊息（出現／變身／結束／查詢）都各自
獨立帶有的資訊，不需要依賴任何先前累積的狀態去推算。

存檔位置：data/{帳號}/world_boss_progress.json（跟帳號綁定，不是遊戲共通資料）。
換日（台北時間 00:00）後第一次讀取，會自動把「今天打過的王」清單清空重來。

=== 2026-09-06 擴充：從純名字清單改成名字→細節的字典 ===
熊反映 furnace_loop/full_clear 需要知道「這隻王判定成哪個模式」「次數是否
已經確定用完」「爐火有沒有已經跑完一次」，才能在戰報／查詢回覆／爐火完成
這些不同的反應時機正確判斷——原本設計成「流程進行中」的 session 狀態
(is_active() 那一套)，斷線重連後容易變成過時的錯誤記憶(記得在忙，但
遊戲實際狀態早就變了)，熊明確要求拿掉，改成「不確定就查當下的紀錄／
查詢遊戲，不要記住自己在做什麼」。這裡本來就有跨日重置邏輯、本來就是
帳號個人資料，直接擴充最自然，不用另外開檔案。

hit_king_names_today 從 list 改成 dict：
    {"王名": {"mode": "furnace_loop"/"full_clear"/"touch"/None,
              "count_exhausted": bool,
              "furnace_completed": bool}}
has_hit_today()/mark_hit() 這兩個既有函式介面完全不變（呼叫端不用改），
只是內部改成操作 dict 的 key 而不是 list 的元素；讀到舊格式(list)的
檔案會自動遷移成新格式，不會壞掉，只是舊資料沒有 mode/count_exhausted/
furnace_completed 這些細節，一律補預設值(反正舊資料本來就沒有這些資訊，
沒辦法還原)。
"""

import json
from datetime import datetime, timezone, timedelta

from data_store import account_dir

LOCAL_TZ = timezone(timedelta(hours=8))  # 台北時間，跟 executor.py 保持一致

_EMPTY_ENTRY = {"mode": None, "count_exhausted": False, "furnace_completed": False}


def _progress_file(base_dir, account_id):
    return account_dir(base_dir, account_id) / "world_boss_progress.json"


def _today_str():
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def _load(base_dir, account_id):
    """讀取進度，順便處理跨日重置。永遠回傳一份「日期是今天」的乾淨資料，
    hit_king_names_today 一律是 dict 格式（自動遷移舊的 list 格式）。"""
    f = _progress_file(base_dir, account_id)
    if f.exists():
        try:
            with f.open(encoding="utf-8") as fp:
                data = json.load(fp)
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}

    today = _today_str()
    if data.get("date") != today:
        # 換日了（或檔案不存在/壞掉），今天的紀錄從空的開始
        data = {"date": today, "hit_king_names_today": {}}

    names = data.setdefault("hit_king_names_today", {})
    if isinstance(names, list):
        # 舊格式(純名字清單)遷移成新格式，細節欄位一律用預設值。
        data["hit_king_names_today"] = {name: dict(_EMPTY_ENTRY) for name in names}

    return data


def _save(base_dir, account_id, data):
    f = _progress_file(base_dir, account_id)
    with f.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def _entry(data, king_name):
    """取得(或建立)這隻王的記錄，不存在就用預設值建一筆。"""
    return data["hit_king_names_today"].setdefault(king_name, dict(_EMPTY_ENTRY))


def has_hit_today(base_dir, account_id, king_name):
    data = _load(base_dir, account_id)
    return king_name in data["hit_king_names_today"]


def mark_hit(base_dir, account_id, king_name):
    """記錄「今天打過這隻王了」。重複呼叫同一個名字沒有副作用（不會覆蓋
    掉已經記錄過的 mode/count_exhausted/furnace_completed）。"""
    data = _load(base_dir, account_id)
    _entry(data, king_name)  # 不存在就建立，已存在就不動
    _save(base_dir, account_id, data)


def get_mode(base_dir, account_id, king_name):
    """這隻王當初被判定成哪個模式。沒有記錄過就回傳 None——呼叫端要自己
    決定「不知道」時的預設行為，不要假設一定是某個模式。"""
    data = _load(base_dir, account_id)
    entry = data["hit_king_names_today"].get(king_name)
    return entry["mode"] if entry else None


def mark_mode(base_dir, account_id, king_name, mode):
    """記錄這隻王被判定成哪個模式——出現/變身/查詢三個觸發點，
    _current_mode() 判斷出結果時呼叫，之後戰報/查詢/爐火完成這些反應
    時機才能查表知道「這隻王當初是怎麼判定的」，不用重新猜。"""
    data = _load(base_dir, account_id)
    _entry(data, king_name)["mode"] = mode
    _save(base_dir, account_id, data)


def is_count_exhausted(base_dir, account_id, king_name):
    data = _load(base_dir, account_id)
    entry = data["hit_king_names_today"].get(king_name)
    return bool(entry and entry.get("count_exhausted"))


def mark_count_exhausted(base_dir, account_id, king_name):
    """任何一則帶每日次數資訊的訊息(戰報、連續討伐回覆、查詢回覆)只要
    看到次數用完，就呼叫這個標記——變成可查詢的持久事實，不是只存在
    單則訊息的暫時解析結果裡（熊 2026-09-06 明確要求）。重複呼叫沒有
    副作用。"""
    data = _load(base_dir, account_id)
    _entry(data, king_name)["count_exhausted"] = True
    _save(base_dir, account_id, data)


def clear_count_exhausted(base_dir, account_id, king_name):
    """查詢回覆看到「之前記錄是用完的，現在卻沒用完了」時呼叫——代表
    爐火重置剛完成、次數補回來了，把標記清掉，才能正確偵測「下一輪」
    次數用完(full_clear 需要重複偵測很多輪，不清掉的話只有第一輪能
    正確判斷)。"""
    data = _load(base_dir, account_id)
    _entry(data, king_name)["count_exhausted"] = False
    _save(base_dir, account_id, data)


def is_furnace_completed(base_dir, account_id, king_name):
    data = _load(base_dir, account_id)
    entry = data["hit_king_names_today"].get(king_name)
    return bool(entry and entry.get("furnace_completed"))


def mark_furnace_completed(base_dir, account_id, king_name):
    """爐火真的跑完(炸掉)時呼叫。furnace_loop 靠這個判斷「已經做過一次
    了，不用再觸發」；full_clear 不看這個欄位，一律觸發（熊 2026-09-06
    確認：full_clear 不設重置次數上限）。"""
    data = _load(base_dir, account_id)
    _entry(data, king_name)["furnace_completed"] = True
    _save(base_dir, account_id, data)