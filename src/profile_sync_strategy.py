"""
profile_sync_strategy.py —— 陀螺／衛星／背包／道具說明 → 個人資料庫同步

這四種伺服器回應的共同點：都不需要「決定要不要送出遊戲指令」，看到就是把
最新狀態存進 data/{帳號ID}/（道具說明是共通資料，存進 data/common/）。
唯一的例外是背包出現沒看過的道具時，需要回頭查詢「道具說明 xxx」——但那
仍然只是「存檔完，順便查一下沒看過的東西」，不是策略判斷，所以四種放在
同一支檔案處理，而不是分散在 main.py 的 dispatch_action 裡各自一段 if。

跟 world_boss_strategy / satellite_training_strategy 的分工原則一致：
這裡只負責「判斷 + 存檔」，真正呼叫 executor 送出指令的動作留給呼叫端
（main.py）執行——這支檔案完全不 import executor。

用法：
    import profile_sync_strategy

    result = profile_sync_strategy.handle_server_message(text, BASE_DIR, ACCOUNT_ID)
    if result["handled"]:
        print(result["log"])
        if result["commands"]:
            await executor.send_sequence(
                result["commands"], interval_seconds=2, reason=result["commands_reason"]
            )
        return
"""

import time

import backpack_watcher
import inventory_parsers


def _no_match():
    return {"handled": False, "log": None, "commands": [], "commands_reason": None}


def _handled(log, commands=None, commands_reason=None):
    return {
        "handled": True,
        "log": log,
        "commands": commands or [],
        "commands_reason": commands_reason,
    }


# ============================================================
# 多頁／多則組裝狀態（陀螺清單、衛星圖鑑都可能因為內容太多，
# 被遊戲主動分頁或被 TG 從中間硬切，一次收不到完整內容）
# ============================================================
#
# key 用 account_id：這支 bot 一次只服務一個登入中的帳號（換帳號需要
# 重啟，見 credentials.py / accounts.json 既有限制），不需要再拆 chat_id。
#
# 判斷「接下來這則不認得格式的伺服器訊息，是不是接續內容」的邏輯很單純：
# 只要這個帳號目前「正在組裝中」，就先試著接上去、重新解析；
# 接完發現還是不完整，就繼續等下一則；沒有正在組裝中，才真的當無關訊息忽略。
# 這是故意簡化的判斷（不看訊息間隔時間），因為陀螺清單／衛星圖鑑的分頁／
# 分則訊息都是遊戲/TG 緊接著送出，中間插入其他無關伺服器訊息的機率極低；
# 如果之後發現誤吃到不相關訊息，可以再加時間窗限制。

_pending_assembly = {}  # account_id -> {"kind": "tops"|"satellite"|"bindings", "parts": [str, ...], "started_at": float}

_ASSEMBLY_STALE_SECONDS = 30  # 組裝卡超過這麼久還沒完成就視為異常放棄，避免舊狀態一直誤吃之後的訊息


def _start_assembly(account_id, kind, text):
    _pending_assembly[account_id] = {
        "kind": kind,
        "parts": [text],
        "started_at": time.time(),
    }


def _get_pending(account_id):
    pending = _pending_assembly.get(account_id)
    if pending is None:
        return None
    if time.time() - pending["started_at"] > _ASSEMBLY_STALE_SECONDS:
        del _pending_assembly[account_id]
        return None
    return pending


def _clear_pending(account_id):
    _pending_assembly.pop(account_id, None)


def handle_server_message(text, base_dir, account_id):
    """依序比對陀螺清單／衛星圖鑑／背包／道具說明四種格式。

    四選一，命中第一個符合的格式就直接存檔、回傳，不會繼續比對後面幾種
    （四種訊息格式彼此不會重疊，所以用 if/return 依序試沒有互斥問題）。

    回傳格式：
        {"handled": bool, "log": str|None, "commands": [str, ...], "commands_reason": str|None}
    handled=False 代表這則訊息不屬於這四種，呼叫端應該繼續往下跑其他判斷。
    """
    if inventory_parsers.is_my_tops_message(text):
        return _handle_tops_start(text, base_dir, account_id)

    if inventory_parsers.is_satellite_catalog_message(text):
        return _handle_satellite_start(text, base_dir, account_id)

    if inventory_parsers.is_bindings_message(text):
        return _handle_bindings_start(text, base_dir, account_id)

    if backpack_watcher.is_backpack_message(text):
        result = backpack_watcher.parse_backpack(text)
        backpack_watcher.save_inventory_snapshot(base_dir, account_id, result)

        new_items = backpack_watcher.find_new_items(base_dir, result)
        if not new_items:
            return _handled("[背包] 已更新")

        backpack_watcher.mark_items_as_queried(base_dir, new_items)
        commands = [f"道具說明 {name}" for name in new_items]
        return _handled(
            f"[背包] 發現 {len(new_items)} 個沒看過的道具：{new_items}",
            commands=commands,
            commands_reason="背包新道具自動查詢",
        )

    desc = backpack_watcher.parse_item_description(text)
    if desc is not None:
        backpack_watcher.save_item_description(base_dir, desc["display_name"], desc)
        return _handled(f"[道具說明] 已記錄：{desc['display_name']} → {desc['description']}")

    # 沒有比對到任何已知的開頭格式：如果這個帳號目前有正在組裝中的清單
    # （陀螺清單／衛星圖鑑被拆成多則訊息還沒收完），這則很可能是接續內容，
    # 試著接上去；沒有組裝中的狀態，才是真的無關訊息，忽略。
    pending = _get_pending(account_id)
    if pending is not None:
        return _continue_assembly(pending, text, base_dir, account_id)

    return _no_match()


