import asyncio
import json
import re
import time

from telegram_client import client, BASE_DIR
import monitor
import executor
import scheduler
import button_lookup
import profile_sync_strategy
import satellite_training_strategy
import world_boss_strategy
from parser import MessageRouter
from log_maintenance import run_maintenance

router = MessageRouter()

ANNOUNCEMENT_STRATEGIES = [
    world_boss_strategy,
]

ACCOUNT_ID = None

REACTION_RULES_FILE = BASE_DIR / "config" / "reaction_rules.json"

def load_reaction_rules():
    with open(REACTION_RULES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("rules", [])

REACTION_RULES = load_reaction_rules()

_last_fired_at = {}

_CLICK_POSITION_RE = re.compile(r"^row=(\d+)\s*,\s*col=(\d+)$", re.IGNORECASE)


async def click_button_by_text(spec, chat_id=None, reason=None):
    """給 /sched click:xxx 用：xxx 可以是按鈕文字（模糊比對），
    也可以寫成 'row=1,col=2' 依版面位置比對（文字太多變時比較穩定）。
    沒指定 chat_id 時比照 executor.send_now 的行為，fallback 用預設頻道。
    找到之後統一呼叫 executor.click_button 實際點擊。"""
    target_chat_id = chat_id or executor.DEFAULT_COMMAND_CHAT_ID
    pos_match = _CLICK_POSITION_RE.match(spec.strip())
    if pos_match:
        row, column = int(pos_match.group(1)), int(pos_match.group(2))
        match = button_lookup.find_button_by_position(chat_id=target_chat_id, row=row, column=column)
    else:
        match = button_lookup.find_button(spec, chat_id=target_chat_id)

    if match is None:
        raise ValueError(f"raw log 裡找不到符合「{spec}」的按鈕")
    return await executor.click_button(
        match["chat_id"], match["message_id"], match["data"],
        button_text=match["button_text"], reason=reason,
    )


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


async def dispatch_action(record, parsed):
    if parsed is None:
        return

    source_type = parsed.get("source_type")

    if source_type == "user" and parsed.get("command") == "培育":
        satellite_training_strategy.mark_awaiting_reply(record.get("chat_id"))

    if source_type not in ("server", "announcement"):
        return

    text = record.get("text") or ""

    was_awaiting_training_reply = satellite_training_strategy.consume_awaiting_reply(record.get("chat_id"))

    if source_type == "announcement":
        for strategy in ANNOUNCEMENT_STRATEGIES:
            catalog = strategy.load_catalog(BASE_DIR)
            action = strategy.decide_action(text, catalog, BASE_DIR, ACCOUNT_ID)
            if action["mode"] == "now":
                await executor.send_now(action["command"], chat_id=action["chat_id"], reason=action["reason"])
                return
            if action["mode"] == "scheduled":
                job = scheduler.ScheduledJob(
                    steps=[action["command"]],
                    delay_seconds=action["delay_seconds"],
                    chat_id=action["chat_id"],
                    reason=action["reason"],
                )
                job_id = scheduler.schedule(job, executor.send_now, click_button_by_text)
                print(f"[公告觸發] ⏳ {action['reason']}，已排程 {job_id}"
                      f"（{action['delay_seconds']:.0f} 秒後執行，"
                      f"可用 /sched list 查看、/sched cancel {job_id} 取消）")
                return
        return

    if source_type == "server":
        sync_result = profile_sync_strategy.handle_server_message(text, BASE_DIR, ACCOUNT_ID)
        if sync_result["handled"]:
            print(sync_result["log"])
            if sync_result["commands"]:
                await executor.send_sequence(
                    sync_result["commands"], interval_seconds=2, reason=sync_result["commands_reason"]
                )
            return

    if source_type == "server":
        wb_catalog = world_boss_strategy.load_catalog(BASE_DIR)
        wb_action = world_boss_strategy.decide_action_from_status_query(text, wb_catalog, BASE_DIR, ACCOUNT_ID)
        if wb_action["mode"] == "now":
            await executor.send_now(wb_action["command"], chat_id=wb_action["chat_id"], reason=wb_action["reason"])
            return

    buttons = record.get("buttons")
    if source_type == "server" and buttons:
        chat_id = record.get("chat_id")

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
    # action 欄位可以照舊寫一般文字指令（沿用原本行為，直接 send_now），
    # 也可以寫成 "/sched ..." 語法，這樣就能用上 delay/rep/interval/alias/click: 等能力，
    # 例如 "/sched click:強攻" 或 "/sched delay=3s alias=備戰 T0001 T0002"。
    # watch_chat 是可選欄位：規則沒寫就跟以前一樣不限聊天室；要限定某個頻道才觸發，
    # 在規則裡加一行 "watch_chat": "摸摸熊戰鬥陀螺" 即可。
    chat_name = record.get("chat_name") or ""
    for rule in REACTION_RULES:
        if rule["match_pattern"] not in text:
            continue

        watch_chat = rule.get("watch_chat")
        if watch_chat and watch_chat != chat_name:
            continue

        rule_id = rule["id"]
        cooldown = rule.get("cooldown_seconds", 60)
        last_fired = _last_fired_at.get(rule_id, 0)
        if time.time() - last_fired < cooldown:
            continue

        _last_fired_at[rule_id] = time.time()

        risk_level = rule.get("risk_level")
        action = rule.get("action")

        if risk_level == "safe" and rule.get("auto_execute") and action:
            print(f"[反應] 命中規則「{rule_id}」→ 自動執行：{action}")
            if action.strip().startswith("/sched"):
                try:
                    parsed_action = scheduler.parse_sched(action.strip())
                except scheduler.SchedParseError as e:
                    print(f"[反應] ⚠️ 規則「{rule_id}」的 /sched 動作語法錯誤：{e}")
                    continue
                if isinstance(parsed_action, scheduler.SchedControl):
                    print(f"[反應] ⚠️ 規則「{rule_id}」的動作不能是 list/cancel 這類管理指令：{action}")
                    continue
                job_id = scheduler.schedule(parsed_action, executor.send_now, click_button_by_text)
                print(f"[反應] 已排程 {job_id}：{parsed_action.summary}")
            else:
                await executor.send_now(action, reason=f"規則:{rule_id}")
        else:
            print(f"[反應] ⚠️ 命中規則「{rule_id}」，但風險等級為 {risk_level}，"
                  f"需要你自行確認並手動執行：{action or '(未指定動作，請自行判斷)'}")


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
                job_id = scheduler.schedule(parsed, executor.send_now, click_button_by_text)
                print(f"[SCHED] 已排程 {job_id}：{parsed.summary}"
                      f"（delay={parsed.delay_seconds:.0f}s, repeat={parsed.repeat}）")
        else:
            await executor.send_now(text, reason="手動輸入(終端機)")

async def on_record(record):
    if record.get("sent_by_bot"):
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

    run_maintenance()
    print()

    print("正在連線 Telegram...")
    await client.start()
    print("✅ 連線成功，開始監聽中（Ctrl+C 停止）")

    global ACCOUNT_ID
    me = await client.get_me()
    ACCOUNT_ID = me.id
    print(f"目前登入帳號 ID：{ACCOUNT_ID} ")
    print()

    asyncio.create_task(terminal_input_loop())
    await client.run_until_disconnected()


if __name__ == "__main__":
    with client:
        try:
            client.loop.run_until_complete(run())
        except KeyboardInterrupt:
            print("\n手動停止，程式結束。")