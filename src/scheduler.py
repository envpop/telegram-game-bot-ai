"""
scheduler.py
負責解析 /sched 前綴指令，並控制延後執行 / 連續重複執行 / 管理進行中的排程。
不理解遊戲指令本身的語意，執行動作預設呼叫 executor.send_now / executor.click_button_by_text；
呼叫端不用自己組轉接函式再傳進來，除非測試或有特殊需求才需要覆蓋。

2026-09 改版：ScheduledJob 的 steps/once_steps 兩個固定欄位，合併成
segments: List[Tuple[str, List[str]]]——每個區塊是 ("once"|"repeat", 步驟清單)，
依序執行。"once" 區塊固定跑一次；"repeat" 區塊跑 repeat 輪（外層 rep= 是
單一個共用次數，套用在每一個 repeat 區塊上，不支援每個區塊各自不同次數）。
這讓 alias（見 aliases.py）可以定義任意交錯的 once→repeat→once→... 順序，
不再侷限「只能一組 once 接一組 repeat」。純分號分隔的普通 /sched 指令
（不透過 alias）等同於單一個 repeat 區塊，行為跟改版前一致。
"""

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Callable, Awaitable, Union

import aliases
import executor

_SCHED_PREFIX = "/sched"
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)(s|m|h)?$")

# 連續重複時的最低間隔（安全下限，就算 interval 打得再小也不會低於這個值）。
MIN_INTERVAL_SECONDS = 0.1
# repeat > 1 但沒指定 interval 時使用的預設間隔（跟上面的下限是兩件事，各自可調）。
DEFAULT_INTERVAL_SECONDS = 2.0

SCHED_USAGE = (
    "/sched 用法：\n"
    "  /sched delay=5m 指令內容                → 5 分鐘後執行一次\n"
    "  /sched at=22:30 指令內容                → 在今天/明天 22:30 執行一次\n"
    "  /sched rep=3 int=10s 指令內容           → 每 10 秒重複執行 3 次\n"
    "  /sched rep=5 int=10s-20s 指令內容       → 間隔在 10~20 秒間隨機\n"
    "  /sched int=5s cmd1;cmd2;cmd3            → 依序執行 cmd1、cmd2、cmd3\n"
    "  /sched rep=3 int=5s cmd1;cmd2           → 交叉輪流：cmd1,cmd2,cmd1,cmd2,cmd1,cmd2\n"
    "  /sched int=2s click:確定;討伐;click:再抽一次 → 文字指令跟按鈕點擊可混用\n"
    "  /sched alias=備戰 T0001 T0002           → 展開設定好的別名，代入參數依序執行\n"
    "  /sched delay=5m alias=備戰 T0001 T0002  → alias 一樣可以疊加 delay/rep/int\n"
    "  （alias 可以定義任意順序的 once/repeat 區塊交錯，例如 once→repeat→once，\n"
    "   　rep= 套用在每一個 repeat 區塊上，格式說明見 aliases.py 開頭註解）\n"
    "  （repeat 可簡寫 rep，interval 可簡寫 int；用分號 ; 分隔多個指令可依序執行；\n"
    "   　多指令、rep>1 或使用 alias 沒給 int 時會套用預設間隔）\n"
    "  （查詢有哪些 alias 可用，改用 /alias list，不透過 /sched）\n"
    "  /sched list                             → 列出進行中的排程\n"
    "  /sched cancel <job_id>                  → 取消指定排程\n"
    "  /sched cancel all  （或 /sched stop）    → 取消全部排程"
)

# key 別名，輸入時可以用縮寫代替全名，解析後一律轉回全名處理。
_KEY_ALIASES = {"rep": "repeat", "int": "interval"}


class SchedParseError(ValueError):
    """/sched 語法錯誤，訊息已包含用法提示，呼叫端直接印出即可。"""
    pass


