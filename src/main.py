import asyncio

from telegram_client import client, BASE_DIR
import auto_toggle
import monitor
import executor
import scheduler
import world_boss_strategy
from parser import MessageRouter
from log_maintenance import run_maintenance
from display_formatter import format_display_line
from action_dispatcher import ActionDispatcher
from strategy_pipeline import StrategyPipeline
from query_advisor_strategy import QueryAdvisorStrategy
from strategies.market_tracking_strategy import MarketTrackingStrategy
from strategies.chart_correlation_strategy import ChartCorrelationStrategy
from strategies.contract_tracking_strategy import ContractTrackingStrategy
from message_buffer import MessageBuffer
from inventory_display_strategy import InventoryDisplayStrategy


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

        if text.startswith("/delay"):
            # 按鈕點擊前的反應延遲，套用在所有三套自動系統共用的
            # executor.click_button()，不用各自處理。跟 /click、/sched、
            # /auto 一樣是終端機輸入的即時指令。
            # 用法：
            #   /delay              查詢目前設定
            #   /delay 1.5          設成固定 1.5 秒
            #   /delay 0.8-1.5      設成範圍 0.8~1.5 秒（每次點擊隨機抽一個）
            #   /delay 0            設成 0（不延遲，沒有強制下限）
            _DELAY_USAGE = ("[錯誤] /delay 用法：\n"
                             "  /delay              查詢目前設定\n"
                             "  /delay 1.5          設成固定 1.5 秒\n"
                             "  /delay 0.8-1.5      設成範圍 0.8~1.5 秒（每次隨機）")
            parts = text.split(maxsplit=1)
            if len(parts) == 1:
                lo, hi = executor.get_click_delay_range()
                if lo >= hi:
                    print(f"[延遲] 按鈕點擊前的延遲目前：固定 {lo} 秒")
                else:
                    print(f"[延遲] 按鈕點擊前的延遲目前：範圍 {lo}~{hi} 秒（每次隨機）")
            else:
                spec = parts[1].strip()
                if "-" in spec:
                    bounds = spec.split("-", 1)
                    try:
                        lo, hi = float(bounds[0]), float(bounds[1])
                    except ValueError:
                        print(_DELAY_USAGE)
                        continue
                else:
                    try:
                        lo = hi = float(spec)
                    except ValueError:
                        print(_DELAY_USAGE)
                        continue

                if lo < 0:
                    print("[錯誤] 延遲秒數不能是負數")
                elif hi < lo:
                    print("[錯誤] 範圍上限不能小於下限")
                else:
                    executor.set_click_delay_range(lo, hi)
                    if lo == hi:
                        print(f"[延遲] ✅ 按鈕點擊前的延遲已設定為固定 {lo} 秒")
                    else:
                        print(f"[延遲] ✅ 按鈕點擊前的延遲已設定為範圍 {lo}~{hi} 秒（每次隨機）")
            continue

        if text.startswith("/auto"):
            # 統一開關：主塔戰鬥／世界王／群星計畫，三套會自動送出動作的
            # 系統共用同一個指令。跟 /click、/sched 一樣是終端機輸入的
            # 即時指令，不經過 Telegram（目前架構還沒有 Telegram 端的
            # 遠端控制通道）。
            # 用法：
            #   /auto                       查看三套系統目前開關狀態
            #   /auto <system> on|off       開啟/關閉指定系統
            # <system> 可用簡稱：mtb / wb / sat / gc ，或完整 key：
            #   main_tower_battle / world_boss / satellite_training / guard_clear
            _AUTO_ALIASES = {
                "mtb": "main_tower_battle",
                "main_tower": "main_tower_battle",
                "main_tower_battle": "main_tower_battle",
                "wb": "world_boss",
                "world_boss": "world_boss",
                "sat": "satellite_training",
                "satellite": "satellite_training",
                "satellite_training": "satellite_training",
                "gc": "guard_clear",
                "guard": "guard_clear",
                "guard_clear": "guard_clear",
            }
            _AUTO_USAGE = ("[錯誤] /auto 用法：\n"
                           "  /auto                    查看三套系統目前開關狀態\n"
                           "  /auto <system> on|off    開啟/關閉指定系統\n"
                           "  <system>：mtb（主塔戰鬥）／wb（世界王）／sat（群星計畫）／gc（清除守衛）")
            parts = text.split()
            if len(parts) == 1:
                print("[開關狀態]\n" + auto_toggle.status_summary(BASE_DIR))
            elif len(parts) == 3 and parts[2] in ("on", "off"):
                system_key = _AUTO_ALIASES.get(parts[1])
                if system_key is None:
                    print(_AUTO_USAGE)
                else:
                    enabled = parts[2] == "on"
                    auto_toggle.set_enabled(BASE_DIR, system_key, enabled)
                    label = auto_toggle.SYSTEM_KEYS[system_key]
                    state = "✅ 開啟" if enabled else "🔕 關閉"
                    print(f"[開關] {label}：{state}")
            else:
                print(_AUTO_USAGE)
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
    market_tracking = MarketTrackingStrategy(
        account_data_dir=BASE_DIR / "data" / str(ACCOUNT_ID),
        common_data_dir=BASE_DIR / "data" / "common",
        enable_pulse=False,
    )
    contract_tracking = ContractTrackingStrategy(common_data_dir=BASE_DIR / "data" / "common")
    query_advisor = QueryAdvisorStrategy(
        account_data_dir=BASE_DIR / "data" / str(ACCOUNT_ID),
        common_data_dir=BASE_DIR / "data" / "common",
    )
    inventory_display = InventoryDisplayStrategy(
        base_dir=BASE_DIR,
        account_id_getter=_get_account_id,   # main.py 已經有這個函式，直接沿用
    )
    STRATEGY_PIPELINE = StrategyPipeline([market_tracking, contract_tracking, query_advisor, inventory_display])
    global CHART_CORRELATION
    CHART_CORRELATION = ChartCorrelationStrategy(
        common_data_dir=BASE_DIR / "data" / "common",
        media_dir=BASE_DIR / "data" / "common" / "chart_media",
    )
    asyncio.create_task(terminal_input_loop())
    await client.run_until_disconnected()


if __name__ == "__main__":
    with client:
        try:
            client.loop.run_until_complete(run())
        except KeyboardInterrupt:
            print("\n手動停止，程式結束。")