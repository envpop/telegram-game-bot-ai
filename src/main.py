import asyncio

from telegram_client import client, BASE_DIR
import auto_toggle
import monitor
import executor
import scheduler
import data_store
from triggers import world_boss_strategy
from triggers import main_tower_battle_strategy
from triggers import guard_clear_strategy
from triggers import satellite_training_strategy
from triggers import satellite_naming_strategy
from triggers import sakura_strategy
from parser import MessageRouter
from log_maintenance import run_maintenance
from display_formatter import format_display_line
from action_dispatcher import ActionDispatcher
from strategy_pipeline import StrategyPipeline
from strategies.query_advisor_strategy import QueryAdvisorStrategy
from strategies.market_tracking_strategy import MarketTrackingStrategy
from strategies.chart_correlation_strategy import ChartCorrelationStrategy
from strategies.contract_tracking_strategy import ContractTrackingStrategy
from message_buffer import MessageBuffer
from strategies.inventory_display_strategy import InventoryDisplayStrategy
from strategies.battle_status_line_strategy import BattleStatusLineStrategy

import os
os.system("title MOMOBearBot - main")

router = MessageRouter()

REACTION_RULES_FILE = BASE_DIR / "config" / "reaction_rules.json"

# 目前登入的帳號 ID，啟動時取得一次、快取起來（見 run()）。
ACCOUNT_ID = None

# 被動記錄型 strategy 的統一入口（跟 ActionDispatcher 不同：這裡管的是
# 「單純觀察並記錄」，不主動觸發遊戲內動作）。要等 run() 裡拿到
# ACCOUNT_ID 之後才能建立（要知道存去 data/{帳號ID}/ 底下哪裡），
# 所以不能像 router 一樣在檔案最上面就建立，寫法比照 ACCOUNT_ID。
STRATEGY_PIPELINE = None

# 圖片配對用的 strategy，需要非同步下載，不能塞進同步的 StrategyPipeline，
# 獨立用一個變數持有、獨立 await 呼叫。一樣要等 run() 裡拿到 ACCOUNT_ID
# 之後才能建立。
CHART_CORRELATION = None


def _get_account_id():
    return ACCOUNT_ID


# 公告頻道（摸摸熊戰鬥陀螺）的觸發規則清單。之後新增其他公告種類，
# 照 world_boss_strategy.py 的模式寫一個新模組，加進這個清單就好。
#
# server_triggers：server 訊息的觸發清單，依序嘗試，第一個判斷出動作的
# 就處理掉（見 action_dispatcher.py 的說明）。順序跟搬移前 dispatch() 裡
# 原本的 if/elif 順序一致：世界王查詢保險 → 主塔戰鬥 → 清護衛 → 群星計畫。
# 之後新增觸發模組，照 triggers/ 底下任一支的 decide(ctx) 介面寫一支、
# 加進這個清單就好，不用再改 action_dispatcher.py。
dispatcher = ActionDispatcher(
    base_dir=BASE_DIR,
    rules_file=REACTION_RULES_FILE,
    account_id_getter=_get_account_id,
    announcement_strategies=[world_boss_strategy, sakura_strategy],
    server_triggers=[
        world_boss_strategy,
        main_tower_battle_strategy,
        guard_clear_strategy,
        satellite_training_strategy,
        satellite_naming_strategy,
    ],
)


# 終端機指令登記表：指令前綴 -> 該指令實際邏輯所在模組的 handler。
# 每個指令的解析/處理邏輯都放在它操作的那個模組裡（見 executor.py 的
# handle_delay_command/handle_click_command、auto_toggle.py 的
# handle_command、sakura_strategy.py 的 handle_command），這裡只登記
# 「哪個前綴對應哪個 handler」，不重新寫一次邏輯——跟 server_triggers
# 清單同一種「以少控多」的作法（2026-08-22 熊指出 SYSTEM_KEYS 都集中
# 管理了，指令處理邏輯卻散落在 main.py，缺乏整體感，照這個模式修正）。
# /sched 風格的指令不在這裡登記，維持原本 scheduler.parse_sched() 那條
# 路徑（本來就是同一種模式，只是命名前綴比較多樣，不適合用簡單前綴比對）。
TERMINAL_COMMANDS = {
    "/delay": executor.handle_delay_command,
    "/auto": auto_toggle.handle_command,
    "/sakura": sakura_strategy.handle_command,
    "/click": executor.handle_click_command,
}


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

        matched_handler = None
        for prefix, handler in TERMINAL_COMMANDS.items():
            if text.startswith(prefix):
                matched_handler = handler
                break
        if matched_handler is not None:
            await matched_handler(text, BASE_DIR, _get_account_id())
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

    if parsed is not None and STRATEGY_PIPELINE is not None:
        parsed = STRATEGY_PIPELINE.run(parsed, record)

    if parsed is not None and CHART_CORRELATION is not None:
        try:
            await CHART_CORRELATION.observe(parsed, record)
        except Exception as e:
            print(f"[WARN] chart_correlation 執行失敗：msg={record.get('message_id')} 錯誤：{e}")

    print(format_display_line(record, parsed))
    await dispatcher.dispatch(record, parsed)

message_buffer = MessageBuffer(on_flush=on_record)

async def run():
    monitor.ON_RECORD_CALLBACK = message_buffer.handle   # 原本是 on_record
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
    try:
        await client.start()
    except (ConnectionError, OSError) as e:
        print(f"[連線失敗] {e}，請確認網路狀態後重新啟動程式")
        return
    print("✅ 連線成功，開始監聽中（Ctrl+C 停止）")

    global ACCOUNT_ID
    me = await client.get_me()
    ACCOUNT_ID = me.id
    print(f"目前登入帳號 ID：{ACCOUNT_ID} ")
    print()

    # 這裡才知道 ACCOUNT_ID，才能建立需要存檔到 data/{帳號ID}/ 的 strategy。
    # 之後新增其他被動記錄型 strategy，只要加進這個清單，這裡跟 on_record
    # 都不用再改。
    global STRATEGY_PIPELINE
    account_dir = data_store.account_dir(BASE_DIR, ACCOUNT_ID)
    common_dir = data_store.common_dir(BASE_DIR)
    market_tracking = MarketTrackingStrategy(
        account_data_dir=account_dir,
        common_data_dir=common_dir,
        enable_pulse=False,
    )
    contract_tracking = ContractTrackingStrategy(common_data_dir=common_dir)
    query_advisor = QueryAdvisorStrategy(
        account_data_dir=account_dir,
        common_data_dir=common_dir,
    )
    inventory_display = InventoryDisplayStrategy(
        base_dir=BASE_DIR,
        account_id_getter=_get_account_id,   # main.py 已經有這個函式，直接沿用
    )
    battle_status_line = BattleStatusLineStrategy(
        account_data_dir=account_dir,
    )
    STRATEGY_PIPELINE = StrategyPipeline(
        [market_tracking, contract_tracking, query_advisor, inventory_display, battle_status_line]
    )
    global CHART_CORRELATION
    CHART_CORRELATION = ChartCorrelationStrategy(
        common_data_dir=common_dir,
        media_dir=common_dir / "chart_media",
    )
    asyncio.create_task(terminal_input_loop())
    await client.run_until_disconnected()


if __name__ == "__main__":
    with client:
        try:
            client.loop.run_until_complete(run())
        except KeyboardInterrupt:
            print("\n手動停止，程式結束。")