def parse_duration(token: str) -> float:
    """把 '5m' '30s' '1h' '300' 轉成秒數（無單位視為秒）。"""
    m = _DURATION_RE.match(token.strip())
    if not m:
        raise SchedParseError(f"無法解析時間格式：{token}")
    value, unit = m.groups()
    value = float(value)
    if unit == "m":
        return value * 60
    if unit == "h":
        return value * 3600
    return value


def parse_interval(token: str) -> Tuple[float, float]:
    """支援固定值 '10s' 或範圍 '10s-20s'（隨機取值，模擬手動間隔）。"""
    if "-" in token:
        lo, hi = token.split("-", 1)
        lo_s, hi_s = parse_duration(lo), parse_duration(hi)
        return (min(lo_s, hi_s), max(lo_s, hi_s))
    v = parse_duration(token)
    return (v, v)


def _seconds_until(hhmm: str) -> float:
    now = datetime.now()
    try:
        hh, mm = map(int, hhmm.split(":"))
    except ValueError:
        raise SchedParseError(f"無法解析時間點：{hhmm}，格式需為 HH:MM")
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


@dataclass
class ScheduledJob:
    segments: List[Tuple[str, List[str]]]  # [("once"|"repeat", 步驟清單), ...]，依序執行
    delay_seconds: float = 0.0
    repeat: int = 1  # 套用在每一個 "repeat" 區塊上；"once" 區塊永遠只跑一次
    interval: Tuple[float, float] = (0.0, 0.0)
    chat_id: Optional[int] = None
    reason: Optional[str] = None
    job_id: str = field(default_factory=lambda: f"job-{int(time.time() * 1000)}")

    @property
    def summary(self) -> str:
        """給 print/log 用的簡短顯示字串。"""
        parts = []
        for kind, steps in self.segments:
            steps_part = steps[0] if len(steps) == 1 else " ; ".join(steps)
            if kind == "repeat" and self.repeat > 1:
                parts.append(f"(重複{self.repeat}次) {steps_part}")
            else:
                parts.append(steps_part)
        return " → ".join(parts)


@dataclass
class SchedControl:
    """/sched list、/sched cancel 這類管理指令，不會產生要送出的遊戲指令。"""
    action: str  # "list" | "cancel"
    target: Optional[str] = None  # cancel 用：job_id 或 "all"