def _handle_tops_start(text, base_dir, account_id):
    result = inventory_parsers.parse_my_tops(text)
    if result["is_complete"]:
        inventory_parsers.save_tops_snapshot(base_dir, account_id, result)
        return _handled(f"[陀螺清單] 已更新，共 {result['total_count']} 顆")

    _start_assembly(account_id, "tops", text)
    return _handled(
        f"[陀螺清單] 內容被分頁，等待後續分頁中"
        f"（目前 {result['total_count']}/{result['declared_count']} 顆）"
    )


def _handle_satellite_start(text, base_dir, account_id):
    result = inventory_parsers.parse_satellite_catalog(text)
    if result["is_complete"]:
        inventory_parsers.save_satellites_snapshot(base_dir, account_id, result)
        return _handled(f"[衛星清單] 已更新，共 {result['total_count']} 顆")

    _start_assembly(account_id, "satellite", text)
    return _handled(
        f"[衛星清單] 內容被截斷，等待後續分則中"
        f"（目前 {result['total_count']}/{result['declared_count']} 顆）"
    )


def _handle_bindings_start(text, base_dir, account_id):
    result = inventory_parsers.parse_bindings(text)
    if result["is_complete"]:
        return _finish_bindings(result, base_dir, account_id)

    _start_assembly(account_id, "bindings", text)
    return _handled(
        f"[綁定一覽] 內容被截斷，等待後續分則中"
        f"（目前 {result['total_count']}/{result['declared_count']} 顆）"
    )


def _finish_bindings(bindings_result, base_dir, account_id):
    """綁定一覽解析完整後：讀回既有的陀螺清單快照，合併綁定資料，存回 tops.json。

    這裡故意不另外存一份 bindings.json——綁定資料本來就是依附在陀螺身上的
    養成資訊，合併進 tops.json 的 detailed 裡才是使用時真正需要的形狀，
    分開存反而多一道「要用時還要自己 join」的麻煩（討論見對話紀錄）。
    """
    tops_result = inventory_parsers.load_tops_snapshot(base_dir, account_id)
    if tops_result is None:
        return _handled(
            f"[綁定一覽] 解析完成（共 {bindings_result['total_count']} 顆），"
            f"但找不到既有的陀螺清單快照，無法合併——請先觸發一次「我的陀螺」再重新查詢綁定一覽"
        )

    inventory_parsers.annotate_special_source(tops_result.get("detailed", []), base_dir, account_id)
    merge_stats = inventory_parsers.merge_bindings_into_tops(tops_result, bindings_result)
    inventory_parsers.save_tops_snapshot(base_dir, account_id, tops_result)

    log = f"[綁定一覽] 已合併進陀螺清單，配對成功 {merge_stats['matched_count']}/{merge_stats['total_bindings']} 顆"
    if merge_stats["unmatched_binding_count"]:
        log += f"（⚠️ {merge_stats['unmatched_binding_count']} 筆找不到對應陀螺，詳見 log）"
    return _handled(log)


def _continue_assembly(pending, text, base_dir, account_id):
    pending["parts"].append(text)
    merged = inventory_parsers.merge_message_parts(pending["parts"])

    if pending["kind"] == "tops":
        result = inventory_parsers.parse_my_tops(merged)
        if result["is_complete"]:
            inventory_parsers.save_tops_snapshot(base_dir, account_id, result)
            _clear_pending(account_id)
            return _handled(f"[陀螺清單] 分頁組裝完成，共 {result['total_count']} 顆")
        return _handled(
            f"[陀螺清單] 仍在等待後續分頁"
            f"（目前 {result['total_count']}/{result['declared_count']} 顆）"
        )

    if pending["kind"] == "bindings":
        result = inventory_parsers.parse_bindings(merged)
        if result["is_complete"]:
            _clear_pending(account_id)
            return _finish_bindings(result, base_dir, account_id)
        return _handled(
            f"[綁定一覽] 仍在等待後續分則"
            f"（目前 {result['total_count']}/{result['declared_count']} 顆）"
        )

    result = inventory_parsers.parse_satellite_catalog(merged)
    if result["is_complete"]:
        inventory_parsers.save_satellites_snapshot(base_dir, account_id, result)
        _clear_pending(account_id)
        return _handled(f"[衛星清單] 分則組裝完成，共 {result['total_count']} 顆")
    return _handled(
        f"[衛星清單] 仍在等待後續分則"
        f"（目前 {result['total_count']}/{result['declared_count']} 顆）"
    )