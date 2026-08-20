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

=== 2026-08-17 收斂 ===
原本這裡自己內建一套分頁組裝機制（_pending_assembly 等），是在
message_buffer.py 出現之前寫的——那時候 MessageRouter 之前沒有任何東西
處理 TG 自動分則，只能自己土法煉鋼等續頁。現在 message_buffer.py 已經
在 MessageRouter.parse() 之前把陀螺清單／衛星圖鑑／綁定一覽組裝完整，
這支檔案收到的內容一定已經是完整的——原本的組裝機制永遠不會被觸發
（續頁在 message_buffer 那層就被吃掉合併了），整套刪除。

同時，陀螺清單／衛星圖鑑／綁定一覽三種訊息不再自己重新呼叫
inventory_parsers.parse_xxx() 解析一次——response_parser.py 底下的
my_tops.py／satellite_catalog.py／bindings.py 這三個 shape 已經解析過了，
這裡直接讀 parsed['shape']／parsed['structured']，只做「存檔」這一件事，
不重工。跟 action_dispatcher.py 裡 _handle_main_tower_battle() 的既有
原則一致：「用 response_parser 已經判斷好的 shape 來確認，而不是重新
對文字做 pattern matching」。

背包／道具說明目前還沒有對應的 response_shapes 檔案（沒有 shape 可以
信任），所以這兩種維持原本讀 parsed['raw_text'] 直接判斷的做法，等之後
真的要做 backpack shape 再一併收斂。

用法：
    import profile_sync_strategy

    result = profile_sync_strategy.handle_server_message(parsed, BASE_DIR, ACCOUNT_ID)
    if result["handled"]:
        print(result["log"])
        if result["commands"]:
            await executor.send_sequence(
                result["commands"], interval_seconds=2, reason=result["commands_reason"]
            )
        return