def parse_sched(text: str) -> Optional[Union[ScheduledJob, SchedControl]]:
    """
    解析 '/sched key=value ... 指令本體' 或 '/sched list' / '/sched cancel <id>'。
    不是 /sched 開頭回傳 None（呼叫端應照原本方式直接送出）。
    只要是 /sched 開頭但格式有誤，一律丟 SchedParseError，訊息已含用法，
    呼叫端不應該把原文字當成遊戲指令送出。
    """
    text = text.strip()
    # 精確比對「/sched」後面接空白或字串結尾，避免 "/schedd" 這種少打空格的
    # 打字誤判成合法的 /sched 指令（startswith 前綴比對會誤放行）。
    if text != _SCHED_PREFIX and not text.startswith(_SCHED_PREFIX + " "):
        return None

    rest = text[len(_SCHED_PREFIX):].strip()
    tokens = rest.split()

    if not tokens:
        raise SchedParseError(f"/sched 後面缺少內容。\n{SCHED_USAGE}")

    if tokens[0] == "list":
        return SchedControl(action="list")

    if tokens[0] == "stop":
        return SchedControl(action="cancel", target="all")

    if tokens[0] == "cancel":
        if len(tokens) < 2:
            raise SchedParseError(f"cancel 需要指定 job_id 或 all。\n{SCHED_USAGE}")
        return SchedControl(action="cancel", target=tokens[1])

    delay_seconds = 0.0
    repeat = 1
    interval = (0.0, 0.0)
    at_time = None
    alias_name = None
    consumed = 0
    modifier_keys = {"delay", "at", "repeat", "interval", "alias"}
    # 判斷「漏打等號」時，縮寫（rep/int）跟全名都要能被抓到。
    modifier_words = modifier_keys | set(_KEY_ALIASES.keys())

    for tok in tokens:
        if "=" not in tok:
            # 這個字剛好是保留字（或其縮寫）但忘了打等號（例如打成 "at 11:03"
            # 而不是 "at=11:03"），直接報錯提醒，不要默默把它當成指令本體送出。
            if tok.lower() in modifier_words:
                raise SchedParseError(
                    f"「{tok}」看起來是漏打等號，應該寫成「{tok}=值」。\n{SCHED_USAGE}"
                )
            break  # 真的不是 key=value 的 token，視為指令本體開始
        key, value = tok.split("=", 1)
        key = key.lower()
        key = _KEY_ALIASES.get(key, key)  # rep→repeat、int→interval，其餘不變
        if key not in modifier_keys:
            # 含有 "="，但 key 不是保留字（或縮寫）之一，多半是打錯字（如 reapeat=3），
            # 直接報錯，不要靜默當成指令本體送出。
            raise SchedParseError(
                f"不認得的參數「{key}」，可用的是 delay/at/repeat(rep)/interval(int)/alias。\n{SCHED_USAGE}"
            )
        if key == "delay":
            delay_seconds = parse_duration(value)
        elif key == "at":
            at_time = value
        elif key == "repeat":
            try:
                repeat = int(value)
            except ValueError:
                raise SchedParseError(f"repeat 必須是整數：{value}\n{SCHED_USAGE}")
        elif key == "interval":
            interval = parse_interval(value)
        elif key == "alias":
            alias_name = value
        consumed += 1

    remaining = tokens[consumed:]

    if alias_name is not None:
        # alias=名稱 之後剩下的 tokens 全部當成該 alias 的參數，依序代入 {1} {2} ...
        try:
            segments = aliases.resolve_alias(alias_name, remaining)
        except aliases.AliasError as e:
            raise SchedParseError(str(e))
    else:
        if not remaining:
            raise SchedParseError(f"/sched 後面沒有偵測到要執行的指令內容。\n{SCHED_USAGE}")
        command_text = " ".join(remaining).strip()
        # 分號分隔多個指令，依序執行；沒有分號就只有一個元素，行為跟以前一樣。
        # 不透過 alias 的普通指令一律視為單一個 repeat 區塊。
        steps = [s.strip() for s in command_text.split(";") if s.strip()]
        if not steps:
            raise SchedParseError(f"/sched 後面沒有偵測到要執行的指令內容。\n{SCHED_USAGE}")
        segments = [("repeat", steps)]

    if at_time:
        delay_seconds = _seconds_until(at_time)

    # 總執行次數 = 所有 once 區塊步驟數（各跑 1 次）+ 所有 repeat 區塊步驟數 × repeat。
    # 只要總次數超過 1，步驟之間、輪次之間就都需要間隔。
    total_runs = sum(
        len(steps) if kind == "once" else len(steps) * repeat
        for kind, steps in segments
    )
    if total_runs > 1:
        if interval == (0.0, 0.0):
            interval = (DEFAULT_INTERVAL_SECONDS, DEFAULT_INTERVAL_SECONDS)
            print(f"[SCHED] 未指定 interval，套用預設間隔 {DEFAULT_INTERVAL_SECONDS:.1f}s")
        lo, hi = interval
        if lo < MIN_INTERVAL_SECONDS or hi < MIN_INTERVAL_SECONDS:
            lo = max(lo, MIN_INTERVAL_SECONDS)
            hi = max(hi, MIN_INTERVAL_SECONDS)
            print(f"[SCHED] 間隔低於最低限制 {MIN_INTERVAL_SECONDS}s，已自動調整為 {lo:.1f}s-{hi:.1f}s")
            interval = (lo, hi)

    return ScheduledJob(
        segments=segments,
        delay_seconds=delay_seconds,
        repeat=repeat,
        interval=interval,
    )


# job_id -> (ScheduledJob, asyncio.Task)
_active_jobs: dict = {}

