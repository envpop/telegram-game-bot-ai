"""
main.py —— BOT 核心 / 協調器

職責分工：
  telegram_client.py  唯一的連線來源，monitor 跟 executor 共用
  monitor.py           只負責擷取 Telegram 訊息、存成 raw log（純接收）
  executor.py          只負責送出指令、記錄動作 log（純輸出，含排程/連續送出）
  parser.py            只負責分析一筆 record，回傳結構化的判斷結果
  main.py               （這支檔案）協調以上四者：
                1. 啟動 monitor 的擷取
                2. 每筆訊息存檔後，丟給 parser 分析
                3. 用整理過、好讀的樣式印在畫面上
                4. 依照反應規則表判斷要不要呼叫 executor 送出指令
"""

import asyncio
import json
import time

from telegram_client import client, BASE_DIR
import monitor
import executor
import backpack_watcher
import inventory_parsers
import satellite_training_strategy
from parser import MessageRouter
from log_maintenance import run_maintenance

router = MessageRouter()

# 目前登入的帳號 ID，啟動時取得一次、快取起來，用於資料庫隔離
# （data/{帳號ID}/... 底下的資料只屬於這個帳號，換帳號登入就會自動切換資料夾）
ACCOUNT_ID = None

REACTION_RULES_FILE = BASE_DIR / "config" / "reaction_rules.json"

