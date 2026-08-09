import asyncio

from telegram_client import client, BASE_DIR
import monitor
import executor
import scheduler
import world_boss_strategy
from parser import MessageRouter
from log_maintenance import run_maintenance
from display_formatter import format_display_line
from action_dispatcher import ActionDispatcher

router = MessageRouter()

REACTION_RULES_FILE = BASE_DIR / "config" / "reaction_rules.json"

# 目前登入的帳號 ID，啟動時取得一次、快取起來（見 run()）。
ACCOUNT_ID = None


def _get_account_id():
    return ACCOUNT_ID


# 公告頻道（摸摸熊戰鬥陀螺）的觸發規則清單。之後新增其他公告種類，
# 照 world_boss_strategy.py 的模式寫一個新模組，加進這個清單就好。
dispatcher = ActionDispatcher(
    base_dir=BASE_DIR,
    rules_file=REACTION_RULES_FILE,
    account_id_getter=_get_account_id,
    announcement_strategies=[world_boss_strategy],
)


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

        if text.startswith("/click"):
            # 獨立於 /sched 之外的直接點擊：跟一般文字指令一樣立即執行，
            # 不需要透過排程機制。用法跟 /sched click:xxx 裡的寫法一致：
            #   /click 按鈕文字        → 模糊比對
            #   /click row=1,col=2    → 依版面位置比對
            if not text.startswith("/click "):
                print("[錯誤] /click 用法：/click 按鈕文字  或  /click row=1,col=2")
                continue
            spec = text[len("/click "):].strip()
            if not spec:
                print("[錯誤] /click 用法：/click 按鈕文字  或  /click row=1,col=2")
                continue
            try:
                await executor.click_button_by_text(spec, reason="手動輸入(終端機)/click")
            except ValueError as e:
                print(f"[錯誤] {e}")
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
                job_id = scheduler.schedule(parsed)
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
    await dispatcher.dispatch(record, parsed)


async def run():
    monitor.ON_RECORD_CALLBACK = on_record
    # main.py 自己會透過 display_formatter 顯示每一則訊息，
    # 關掉 monitor.py 自帶的輸出，避免同一則訊息印兩次
    monitor.PRINT_ENABLED = False
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