SendFn = Callable[..., Awaitable[dict]]
ClickFn = Callable[..., Awaitable[dict]]

_CLICK_PREFIX = "click:"


async def _run_step(step: str, job: "ScheduledJob", reason: str, send_fn: SendFn, click_fn: Optional[ClickFn]):
    """依步驟內容分派：click: 開頭走按鈕點擊，否則照舊送文字指令。"""
    if step.lower().startswith(_CLICK_PREFIX):
        if click_fn is None:
            raise RuntimeError("這個排程包含按鈕點擊步驟，但呼叫端沒有提供 click_fn")
        button_text = step[len(_CLICK_PREFIX):].strip()
        await click_fn(button_text, chat_id=job.chat_id, reason=reason)
    else:
        await send_fn(step, chat_id=job.chat_id, reason=reason)


async def _run_job(job: ScheduledJob, send_fn: SendFn, click_fn: Optional[ClickFn] = None):
    try:
        if job.delay_seconds > 0:
            print(f"[SCHED] {job.job_id} 將於 {job.delay_seconds:.1f} 秒後開始執行：{job.summary}")
            await asyncio.sleep(job.delay_seconds)

        total = sum(
            len(steps) if kind == "once" else len(steps) * job.repeat
            for kind, steps in job.segments
        )
        i = 0

        async def _execute(step: str) -> bool:
            """回傳 True 表示成功，False 表示失敗（呼叫端要中止剩餘步驟）。"""
            nonlocal i
            reason = f"{job.reason}（{i + 1}/{total}）" if job.reason else f"排程({job.job_id}) {i + 1}/{total}"
            try:
                await _run_step(step, job, reason, send_fn, click_fn)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # 某一步失敗（例如按鈕找不到）就停下來，不要盲目繼續跑剩下的步驟，
                # 因為後續步驟很可能是建立在這一步成功的前提上。
                print(f"[SCHED] {job.job_id} 執行「{step}」時發生錯誤：{e}，已中止剩餘步驟")
                return False
            i += 1
            if i < total:
                lo, hi = job.interval
                wait = random.uniform(lo, hi) if hi > lo else lo
                await asyncio.sleep(wait)
            return True

        # 依序跑過每個區塊："once" 固定跑一次，"repeat" 跑 job.repeat 輪。
        # 這樣才能支援 once→repeat→once→... 任意交錯的順序，不再侷限
        # 「所有 once 一定在所有 repeat 前面」。
        for kind, steps in job.segments:
            if kind == "once":
                for step in steps:
                    if not await _execute(step):
                        return
            else:  # kind == "repeat"
                for r in range(job.repeat):
                    for step in steps:
                        if not await _execute(step):
                            return
    except asyncio.CancelledError:
        print(f"[SCHED] {job.job_id} 已被取消")
        raise
    finally:
        _active_jobs.pop(job.job_id, None)


def schedule(job: ScheduledJob, send_fn: Optional[SendFn] = None, click_fn: Optional[ClickFn] = None) -> str:
    """建立排程任務並回傳 job_id，不會阻塞呼叫端。
    send_fn/click_fn 不給的話，預設用 executor.send_now / executor.click_button_by_text，
    只有測試或需要換掉實際送出方式時才需要自己傳。"""
    send_fn = send_fn or executor.send_now
    click_fn = click_fn or executor.click_button_by_text
    task = asyncio.create_task(_run_job(job, send_fn, click_fn))
    _active_jobs[job.job_id] = (job, task)
    return job.job_id


def list_jobs():
    """回傳目前所有進行中任務的簡要資訊，供 /sched list 使用。"""
    return [
        {"job_id": jid, "command": j.summary, "repeat": j.repeat}
        for jid, (j, _) in _active_jobs.items()
    ]