def load_reaction_rules():
    with open(REACTION_RULES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("rules", [])

REACTION_RULES = load_reaction_rules()

# 記錄每條規則上次觸發的時間，避免同一個提示訊息短時間內重複觸發、洗版送出重複指令
_last_fired_at = {}

# 記錄「使用者剛打了培育指令，還在等 BOT 第一則回覆」的狀態，key 是 chat_id。
# 只有這個旗標是 True 的時候，收到的下一則 main_menu 訊息才需要判斷新建/續練，
# 判斷完（或發現不是預期的回覆）就要清掉，避免之後每回合都誤判。
_awaiting_training_reply = {}


# ============================================================
# 顯示層：把 parser 的結構化結果，整理成人看得懂的樣子
# ============================================================

def format_display_line(record, parsed):
    chat_name = record.get("chat_name", "<unknown>")
    message_id = record.get("message_id", "")
    text = (record.get("text") or "").replace("\n", " ").strip()
    preview = text if len(text) <= 60 else text[:60] + "..."
    if not preview:
        preview = "<無文字>"

    if parsed is None:
        return f"[{chat_name} #{message_id}] ⚠️ PARSER 執行失敗 | {preview}"

    source_type = parsed.get("source_type")

    if source_type == "user":
        if parsed.get("recognized"):
            command = parsed.get("command")
            arguments = parsed.get("arguments") or []
            arg_str = " ".join(arguments)
            valid_shape = parsed.get("valid_shape")
            shape_flag = "" if valid_shape else " ⚠️參數數量不符"
            return (f"[{chat_name} #{message_id}] 👤指令 | {command} {arg_str}"
                    f" → route={parsed.get('route')}{shape_flag}")
        else:
            return f"[{chat_name} #{message_id}] 👤指令 | ⚠️未辨識：{preview} → review_queue"

    if source_type == "server":
        return f"[{chat_name} #{message_id}] 🤖伺服器回應 | {preview}"

    if source_type == "announcement":
        return f"[{chat_name} #{message_id}] 📢公告 | {preview}"

    return f"[{chat_name} #{message_id}] {source_type} | {preview}"


# ============================================================
# 執行層（骨架）：未來依 route 呼叫對應動作
# ============================================================
# 目前先留空，只做記錄跟印出「這裡未來會做什麼」，實際送出指令、
# 呼叫外部程式的邏輯之後再逐步補上。risk_level/需要確認的動作，
# 之後要在這裡擋下來、丟通知給你，而不是自動執行。

async def dispatch_action(record, parsed):
    if parsed is None:
        return

    source_type = parsed.get("source_type")

    # 使用者打「培育」指令：記下來，等下一則 BOT 回覆時判斷是新建還是續練。
    # 這個判斷要在「不是 server/announcement 就 return」之前做，
    # 因為使用者指令本身的 source_type 是 "user"。
    if source_type == "user" and parsed.get("command") == "培育":
        _awaiting_training_reply[record.get("chat_id")] = True

    # 目前的反應規則只針對 server/announcement 的內容做比對；
    # 使用者自己打的指令不需要 BOT 反應（那是你自己在做的事）。
    if source_type not in ("server", "announcement"):
        return

    text = record.get("text") or ""

    # 消費「剛打了培育指令、正在等第一則回覆」的旗標——不管這則伺服器訊息
    # 是不是培育相關，都算是「等到了下一則回覆」，旗標就該清掉，避免卡住
    # 一直殘留到很久之後某次不相關的培育訊息才被誤判。
    was_awaiting_training_reply = _awaiting_training_reply.pop(record.get("chat_id"), False)

    # ---- 我的陀螺：整份覆蓋存進個人資料 ----
    if source_type == "server" and inventory_parsers.is_my_tops_message(text):
        result = inventory_parsers.parse_my_tops(text)
        inventory_parsers.save_tops_snapshot(BASE_DIR, ACCOUNT_ID, result)
        print(f"[陀螺清單] 已更新，共 {result['total_count']} 顆")
        return

    # ---- 衛星圖鑑（含 alias 我的衛星）：整份覆蓋存進個人資料 ----
    if source_type == "server" and inventory_parsers.is_satellite_catalog_message(text):
        result = inventory_parsers.parse_satellite_catalog(text)
        inventory_parsers.save_satellites_snapshot(BASE_DIR, ACCOUNT_ID, result)
        print(f"[衛星清單] 已更新，共 {result['total_count']} 顆")
        return

    # ---- 背包偵測：解析內容,對沒看過的道具自動查詢說明,並存下個人持有數量 ----
    if source_type == "server" and backpack_watcher.is_backpack_message(text):
        result = backpack_watcher.parse_backpack(text)

        backpack_watcher.save_inventory_snapshot(BASE_DIR, ACCOUNT_ID, result)

        new_items = backpack_watcher.find_new_items(BASE_DIR, result)
        if new_items:
            print(f"[背包] 發現 {len(new_items)} 個沒看過的道具：{new_items}")
            backpack_watcher.mark_items_as_queried(BASE_DIR, new_items)
            commands = [f"道具說明 {name}" for name in new_items]
            await executor.send_sequence(commands, interval_seconds=2, reason="背包新道具自動查詢")
        return

    # ---- 道具說明回應：解析後存進共通資料庫 ----
    desc = backpack_watcher.parse_item_description(text)
    if desc is not None:
        backpack_watcher.save_item_description(BASE_DIR, desc["display_name"], desc)
        print(f"[道具說明] 已記錄：{desc['display_name']} → {desc['description']}")
        return

    # ---- 群星計畫（衛星培育）：帶按鈕的訊息，交給策略層決定要點哪顆按鈕 ----
    # 判斷邏輯全部在 satellite_training_strategy.py，這裡只負責呼叫、
    # 把決策結果送去 executor.click_button 執行。
    buttons = record.get("buttons")
    if source_type == "server" and buttons:
        chat_id = record.get("chat_id")

        # 如果這是「培育」指令送出後的第一則回覆，判斷新建還是續練，只判斷這一次。
        if was_awaiting_training_reply:
            catalog = satellite_training_strategy.load_catalog(BASE_DIR)
            session_kind = satellite_training_strategy.classify_session_start(text, catalog)
            if session_kind == "new":
                print("[群星計畫] 🆕 開始新一輪培育（新建衛星）")
            elif session_kind == "continuing":
                print("[群星計畫] ▶️ 續練進行中的衛星")

        action = satellite_training_strategy.decide_action(text, buttons, BASE_DIR)
        if action:
            await executor.click_button(
                chat_id=chat_id,
                message_id=record.get("message_id"),
                data=action["data"],
                button_text=action["button_text"],
                reason=action["reason"],
            )
        else:
            print(f"[群星計畫] ⚠️ 策略無法判斷要選哪個按鈕，需要人工介入：{text[:40]}...")
        return

    # ---- 一般觸發規則（reaction_rules.json）----
    for rule in REACTION_RULES:
        if rule["match_pattern"] not in text:
            continue

        rule_id = rule["id"]
        cooldown = rule.get("cooldown_seconds", 60)
        last_fired = _last_fired_at.get(rule_id, 0)
        if time.time() - last_fired < cooldown:
            continue  # 冷卻中，避免短時間內對同一提示重複反應

        _last_fired_at[rule_id] = time.time()

        risk_level = rule.get("risk_level")
        action = rule.get("action")

        if risk_level == "safe" and rule.get("auto_execute") and action:
            print(f"[反應] 命中規則「{rule_id}」→ 自動執行：{action}")
            await executor.send_now(action, reason=f"規則:{rule_id}")
        else:
            # 高風險/需要判斷的情境：只通知，不自動執行
            print(f"[反應] ⚠️ 命中規則「{rule_id}」，但風險等級為 {risk_level}，"
                  f"需要你自行確認並手動執行：{action or '(未指定動作，請自行判斷)'}")


# ============================================================
# 串接 monitor
# ============================================================

# ============================================================
# 終端機手動輸入：直接在這個視窗打字送出指令
# ============================================================
# 跟 BOT 自動觸發的指令走同一條路徑（executor.send_now），
# 一樣會記錄進 actions_sent.jsonl，reason 標成「手動輸入」方便之後區分。

async def terminal_input_loop():
    loop = asyncio.get_event_loop()
    print("💬 可以直接在這裡輸入指令送出遊戲（Enter 送出，Ctrl+C 結束整個程式）")
    while True:
        try:
            text = await loop.run_in_executor(None, input, "> ")
        except (EOFError, KeyboardInterrupt):
            break

        text = text.strip()
        if not text:
            continue

        await executor.send_now(text, reason="手動輸入(終端機)")


async def on_record(record):
    if record.get("sent_by_bot"):
        # 這是我方（BOT 或你手動輸入）自己剛送出去的訊息，raw log 已經完整記錄，
        # 但不需要再送進 parser 重新解析一次、也不需要再跑一次反應規則判斷，
        # 避免同一個動作被處理兩次。
        return

    try:
        parsed = router.parse(record)
    except Exception as e:
        print(f"[WARN] parser 執行失敗：msg={record.get('message_id')} 錯誤：{e}")
        parsed = None

    print(format_display_line(record, parsed))
    await dispatch_action(record, parsed)


async def run():
    monitor.ON_RECORD_CALLBACK = on_record

    print("=" * 70)
    print("BOT 核心啟動")
    print(f"監看的 Chat：")
    for chat_id, name in monitor.MONITORED_CHATS.items():
        print(f"  {name} : {chat_id}")
    print("=" * 70)

    # 每次啟動先跑一次 log 維護（壓縮 7 天前的 log、刪除 90 天前的壓縮檔）
    # 檢查的是資料夾日期,成本很低,啟動時做一次即可,不需要在執行期間反覆跑。
    run_maintenance()
    print()

    print("正在連線 Telegram...")
    await client.start()
    print("✅ 連線成功，開始監聽中（Ctrl+C 停止）")

    global ACCOUNT_ID
    me = await client.get_me()
    ACCOUNT_ID = me.id
    print(f"目前登入帳號 ID：{ACCOUNT_ID}（資料庫將存於 data/{ACCOUNT_ID}/）")
    print()

    asyncio.create_task(terminal_input_loop())
    await client.run_until_disconnected()


if __name__ == "__main__":
    with client:
        try:
            client.loop.run_until_complete(run())
        except KeyboardInterrupt:
            print("\n手動停止，程式結束。")