"""
main.py —— BOT 核心 / 協調器

職責分工：
  telegram_client.py    唯一的連線來源，monitor 跟 executor 共用
  monitor.py             只負責擷取 Telegram 訊息、存成 raw log（純接收）
  executor.py            只負責送出指令、記錄動作 log（純輸出，含連續送出）
  scheduler.py            /sched 指令解析 + 排程執行/管理（延後、重複、取消）
  parser.py               只負責分析一筆 record，回傳結構化的判斷結果
  xxx_strategy.py         各自「事件」的判斷邏輯（世界王／衛星培育／資料同步…），
                          只負責「判斷該做什麼」，不呼叫 executor、不存跨事件狀態。
  main.py                （這支檔案）協調以上各者：
                1. 啟動 monitor 的擷取
                2. 每筆訊息存檔後，丟給 parser 分析
                3. 用整理過、好讀的樣式印在畫面上
                4. 把「決定要不要做什麼」的工作分派給對應的 strategy 模組，
                   自己只留通用的反應規則表（reaction_rules.json）判斷
"""

import asyncio
import json
import time

from telegram_client import client, BASE_DIR
import monitor
import executor
import scheduler
import profile_sync_strategy
import satellite_training_strategy
import world_boss_strategy
from parser import MessageRouter
from log_maintenance import run_maintenance

router = MessageRouter()