def cancel(job_id: str) -> bool:
    """
    取消尚未完成的任務，供 /sched cancel <id> 使用。
    job_id 傳 'all' 時取消全部進行中的排程（失控時的緊急停止手段，
    不用整個關掉主程式也能停下來；真的連這個都沒反應，直接 Ctrl+C 就好）。
    """
    if job_id == "all":
        had_any = bool(_active_jobs)
        for _, task in list(_active_jobs.values()):
            task.cancel()
        _active_jobs.clear()
        return had_any

    entry = _active_jobs.pop(job_id, None)
    if entry:
        entry[1].cancel()
        return True
    return False


# ==================== 終端機指令入口（登記進 main.py 的 TERMINAL_COMMANDS） ====================
# 2026-09：/sched、/alias 原本分別是 main.py 裡各自的 if/else 特例，跟
# TERMINAL_COMMANDS「指令前綴 -> handler」的統一介面不一致（見 main.py
# 開頭註解）。這裡補上兩個函式簽章跟其他 handler（例如
# executor.handle_delay_command）一致的 handle_command，main.py 之後
# 只需要登記、不用再寫任何解析或分支邏輯。

async def handle_command(text, base_dir, account_id):
    """/sched 終端機指令的統一入口。base_dir/account_id 是 TERMINAL_COMMANDS
    呼叫慣例統一帶的參數，/sched 用不到，純粹為了跟其他 handler 保持同樣的
    函式簽章，main.py 才能用同一個迴圈呼叫全部指令。"""
    try:
        parsed = parse_sched(text)
    except SchedParseError as e:
        print(f"[錯誤] {e}")
        return

    if parsed is None:
        print(f"[錯誤] 不認得的指令「{text}」，開頭 / 的訊息不會被送出。\n{SCHED_USAGE}")
        return

    if isinstance(parsed, SchedControl):
        if parsed.action == "list":
            jobs = list_jobs()
            if not jobs:
                print("[SCHED] 目前沒有進行中的排程")
            else:
                for j in jobs:
                    print(f"  {j['job_id']} ｜ {j['command']} ｜ repeat={j['repeat']}")
        elif parsed.action == "cancel":
            ok = cancel(parsed.target)
            print(f"[SCHED] 已取消 {parsed.target}" if ok else f"[SCHED] 找不到 {parsed.target}")
    else:
        job_id = schedule(parsed)
        print(f"[SCHED] 已排程 {job_id}：{parsed.summary}"
              f"（delay={parsed.delay_seconds:.0f}s, repeat={parsed.repeat}）")


async def handle_alias_command(text, base_dir, account_id):
    """/alias 終端機指令的統一入口。放在這裡（不是 aliases.py）：scheduler.py
    本來就 import aliases（parse_sched 展開 alias 用），這裡沿用同一個
    方向；如果反過來讓 aliases.py 呼叫這裡的 schedule()/parse_sched()，
    會變成兩邊互相 import，違反禁止循環依賴的原則。

    /alias list              → 列出目前可用的 alias 名稱，純查詢，不排程。
    /alias 名稱 [參數...]     → 立即執行一次，純文字改寫成
                                 /sched alias=名稱 [參數...]（delay=0、
                                 repeat=1 的預設值就是「立即執行一次」），
                                 不重新實作一次「怎麼把展開的步驟送出去」，
                                 沿用 parse_sched()/schedule() 同一套邏輯。
    """
    rest = text[len("/alias"):].strip()
    if not rest:
        print("[錯誤] /alias 後面缺少 alias 名稱或 list。用法：/alias list，或 /alias 名稱 [參數...]")
        return

    if rest == "list":
        names = aliases.list_aliases()
        if names:
            print("[ALIAS] 目前可用：" + "、".join(names))
        else:
            print("[ALIAS] 目前沒有任何 alias（請先在 config/aliases.json 設定）")
        return

    try:
        parsed = parse_sched("/sched alias=" + rest)
    except SchedParseError as e:
        print(f"[錯誤] {e}")
        return
    job_id = schedule(parsed)
    print(f"[SCHED] 已排程 {job_id}：{parsed.summary}"
          f"（delay={parsed.delay_seconds:.0f}s, repeat={parsed.repeat}）")