"""
scheduler.py
負責解析 /sched 前綴指令，並控制延後執行 / 連續重複執行 / 管理進行中的排程。
不理解遊戲指令本身的語意，執行動作一律轉呼叫既有的 executor.send_now。
"""

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Tuple, Callable, Awaitable, Union

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
    "  （repeat 可簡寫 rep，interval 可簡寫 int；delay/at 可與 rep/int 疊加使用；\n"
    "   　rep>1 沒給 int 時會套用預設間隔）\n"
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
    command_text: str
    delay_seconds: float = 0.0
    repeat: int = 1
    interval: Tuple[float, float] = (0.0, 0.0)
    chat_id: Optional[int] = None
    reason: Optional[str] = None
    job_id: str = field(default_factory=lambda: f"job-{int(time.time() * 1000)}")


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
    consumed = 0
    modifier_keys = {"delay", "at", "repeat", "interval"}
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
                f"不認得的參數「{key}」，可用的是 delay/at/repeat(rep)/interval(int)。\n{SCHED_USAGE}"
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
        consumed += 1

    command_text = " ".join(tokens[consumed:]).strip()
    if not command_text:
        raise SchedParseError(f"/sched 後面沒有偵測到要執行的指令內容。\n{SCHED_USAGE}")

    if at_time:
        delay_seconds = _seconds_until(at_time)

    if repeat > 1:
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
        command_text=command_text,
        delay_seconds=delay_seconds,
        repeat=repeat,
        interval=interval,
    )


# job_id -> (ScheduledJob, asyncio.Task)
_active_jobs: dict = {}

SendFn = Callable[..., Awaitable[dict]]


async def _run_job(job: ScheduledJob, send_fn: SendFn):
    try:
        if job.delay_seconds > 0:
            print(f"[SCHED] {job.job_id} 將於 {job.delay_seconds:.0f} 秒後開始執行：{job.command_text}")
            await asyncio.sleep(job.delay_seconds)

        for i in range(job.repeat):
            reason = job.reason or f"排程({job.job_id}) {i + 1}/{job.repeat}"
            await send_fn(job.command_text, chat_id=job.chat_id, reason=reason)
            if i < job.repeat - 1:
                lo, hi = job.interval
                wait = random.uniform(lo, hi) if hi > lo else lo
                await asyncio.sleep(wait)
    except asyncio.CancelledError:
        print(f"[SCHED] {job.job_id} 已被取消")
        raise
    finally:
        _active_jobs.pop(job.job_id, None)


def schedule(job: ScheduledJob, send_fn: SendFn) -> str:
    """建立排程任務並回傳 job_id，不會阻塞呼叫端。"""
    task = asyncio.create_task(_run_job(job, send_fn))
    _active_jobs[job.job_id] = (job, task)
    return job.job_id


def list_jobs():
    """回傳目前所有進行中任務的簡要資訊，供 /sched list 使用。"""
    return [
        {"job_id": jid, "command": j.command_text, "repeat": j.repeat}
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