# ── 摸摸熊戰鬥陀螺頻道（parser.ANNOUNCEMENT_CHAT_ID）的觸發規則清單 ──
# 這個頻道除了世界王，之後還會有其他種類的重要公告需要接自動觸發。
# 每加一種新公告觸發，照 world_boss_strategy.py 的模式寫一個新模組：
#   - 一份 data/common/xxx_catalog.json（事件目錄，trigger_pattern 等）
#   - 一支 src/xxx_strategy.py，提供：
#       load_catalog(base_dir) -> dict
#       decide_action(text, catalog) -> {"mode", "delay_seconds", "command", "chat_id", "reason"}
# 寫好後把模組加進下面這個清單就好，dispatch_action() 裡的迴圈不用改。
ANNOUNCEMENT_STRATEGIES = [
    world_boss_strategy,
    # 之後新增其他公告觸發時，把新模組加在這裡
]

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
    # 因為使用者指令本身的 source_type 是 "user"。狀態本身存在
    # satellite_training_strategy.py（跟消費端同一支檔案，見該檔說明）。
    if source_type == "user" and parsed.get("command") == "培育":
        satellite_training_strategy.mark_awaiting_reply(record.get("chat_id"))

    # 目前的反應規則只針對 server/announcement 的內容做比對；
    # 使用者自己打的指令不需要 BOT 反應（那是你自己在做的事）。
    if source_type not in ("server", "announcement"):
        return

    text = record.get("text") or ""

    # 消費「剛打了培育指令、正在等第一則回覆」的旗標——不管這則伺服器訊息
    # 是不是培育相關，都算是「等到了下一則回覆」，旗標就該清掉，避免卡住
    # 一直殘留到很久之後某次不相關的培育訊息才被誤判。
    was_awaiting_training_reply = satellite_training_strategy.consume_awaiting_reply(record.get("chat_id"))

    # ---- 公告頻道（世界王等）：依序問過 ANNOUNCEMENT_STRATEGIES，第一個給出
    # 動作的模組獲勝就送出，其餘不用再問。純資訊公告（例如定期戰況播報）
    # 全部模組都會回傳 None，自然往下 return，不會誤觸發任何指令。 ----
    if source_type == "announcement":
        for strategy in ANNOUNCEMENT_STRATEGIES:
            catalog = strategy.load_catalog(BASE_DIR)
            action = strategy.decide_action(text, catalog, BASE_DIR, ACCOUNT_ID)
            if action["mode"] == "now":
                await executor.send_now(action["command"], chat_id=action["chat_id"], reason=action["reason"])
                return
            if action["mode"] == "scheduled":
                job = scheduler.ScheduledJob(
                    command_text=action["command"],
                    delay_seconds=action["delay_seconds"],
                    chat_id=action["chat_id"],
                    reason=action["reason"],
                )
                job_id = scheduler.schedule(job, executor.send_now)
                print(f"[公告觸發] ⏳ {action['reason']}，已排程 {job_id}"
                      f"（{action['delay_seconds']:.0f} 秒後執行，"
                      f"可用 /sched list 查看、/sched cancel {job_id} 取消）")
                return
        return  # 沒有任何模組判斷出動作，純資訊公告，不需要往下處理

    # ---- 陀螺／衛星／背包／道具說明：四種都只是「更新自己資料庫」，
    # 判斷跟存檔邏輯統一收在 profile_sync_strategy.py，這裡只負責把
    # 結果印出來，命中時需要的話再呼叫 executor 送出查詢指令。 ----
    if source_type == "server":
        sync_result = profile_sync_strategy.handle_server_message(text, BASE_DIR, ACCOUNT_ID)
        if sync_result["handled"]:
            print(sync_result["log"])
            if sync_result["commands"]:
                await executor.send_sequence(
                    sync_result["commands"], interval_seconds=2, reason=sync_result["commands_reason"]
                )
            return

    # ---- 世界王查詢回覆（第三道保險）：使用者手動查詢「世界王」時，
    # 順便檢查這隻王今天打過沒、還活著嗎，需要的話補一刀。
    # 這個觸發跟公告頻道的 ANNOUNCEMENT_STRATEGIES 是分開處理的，
    # 因為查詢回覆出現在不同的 chat（摸熊神社），格式也不一樣。 ----
    if source_type == "server":
        wb_catalog = world_boss_strategy.load_catalog(BASE_DIR)
        wb_action = world_boss_strategy.decide_action_from_status_query(text, wb_catalog, BASE_DIR, ACCOUNT_ID)
        if wb_action["mode"] == "now":
            await executor.send_now(wb_action["command"], chat_id=wb_action["chat_id"], reason=wb_action["reason"])
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

        if text.startswith("/"):
            # 開頭是 / 的一律視為系統功能指令保留字，不認得就擋下來，絕不送到遊戲。
            try:
                parsed = scheduler.parse_sched(text)
            except scheduler.SchedParseError as e:
                print(f"[錯誤] {e}")
                continue

            if parsed is None:
                print(f"[錯誤] 不認得的指令「{text}」，開頭 / 的訊息不會被送出。\n{scheduler.SCHED_USAGE}")
                continue

            if isinstance(parsed, scheduler.SchedControl):
                if parsed.action == "list":
                    jobs = scheduler.list_jobs()
                    if not jobs:
                        print("[SCHED] 目前沒有進行中的排程")
                    else:
                        for j in jobs:
                            print(f"  {j['job_id']} ｜ {j['command']} ｜ repeat={j['repeat']}")
                elif parsed.action == "cancel":
                    ok = scheduler.cancel(parsed.target)
                    print(f"[SCHED] 已取消 {parsed.target}" if ok else f"[SCHED] 找不到 {parsed.target}")
            else:
                job_id = scheduler.schedule(parsed, executor.send_now)
                print(f"[SCHED] 已排程 {job_id}：{parsed.command_text}"
                      f"（delay={parsed.delay_seconds:.0f}s, repeat={parsed.repeat}）")
        else:
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
    print(f"目前登入帳號 ID：{ACCOUNT_ID} ") #（資料庫將存於 data/{ACCOUNT_ID}/）資料庫保密
    print()

    asyncio.create_task(terminal_input_loop())
    await client.run_until_disconnected()


if __name__ == "__main__":
    with client:
        try:
            client.loop.run_until_complete(run())
        except KeyboardInterrupt:
            print("\n手動停止，程式結束。")