"""

import json

import backpack_watcher
import forge_result_parser
import inventory_parsers
from pathlib import Path

# response_parser.py 的 _KNOWN_SHAPES 裡，三種訊息各自的 shape 名稱
# （shape_module.__name__.rsplit(".", 1)[-1] 算出來的值，對照
# parsing/response_shapes/ 底下的檔名）。
_SHAPE_MY_TOPS = "my_tops"
_SHAPE_SATELLITE_CATALOG = "satellite_catalog"
_SHAPE_BINDINGS = "bindings"
_SHAPE_ACTIVE_TOP_CONFIRMATION = "active_top_confirmation"
_SHAPE_TOP_RECORD = "top_record"
_SHAPE_SUB_TOP_CONFIRMATION = "sub_top_confirmation"
_SHAPE_SUB_TOP_STATUS = "sub_top_status"
_SHAPE_FORGE_RESULT = "forge_result"


def _no_match():
    return {"handled": False, "log": None, "commands": [], "commands_reason": None}


def _handled(log, commands=None, commands_reason=None):
    return {
        "handled": True,
        "log": log,
        "commands": commands or [],
        "commands_reason": commands_reason,
    }


def handle_server_message(parsed, base_dir, account_id):
    """依 parsed['shape'] 分流陀螺清單／衛星圖鑑／綁定一覽；背包／道具說明
    這兩種還沒有 shape，退回讀 parsed['raw_text'] 直接判斷。

    四選一，命中第一個符合的格式就直接存檔、回傳，不會繼續比對後面幾種。

    回傳格式：
        {"handled": bool, "log": str|None, "commands": [str, ...], "commands_reason": str|None}
    handled=False 代表這則訊息不屬於這四種，呼叫端應該繼續往下跑其他判斷。
    """
    shape = parsed.get("shape")
    structured = parsed.get("structured") or {}

    if shape == _SHAPE_MY_TOPS:
        return _handle_tops(structured, base_dir, account_id)

    if shape == _SHAPE_SATELLITE_CATALOG:
        return _handle_satellite(structured, base_dir, account_id)

    if shape == _SHAPE_BINDINGS:
        return _handle_bindings(structured, base_dir, account_id)

    if shape == _SHAPE_ACTIVE_TOP_CONFIRMATION:
        return _handle_active_top_confirmation(structured, base_dir, account_id)

    if shape == _SHAPE_TOP_RECORD:
        return _handle_top_record_active_sync(structured, base_dir, account_id)

    if shape == _SHAPE_SUB_TOP_CONFIRMATION:
        return _handle_sub_top_confirmation(structured, base_dir, account_id)

    if shape == _SHAPE_SUB_TOP_STATUS:
        return _handle_sub_top_status(structured, base_dir, account_id)

    if shape == _SHAPE_FORGE_RESULT:
        return _handle_forge_result(structured, base_dir, account_id)

    text = parsed.get("raw_text") or ""

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

    return _no_match()


def _incomplete_suffix(structured):
    """message_buffer.py 在等續頁逾時（3 秒）沒等到時，會直接把目前收到的
    內容送出來，這裡 structured['is_complete'] 可能仍是 False——這種情況
    還是照存（部分資料好過完全不存），但在 log 裡老實標注，不要假裝完整。"""
    if structured.get("is_complete") is False:
        return f"（⚠️ 疑似不完整：{structured.get('total_count')}/{structured.get('declared_count')}）"
    return ""


def _handle_tops(structured, base_dir, account_id):
    _enrich_and_save_tops(structured, base_dir, account_id)
    _commit_matching_pending_forge(structured.get("detailed", []), base_dir, account_id)
    return _handled(
        f"[陀螺清單] 已更新，共 {structured.get('total_count')} 顆{_incomplete_suffix(structured)}"
    )


def _enrich_and_save_tops(result, base_dir, account_id):
    """「陀螺收藏」查詢完成後，存檔前先補回兩塊會被覆蓋掉的資料：

    1. annotate_special_source()：不需要綁定資料就能跑，靠陀螺名字比對
       special_tops_catalog.json / cast_tops_catalog.json，補上 element/
       base_name/source_category——這步每次查陀螺收藏都該做，不用等綁定一覽。
    2. carry_over_enrichment()：把舊快照裡已有的 binding（天賦養成資料）
       依 match_key 接回來——這塊只有綁定一覽查詢會提供，陀螺收藏本身沒有，
       不接回就會被整份覆蓋洗掉（2026-08-15 確認的實際 bug）。

    舊快照讀不到（第一次用、還沒存過）就跳過第 2 步，全部 binding 保持 None，
    這是正確的初始狀態，不是錯誤。
    """
    inventory_parsers.annotate_special_source(result["detailed"], base_dir, account_id)

    old = inventory_parsers.load_tops_snapshot(base_dir, account_id)
    if old is not None:
        inventory_parsers.carry_over_enrichment(result["detailed"], old.get("detailed", []))
    else:
        for top in result["detailed"]:
            top["binding"] = None

    inventory_parsers.save_tops_snapshot(base_dir, account_id, result)


def _handle_satellite(structured, base_dir, account_id):
    inventory_parsers.save_satellites_snapshot(base_dir, account_id, structured)
    return _handled(
        f"[衛星清單] 已更新，共 {structured.get('total_count')} 顆{_incomplete_suffix(structured)}"
    )


def _handle_bindings(bindings_result, base_dir, account_id):
    """綁定一覽解析完整後：讀回既有的陀螺清單快照，合併綁定資料，存回 tops.json。

    這裡故意不另外存一份 bindings.json——綁定資料本來就是依附在陀螺身上的
    養成資訊，合併進 tops.json 的 detailed 裡才是使用時真正需要的形狀，
    分開存反而多一道「要用時還要自己 join」的麻煩（討論見對話紀錄）。
    """
    tops_result = inventory_parsers.load_tops_snapshot(base_dir, account_id)
    if tops_result is None:
        return _handled(
            f"[綁定一覽] 解析完成（共 {bindings_result.get('total_count')} 顆），"
            f"但找不到既有的陀螺清單快照，無法合併——請先觸發一次「我的陀螺」再重新查詢綁定一覽"
        )

    inventory_parsers.annotate_special_source(tops_result.get("detailed", []), base_dir, account_id)
    merge_stats = inventory_parsers.merge_bindings_into_tops(tops_result, bindings_result)
    inventory_parsers.save_tops_snapshot(base_dir, account_id, tops_result)

    log = (
        f"[綁定一覽] 已合併進陀螺清單，配對成功 "
        f"{merge_stats['matched_count']}/{merge_stats['total_bindings']} 顆"
        f"{_incomplete_suffix(bindings_result)}"
    )
    if merge_stats["unmatched_binding_count"]:
        log += f"（⚠️ {merge_stats['unmatched_binding_count']} 筆找不到對應陀螺，詳見 log）"
    return _handled(log)


def _find_top_by_name_power(detailed, name, power):
    """名字是簡化過的（拿掉稱號前綴/強化值/綁定標籤），不能直接跟
    tops.json 的完整 name 欄位比對相等，用「子字串 + 戰力消歧」——
    子字串比對抓出候選，戰力（tops 不會重複，見專案筆記）用來確保唯一。
    出戰確認訊息／陀螺戰績兩個來源共用這份邏輯，不要各自寫一次。
    """
    if not name or power is None:
        return None

    candidates = [t for t in detailed if name in (t.get("name") or "") and t.get("power") == power]
    if len(candidates) == 1:
        return candidates[0]
    return None  # 找不到或有多個候選（理論上不該發生），不硬猜，交給呼叫端老實回報


def _sync_active_status(base_dir, account_id, name, power, source_label):
    """把 tops.json 裡「目前出戰是誰」同步成 name/power 指到的那顆。
    出戰確認訊息／陀螺戰績兩個來源共用這份邏輯，只有 log 前綴（source_label）不同。
    """
    tops_result = inventory_parsers.load_tops_snapshot(base_dir, account_id)
    if tops_result is None:
        return _handled(
            f"[{source_label}] 目前出戰為「{name}」，"
            f"但找不到既有的陀螺清單快照，無法同步狀態——請先觸發一次「我的陀螺」"
        )

    detailed = tops_result.get("detailed", [])
    matched = _find_top_by_name_power(detailed, name, power)
    if matched is None:
        return _handled(
            f"[{source_label}] 目前出戰為「{name}」，"
            f"但陀螺清單裡找不到唯一對應的項目，無法同步狀態（可能名字/戰力比對不到候選，或有多個候選）"
        )

    if matched.get("status") == "active":
        return _handled(f"[{source_label}] 目前出戰為「{name}」（#{matched['index']}），跟已知狀態一致，不用同步")

    for t in detailed:
        if t.get("status") == "active":
            t["status"] = "bench"
    matched["status"] = "active"

    inventory_parsers.save_tops_snapshot(base_dir, account_id, tops_result)
    return _handled(f"[{source_label}] ✅ 已同步：目前出戰為「{name}」（#{matched['index']}）")


def _handle_active_top_confirmation(confirmation, base_dir, account_id):
    """「出戰 N」確認訊息進來時，把 tops.json 裡「目前出戰是誰」同步更新。

    這條路徑之前完全沒人處理，導致切換出戰後 tops.json 立刻過期——
    直到下次查「我的陀螺」才會重新對齊。清護衛半自動化因此撞到「同一個
    切換動作被重複執行」的迴圈（2026-08-19 實測發現），這裡補上之後，
    任何讀 roster 的功能都能拿到即時準確的「目前出戰」狀態，不只是
    清護衛這一個呼叫端受惠。

    2026-08-19 補上：這則訊息常夾帶副陀螺被自動卸下的資訊（遊戲規則：
    副陀螺五行不能跟主陀螺相同），主同步做完之後順便處理——具名卸下的
    情況要额外把那顆改回 bench（不然它會一直停在 secondary，跟遊戲
    實際狀態不符）；「這顆原本是副陀螺」的情況不用另外處理，反正主同步
    已經把它標成 active，跟 secondary 天然互斥。
    """
    result = _sync_active_status(
        base_dir, account_id,
        confirmation.get("name"), confirmation.get("power"),
        source_label="出戰",
    )

    unequipped_name = confirmation.get("secondary_unequipped_name")
    if unequipped_name:
        sub_result = _sync_secondary_status(base_dir, account_id, unequipped_name, equip=False)
        result["log"] = f"{result['log']}\n{sub_result['log']}"

    return result


def _handle_top_record_active_sync(structured, base_dir, account_id):
    """陀螺戰績是「目前出戰是誰」的第四個資訊來源（見 top_record.py 的
    2026-08-19 補充說明）。這裡回傳 handled=True——陀螺戰績訊息本身
    不需要再往下讓其他 handler 判斷（world_boss/main_tower_battle/
    guard_clear/satellite_buttons 都不會處理這個 shape），提早結束
    dispatch() 這輪判斷，跟其他三個來源的處理方式一致。

    抽取失敗（active_name 是 None，理論上不該發生，陀螺戰績訊息一定有
    出戰那行）就不算 handled，交給後面的 fallback 判斷（不會誤傷，
    反正也不會比對到背包/道具說明）。
    """
    name = structured.get("active_name")
    if not name:
        return _no_match()
    return _sync_active_status(
        base_dir, account_id,
        name, structured.get("active_power"),
        source_label="出戰",
    )


def _find_top_by_name_only(detailed, name):
    """副陀螺相關訊息沒有戰力可以消歧（跟出戰確認訊息不同），只能用
    名字子字串比對，候選不唯一就老實回報，不硬猜。"""
    if not name:
        return None
    candidates = [t for t in detailed if name in (t.get("name") or "")]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _sync_secondary_status(base_dir, account_id, name, equip: bool):
    """把 tops.json 裡「目前副陀螺是誰」同步更新。
    equip=True：把 name 指到的那顆標成 secondary（同時把舊的 secondary 改回 bench）。
    equip=False：把 name 指到的那顆從 secondary 改回 bench（不影響其他欄位）。
    """
    tops_result = inventory_parsers.load_tops_snapshot(base_dir, account_id)
    if tops_result is None:
        return _handled(f"[副陀螺] 涉及「{name}」，但找不到既有的陀螺清單快照，無法同步狀態")

    detailed = tops_result.get("detailed", [])
    matched = _find_top_by_name_only(detailed, name)
    if matched is None:
        return _handled(f"[副陀螺] 涉及「{name}」，但陀螺清單裡找不到唯一對應的項目，無法同步狀態")

    if equip:
        if matched.get("status") == "secondary":
            return _handled(f"[副陀螺] 目前副陀螺為「{name}」（#{matched['index']}），跟已知狀態一致，不用同步")
        for t in detailed:
            if t.get("status") == "secondary":
                t["status"] = "bench"
        matched["status"] = "secondary"
        log = f"[副陀螺] ✅ 已同步：目前副陀螺為「{name}」（#{matched['index']}）"
    else:
        if matched.get("status") != "secondary":
            return _handled(f"[副陀螺] 「{name}」（#{matched['index']}）已卸下，跟已知狀態一致，不用同步")
        matched["status"] = "bench"
        log = f"[副陀螺] ✅ 已同步：「{name}」（#{matched['index']}）已卸下"

    inventory_parsers.save_tops_snapshot(base_dir, account_id, tops_result)
    return _handled(log)


def _handle_sub_top_confirmation(confirmation, base_dir, account_id):
    """「副陀螺 N」確認訊息進來時，把 tops.json 裡「目前副陀螺是誰」同步更新。"""
    name = confirmation.get("name")
    if not name:
        return _no_match()
    return _sync_secondary_status(base_dir, account_id, name, equip=True)


def _handle_sub_top_status(structured, base_dir, account_id):
    """「副陀螺」無參數查詢：equipped=True 就同步成那顆，equipped=False
    就確保沒有任何一顆停留在 secondary（可能之前切換主陀螺時自動卸下，
    這次查詢正好用來確認、補上同步）。"""
    if structured.get("equipped") is None:
        return _no_match()  # 格式不符已知兩種變體，不強行處理

    if structured.get("equipped") is False:
        return _clear_secondary_status(base_dir, account_id)

    name = structured.get("name")
    if not name:
        return _no_match()
    return _sync_secondary_status(base_dir, account_id, name, equip=True)


def _clear_secondary_status(base_dir, account_id):
    tops_result = inventory_parsers.load_tops_snapshot(base_dir, account_id)
    if tops_result is None:
        return _no_match()

    detailed = tops_result.get("detailed", [])
    cleared = [t for t in detailed if t.get("status") == "secondary"]
    if not cleared:
        return _handled("[副陀螺] 查詢結果為未裝，跟已知狀態一致，不用同步")

    for t in cleared:
        t["status"] = "bench"
    inventory_parsers.save_tops_snapshot(base_dir, account_id, tops_result)
    names = "、".join(t.get("name", "?") for t in cleared)
    return _handled(f"[副陀螺] ✅ 已同步：查詢結果為未裝，清掉舊記錄（{names}）")


def _handle_forge_result(structured, base_dir, account_id):
    """「鑄造完成」訊息進來時，先暫存候選，不直接寫進 cast_tops_catalog.json。

    2026-08-19 修正（熊指出的實際風險）：不是每次鑄造都會留下（不要的
    會被捨棄/轉點數），而且如果熊習慣用同一個自訂名字反覆嘗試，
    立刻寫入會被「最後一次鑄造結果」覆蓋掉，可能蓋掉你實際留下的那次
    的正確數據——不是多存垃圾資料而已，是真正的資料損毀風險。

    正確做法：先存進「待確認」暫存清單（pending_forge_results.json），
    等下次「我的陀螺」重新解析出收藏清單時，用名字+戰力+稀有度+類型
    四項一起比對——四項都對得上，代表這隻真的被留下了，這時候才正式
    寫進 cast_tops_catalog.json（見 _commit_matching_pending_forge()，
    在 _handle_tops() 裡呼叫）。查不到的持續留在待確認清單，不會誤寫。
    """
    name = structured.get("name")
    if not name:
        return _no_match()

    pending = _load_pending_forge(base_dir, account_id)
    pending.append(structured)
    _save_pending_forge(base_dir, account_id, pending)

    return _handled(
        f"[鑄造] 「{name}」出爐（{structured['element']}屬性・{structured['type']}・戰力{structured['power']}）"
        f"——等下次查「我的陀螺」確認有留下才會記進鑄造圖鑑"
    )


_PENDING_FORGE_FILENAME = "pending_forge_results.json"


def _pending_forge_path(base_dir, account_id):
    return Path(base_dir) / "data" / str(account_id) / _PENDING_FORGE_FILENAME


def _load_pending_forge(base_dir, account_id):
    path = _pending_forge_path(base_dir, account_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_pending_forge(base_dir, account_id, pending):
    path = _pending_forge_path(base_dir, account_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")


def _commit_matching_pending_forge(detailed, base_dir, account_id):
    """「我的陀螺」重新同步時呼叫：比對待確認清單裡的鑄造候選，跟這次
    收藏清單裡的項目做「名字子字串 + 戰力 + 稀有度 + 類型」四項比對，
    四項都對得上才代表真的被留下，正式寫進 cast_tops_catalog.json；
    對不上的維持在待確認清單裡，不主動清除（可能還沒查到、或熊還在
    考慮要不要留），不設過期時間——熊之後如果覺得清單累積太多冗餘
    候選，再回來討論要不要加淘汰機制。
    """
    pending = _load_pending_forge(base_dir, account_id)
    if not pending:
        return

    still_pending = []
    committed_names = []

    for candidate in pending:
        matched = any(
            candidate.get("name") in (t.get("name") or "")
            and t.get("power") == candidate.get("power")
            and t.get("rarity") == candidate.get("rarity")
            and t.get("type") == candidate.get("type")
            for t in detailed
        )
        if not matched:
            still_pending.append(candidate)
            continue

        result = forge_result_parser.ForgeResult(
            name=candidate["name"], rarity=candidate["rarity"], stars=candidate["stars"],
            tier_label=candidate["tier_label"], type=candidate["type"], element=candidate["element"],
            element_stage=candidate["element_stage"], atk=candidate["atk"], defense=candidate["defense"],
            endurance=candidate["endurance"], power=candidate["power"],
        )
        catalog_path = Path(base_dir) / "data" / str(account_id) / "cast_tops_catalog.json"
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog = forge_result_parser.load_cast_catalog(catalog_path)
        catalog[result.name] = forge_result_parser._to_catalog_entry(result)
        forge_result_parser.save_cast_catalog(catalog, catalog_path)
        committed_names.append(candidate["name"])

    _save_pending_forge(base_dir, account_id, still_pending)
    if committed_names:
        print(f"[鑄造圖鑑] ✅ 確認留下並記錄：{'、'.join(committed_names)}")