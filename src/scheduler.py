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

2026-10 第一階段：新增兩個「選用」參數 lane= / pri=，以及 job_id 改成遞增編號。
  - lane=名稱：同一個 lane 的排程「嚴格依下指令的順序」一個接一個執行（前一個整個
    做完才輪到下一個），不同 lane 互相交錯。at=/delay= 只是這個 job 的最早開始
    時間，不會讓排在它後面的 job 插隊：前面的 job 還在等 at= 時間，後面的也一起等。
    所以 lane 內的順序就是下指令（或 plan 檔）的行順序，要按時間先後就照時間排。
  - hold=時間（只能搭配 lane=）：這個 job 全部做完後，lane 再多佔住這麼久才輪到下一個，
    例如 hold=20s。整個 job 只算一次（不是每一輪 repeat 都停）。可用 /sched cancel 取消。
  - offlineat=HH:MM：離線排程。下指令的當下（程式必須在線）就把整串訊息「編譯」成
    「幾點幾分送什麼」，一則一則交給 Telegram 伺服器排程（executor.send_scheduled）；
    交出去之後本機斷線也會照送。Telegram 排程只精確到分鐘，所以間隔至少 1 分鐘；
    只能是文字指令（不能 click:）；不能跟 at/delay/lane/pri/hold 一起用。
  - pri=high|normal|low（或整數，越小越優先）：多個排程在同一瞬間都要送出時，
    由單一閘門依優先權決定誰先；閘門在每次送出之間強制隔 GATE_MIN_GAP_SECONDS。
  - 沒寫 lane 也沒寫 pri 的排程完全不經過鎖與閘門，行為跟改版前一致。
  - job_id 從 job-<毫秒> 改成 j1、j2、j3…（遞增，不會碰撞，也比較好打）。

2026-10 第二階段 A：新增 /plan（handle_plan_command，檔案讀取在 plans.py）。
plan 是一份「每行一條 /sched」的文字檔，載入時每行各自展開成獨立的 job；
lane/pri 由每一行自己決定，plan 本身不強制。ScheduledJob／SchedControl 都沒動。
"""

import asyncio
import heapq
import itertools
import random
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Callable, Awaitable, Union

import aliases
import executor
import plans

_SCHED_PREFIX = "/sched"
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)(s|m|h)?$")

# 連續重複時的最低間隔（安全下限，就算 interval 打得再小也不會低於這個值）。
MIN_INTERVAL_SECONDS = 0.1
# repeat > 1 但沒指定 interval 時使用的預設間隔（跟上面的下限是兩件事，各自可調）。
DEFAULT_INTERVAL_SECONDS = 2.0

# 離線排程（offlineat=）：伺服器端排程訊息的限制。
# 每個對話的排程訊息數量上限，印象中約 100 則——這個數字沒有實測過，請在真環境確認
# 後再調整。超過就整個拒絕，不會送一半。
OFFLINE_MAX_MESSAGES = 100
# Telegram 排程只精確到分鐘，間隔不到 1 分鐘沒有意義。
OFFLINE_MIN_INTERVAL_SECONDS = 60.0
# 起點離現在不到這麼久就排到明天（避免「已經過了」或「來不及」被 Telegram 拒絕）。
OFFLINE_MIN_LEAD_SECONDS = 60.0
# 連續提交給 Telegram 時，每則之間的小停頓（秒），避免一次丟太快觸發流量限制。
OFFLINE_SUBMIT_PAUSE_SECONDS = 0.3

# 優先權：數字越小越優先。pri= 可以寫名稱或整數。
_PRI_NAMES = {"high": 0, "normal": 1, "low": 2}
PRI_NORMAL = 1
# 優先權閘門：兩次「帶 lane/pri 的送出」之間的最小間隔（秒）。沒有這段間隔，
# 因為文字指令瞬間就送完，優先權幾乎沒有效果。只影響帶 lane/pri 的排程。
GATE_MIN_GAP_SECONDS = 0.5

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
    "  /sched lane=npc int=10s cmd1;cmd2       → 同一個 lane 的排程嚴格依下指令順序執行（前一個做完才輪到下一個），不同 lane 互相交錯\n"
    "  /sched offlineat=08:00 rep=3 int=5m 簽到 → 離線排程：現在就交給 Telegram 伺服器，之後斷線也會從 08:00 起送出\n"
    "                                           （只精確到分鐘、間隔至少 1m、只能文字指令，不能搭配 at/delay/lane/pri/hold）\n"
    "  /sched lane=npc hold=20s 指令           → 這個排程做完後，lane 再保留 20 秒才輪到下一個（只能搭配 lane=）\n"
    "  /sched pri=high 指令內容                → 優先權 high/normal/low（或整數，越小越優先），\n"
    "                                           多個排程同一瞬間都要送出時誰先；low 會讓路\n"
    "  （沒寫 lane 或 pri 的排程維持原本行為：各自獨立、時間到就送）\n"
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


def parse_priority(token: str) -> int:
    """pri= 的值：high/normal/low，或任意整數（越小越優先）。"""
    t = token.strip().lower()
    if t in _PRI_NAMES:
        return _PRI_NAMES[t]
    try:
        return int(t)
    except ValueError:
        raise SchedParseError(f"pri 必須是 high/normal/low 或整數：{token}\n{SCHED_USAGE}")


def _pri_label(pri: int) -> str:
    """顯示用：把整數轉回名稱（high/normal/low），其他整數照原樣。"""
    for name, value in _PRI_NAMES.items():
        if value == pri:
            return name
    return str(pri)


def _parse_hhmm(hhmm: str) -> Tuple[int, int]:
    """驗證並拆開 HH:MM；不合法丟 SchedParseError。"""
    try:
        hh, mm = map(int, hhmm.strip().split(":"))
    except ValueError:
        raise SchedParseError(f"無法解析時間點：{hhmm}，格式需為 HH:MM")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise SchedParseError(f"時間點超出範圍：{hhmm}，小時 0~23、分鐘 0~59")
    return hh, mm


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


# job_id 用遞增編號（j1、j2…）：比毫秒時間戳短、好打，也不會在同一毫秒內碰撞。
# 計數器只在這次程式執行期間有效；job 本來就不持久化，所以重開從 1 開始沒有影響。
_job_seq = itertools.count(1)


@dataclass
class ScheduledJob:
    segments: List[Tuple[str, List[str]]]  # [("once"|"repeat", 步驟清單), ...]，依序執行
    delay_seconds: float = 0.0
    repeat: int = 1  # 套用在每一個 "repeat" 區塊上；"once" 區塊永遠只跑一次
    interval: Tuple[float, float] = (0.0, 0.0)
    chat_id: Optional[int] = None
    reason: Optional[str] = None
    job_id: str = field(default_factory=lambda: f"j{next(_job_seq)}")
    # 以下兩個是 2026-10 新增的選用欄位，放在最後、預設 None：外部（actions.py）用
    # 關鍵字參數建構，沒傳就是 None，行為跟以前一樣。
    lane: Optional[str] = None  # 同 lane 依序執行；None = 不排隊
    pri: Optional[int] = None  # 同一瞬間誰先送；None = 不經過優先權閘門
    # 2026-10 第二階段新增：job 做完後 lane 再多佔住幾秒（秒數）；None = 不保留。只對有 lane 的 job 有意義。
    hold: Optional[float] = None
    # 2026-10 離線排程："HH:MM"；有值代表這個 job 不在本機計時，而是下指令當下就整批
    # 交給 Telegram 伺服器排程（見 compile_offline / _run_offline_job）。None = 一般本機排程。
    offline_at: Optional[str] = None

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
    lane = None
    pri = None
    hold = None
    offline_at = None
    given = set()
    consumed = 0
    modifier_keys = {"delay", "at", "repeat", "interval", "alias", "lane", "pri", "hold", "offlineat"}
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
                f"不認得的參數「{key}」，可用的是 delay/at/repeat(rep)/interval(int)/alias/lane/pri/hold/offlineat。\n{SCHED_USAGE}"
            )
        given.add(key)
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
        elif key == "lane":
            if not value.strip():
                raise SchedParseError(f"lane 不能是空的，應該寫成 lane=名稱。\n{SCHED_USAGE}")
            lane = value.strip()
        elif key == "pri":
            pri = parse_priority(value)
        elif key == "hold":
            hold = parse_duration(value)
        elif key == "offlineat":
            _parse_hhmm(value)  # 先驗證格式，不合法直接報錯
            offline_at = value.strip()
        consumed += 1

    if hold is not None and lane is None:
        # hold 是「lane 做完後多佔住幾秒」，沒有 lane 就沒有意義；直接報錯，不要靜默忽略。
        raise SchedParseError(f"hold 必須搭配 lane=名稱 一起使用（沒有 lane 就沒有東西可以保留）。\n{SCHED_USAGE}")

    if offline_at is not None:
        conflicts = sorted(given & {"delay", "at", "lane", "pri", "hold"})
        if conflicts:
            raise SchedParseError(
                f"offlineat 不能和 {'、'.join(conflicts)} 一起用：離線排程的送出時間在下指令當下就全部算好、"
                f"交給 Telegram，之後本機管不到。\n{SCHED_USAGE}"
            )

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
    if offline_at is not None:
        if any(step.lower().startswith(_CLICK_PREFIX) for _, steps in segments for step in steps):
            raise SchedParseError("離線排程只能是文字指令，不能含 click: 按鈕步驟（伺服器端排程不能點按鈕）。")
        if total_runs > OFFLINE_MAX_MESSAGES:
            raise SchedParseError(
                f"離線排程一共會有 {total_runs} 則訊息，超過上限 {OFFLINE_MAX_MESSAGES} 則（整個拒絕，不會送一半）。"
                "太長的流程請改用一般的本機排程。"
            )
        if total_runs > 1:
            if interval == (0.0, 0.0):
                raise SchedParseError("離線排程有多則訊息時必須指定間隔，至少 1 分鐘，例如 int=5m 或 int=10m-12m。")
            if min(interval) < OFFLINE_MIN_INTERVAL_SECONDS:
                raise SchedParseError("離線排程的間隔至少 1 分鐘（Telegram 排程只精確到分鐘），例如 int=1m。")

    if offline_at is None and total_runs > 1:
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
        lane=lane,
        pri=pri,
        hold=hold,
        offline_at=offline_at,
    )


def compile_offline(job: ScheduledJob, now: Optional[datetime] = None, rng=random) -> List[Tuple[datetime, str]]:
    """把離線 job「編譯」成 [(送出時間, 文字), ...]（時間都是整分鐘、有時區）。

    - 起點：offline_at 的下一次出現；離現在不到 OFFLINE_MIN_LEAD_SECONDS 就排到明天。
    - 步驟順序跟本機排程完全一致（once 區塊 1 次、repeat 區塊 × repeat 輪）。
    - 間隔在 interval 區間內隨機抽（一次全部抽好），累計後四捨五入到整分鐘；
      因為間隔 >= 1 分鐘，取整後相鄰兩則一定至少差 1 分鐘、順序不會亂。
    純函式（now / rng 可注入），方便測試跟 /plan show 預覽。
    """
    now = now or datetime.now().astimezone()
    hh, mm = _parse_hhmm(job.offline_at)
    start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if (start - now).total_seconds() < OFFLINE_MIN_LEAD_SECONDS:
        start += timedelta(days=1)

    texts: List[str] = []
    for kind, steps in job.segments:
        rounds = 1 if kind == "once" else job.repeat
        for _ in range(rounds):
            texts.extend(steps)

    lo, hi = job.interval
    plan: List[Tuple[datetime, str]] = []
    offset = 0.0
    for i, text in enumerate(texts):
        if i > 0:
            offset += rng.uniform(lo, hi) if hi > lo else lo
        plan.append((start + timedelta(minutes=round(offset / 60)), text))
    return plan


async def _run_offline_job(job: ScheduledJob, offline_fn):
    """編譯並把整串訊息一則一則交給 Telegram。這個 task 只活到「全部交出去」為止，
    之後的送出完全由 Telegram 伺服器負責（本機斷線也照送）。

    中途失敗：前面已經交出去的沒辦法自動收回（要用 Telegram 的「排程訊息」清單手動刪，
    或 /offline list 先看），所以失敗訊息會明確說出已經排了幾則。"""
    plan: List[Tuple[datetime, str]] = []
    done = 0
    try:
        plan = compile_offline(job)
        _job_states[job.job_id] = "提交離線排程中"
        print(f"[SCHED] {job.job_id} 離線排程：共 {len(plan)} 則，"
              f"{plan[0][0]:%m/%d %H:%M} ～ {plan[-1][0]:%m/%d %H:%M}，開始交給 Telegram…")
        for run_at, text in plan:
            reason = job.reason or f"離線排程({job.job_id}) {done + 1}/{len(plan)}"
            await offline_fn(text, run_at, chat_id=job.chat_id, reason=reason)
            done += 1
            if done < len(plan):
                await asyncio.sleep(OFFLINE_SUBMIT_PAUSE_SECONDS)
        print(f"[SCHED] ✅ {job.job_id} 離線排程已全部交給 Telegram（{done} 則）。"
              "之後斷線也會照送；要查看或取消請到 Telegram 對話的「排程訊息」清單，或用 /offline list")
    except asyncio.CancelledError:
        print(f"[SCHED] {job.job_id} 離線排程提交被取消，已交給 Telegram 的有 {done}/{len(plan)} 則（不會自動收回）")
        raise
    except Exception as e:
        print(f"[SCHED] ❌ {job.job_id} 離線排程提交到第 {done + 1}/{len(plan) or '?'} 則時失敗：{e}。"
              f"前 {done} 則已經排在 Telegram 上了，需要的話請到「排程訊息」清單手動刪除")
    finally:
        _active_jobs.pop(job.job_id, None)
        _job_states.pop(job.job_id, None)


# job_id -> (ScheduledJob, asyncio.Task)
_active_jobs: dict = {}
# job_id -> 顯示用狀態字串（等待開始／等待 lane／執行中）。獨立一份，
# 不去改 _active_jobs 的 (job, task) 二元組結構。
_job_states: dict = {}
# lane 名稱 -> _LaneQueue。沒寫 lane 的 job 完全不碰這個表。
_lanes: dict = {}


class _LaneQueue:
    """lane 內「嚴格依下指令順序」排隊。

    job 在 schedule() 時就登記進 order（下指令的順序），不是等 delay/at 睡完才
    排隊——這樣第一個 job 還在等 at=00:00 時，後面沒寫 at= 的 job 也不會搶先跑。
    輪到的條件只有一個：自己是 order 的第一個。job 結束（做完、失敗、被取消，
    包含還沒開始就被取消）時由 leave() 移出，並喚醒下一個。

    at=/delay= 只是「最早開始時間」，不會讓排在後面的 job 插隊，所以 lane 內的
    順序就是下指令（或 plan 檔）的行順序。
    """

    def __init__(self):
        self.order: list = []    # job_id，依下指令順序
        self.waiting: dict = {}  # job_id -> Future，已睡完 delay、正在等輪到自己

    def enter(self, job_id: str) -> None:
        self.order.append(job_id)

    def is_turn(self, job_id: str) -> bool:
        return bool(self.order) and self.order[0] == job_id

    async def wait_turn(self, job_id: str) -> None:
        if self.is_turn(job_id):
            return
        fut = asyncio.get_running_loop().create_future()
        self.waiting[job_id] = fut
        await fut

    def leave(self, job_id: str) -> None:
        if job_id in self.order:
            self.order.remove(job_id)
        self.waiting.pop(job_id, None)
        if self.order:
            fut = self.waiting.get(self.order[0])
            if fut is not None and not fut.done():
                fut.set_result(None)


def _lane_leave(job: ScheduledJob) -> None:
    lane = _lanes.get(job.lane)
    if lane is not None:
        lane.leave(job.job_id)


class _PriorityGate:
    """單一名額 + 優先權 + 每次送出後的最小間隔。

    只有帶 lane= 或 pri= 的 job 會經過這裡；其他 job 走原本的路徑，
    不受任何影響。排序是 (優先權, 先來後到)。

    同一瞬間到期的多個 job 會先全部排進佇列、下一輪事件迴圈才決定誰先
    （call_soon），所以 pri=high 的可以贏過同一瞬間先被喚醒的 pri=low。
    已經在送出中的不會被打斷。
    """

    def __init__(self, min_gap: float):
        self._min_gap = min_gap
        self._busy = False
        self._heap: list = []  # (pri, seq, future)
        self._seq = itertools.count()
        self._last_release = float("-inf")

    def _grant_next(self) -> None:
        if self._busy:
            return
        while self._heap:
            _, _, fut = heapq.heappop(self._heap)
            if not fut.done():  # 已被取消的略過
                self._busy = True
                fut.set_result(None)
                return

    def _release(self) -> None:
        self._busy = False
        self._grant_next()

    async def _wait_turn(self, pri: int) -> None:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        heapq.heappush(self._heap, (pri, next(self._seq), fut))
        if not self._busy:
            loop.call_soon(self._grant_next)
        try:
            await fut
        except asyncio.CancelledError:
            # 剛好被授予名額、但在恢復執行前被取消：名額要還回去，否則閘門卡死。
            if fut.done() and not fut.cancelled():
                self._release()
            raise

    @asynccontextmanager
    async def slot(self, pri: int):
        await self._wait_turn(pri)
        try:
            gap = self._last_release + self._min_gap - time.monotonic()
            if gap > 0:
                await asyncio.sleep(gap)
            yield
        finally:
            self._last_release = time.monotonic()
            self._release()


_gate = _PriorityGate(GATE_MIN_GAP_SECONDS)

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


async def _run_segments(job: ScheduledJob, send_fn: SendFn, click_fn: Optional[ClickFn] = None):
    """依序跑完 job.segments。這是原本 _run_job 在 delay 睡完之後的全部內容，
    邏輯不變；唯一差別是帶 lane/pri 的 job 每次送出會先過優先權閘門。"""
    gated = job.lane is not None or job.pri is not None
    gate_pri = job.pri if job.pri is not None else PRI_NORMAL

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
            if gated:
                async with _gate.slot(gate_pri):
                    await _run_step(step, job, reason, send_fn, click_fn)
            else:
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


async def _run_job(job: ScheduledJob, send_fn: SendFn, click_fn: Optional[ClickFn] = None):
    try:
        if job.delay_seconds > 0:
            _job_states[job.job_id] = "等待開始"
            print(f"[SCHED] {job.job_id} 將於 {job.delay_seconds:.1f} 秒後開始執行：{job.summary}")
            await asyncio.sleep(job.delay_seconds)

        # lane：嚴格依下指令順序，輪到自己（order 第一個）才開始。at=/delay= 睡完
        # 只代表「可以開始了」，前面還有 job 沒做完就繼續等。離開 lane 由
        # schedule() 掛的 done callback 負責（涵蓋「還沒開始就被取消」的情況）。
        # 沒寫 lane 的完全跳過這一段。
        if job.lane is not None:
            lane = _lanes[job.lane]
            if not lane.is_turn(job.job_id):
                _job_states[job.job_id] = f"等待 lane={job.lane}"
                print(f"[SCHED] {job.job_id} 在 lane「{job.lane}」排隊中，前面的排程做完才會開始")
            await lane.wait_turn(job.job_id)

        _job_states[job.job_id] = "執行中"
        await _run_segments(job, send_fn, click_fn)

        # hold：做完後 lane 再多佔住幾秒。這個 job 還在 order 第一個、task 還活著，
        # 所以後面的 job 自然會繼續等；被取消時 sleep 會中斷，lane 立刻釋放。
        if job.hold and job.lane is not None:
            _job_states[job.job_id] = f"保留 lane 中（{job.hold:g} 秒）"
            await asyncio.sleep(job.hold)
    except asyncio.CancelledError:
        print(f"[SCHED] {job.job_id} 已被取消")
        raise
    finally:
        _active_jobs.pop(job.job_id, None)
        _job_states.pop(job.job_id, None)


def schedule(job: ScheduledJob, send_fn: Optional[SendFn] = None, click_fn: Optional[ClickFn] = None,
             offline_fn: Optional[Callable[..., Awaitable[dict]]] = None) -> str:
    """建立排程任務並回傳 job_id，不會阻塞呼叫端。
    send_fn/click_fn 不給的話，預設用 executor.send_now / executor.click_button_by_text，
    只有測試或需要換掉實際送出方式時才需要自己傳。"""
    if job.offline_at is not None:
        # 離線排程不在本機計時：整批編譯後交給 Telegram，task 只活到全部交出去為止。
        offline_fn = offline_fn or executor.send_scheduled
        task = asyncio.create_task(_run_offline_job(job, offline_fn))
        _active_jobs[job.job_id] = (job, task)
        return job.job_id
    send_fn = send_fn or executor.send_now
    click_fn = click_fn or executor.click_button_by_text
    if job.lane is not None:
        # 在這裡（下指令的當下）登記順序，不是等 job 開始跑才登記。
        _lanes.setdefault(job.lane, _LaneQueue()).enter(job.job_id)
    task = asyncio.create_task(_run_job(job, send_fn, click_fn))
    if job.lane is not None:
        # 不管做完、失敗、被取消（含還沒開始跑就被取消）都要離開 lane，
        # 不然排在後面的 job 會永遠等下去。
        task.add_done_callback(lambda _t, j=job: _lane_leave(j))
    _active_jobs[job.job_id] = (job, task)
    return job.job_id


def list_jobs():
    """回傳目前所有進行中任務的簡要資訊，供 /sched list 使用。"""
    return [
        {"job_id": jid, "command": j.summary, "repeat": j.repeat,
         "lane": j.lane, "pri": j.pri, "hold": j.hold, "offline_at": j.offline_at, "state": _job_states.get(jid, "")}
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
                    extra = ""
                    if j.get("lane") is not None:
                        extra += f" ｜ lane={j['lane']}"
                    if j.get("pri") is not None:
                        extra += f" ｜ pri={_pri_label(j['pri'])}"
                    if j.get("hold"):
                        extra += f" ｜ hold={j['hold']:g}s"
                    if j.get("offline_at"):
                        extra += f" ｜ offlineat={j['offline_at']}"
                    if j.get("state"):
                        extra += f" ｜ {j['state']}"
                    print(f"  {j['job_id']} ｜ {j['command']} ｜ repeat={j['repeat']}{extra}")
        elif parsed.action == "cancel":
            ok = cancel(parsed.target)
            print(f"[SCHED] 已取消 {parsed.target}" if ok else f"[SCHED] 找不到 {parsed.target}")
    else:
        job_id = schedule(parsed)
        extra = ""
        if parsed.lane is not None:
            extra += f", lane={parsed.lane}"
        if parsed.pri is not None:
            extra += f", pri={_pri_label(parsed.pri)}"
        if parsed.hold:
            extra += f", hold={parsed.hold:g}s"
        if parsed.offline_at:
            extra += f", offlineat={parsed.offline_at}"
        print(f"[SCHED] 已排程 {job_id}：{parsed.summary}"
              f"（delay={parsed.delay_seconds:.0f}s, repeat={parsed.repeat}{extra}）")


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

# ==================== /plan：一組 /sched 行的集合 ====================
# 2026-10：plan 檔的讀取在 plans.py（比照 aliases.py）；這裡負責「逐行解析 +
# 展開成 job」，因為解析跟排程本來就是這支檔案的事，放這裡 plans.py 才不用
# import scheduler（避免循環依賴）。

# plan 名稱 -> 該 plan 展開出來的 job_id 清單。獨立一份，不新增 ScheduledJob
# 欄位；/plan stop 只取消這份清單裡的 job。
_plan_jobs: dict = {}

_PLAN_USAGE = (
    "/plan 用法：\n"
    "  /plan list              → 列出這個帳號的 plan\n"
    "  /plan show 名稱         → 只檢查、顯示每一行（不排程）\n"
    "  /plan load 名稱         → 載入：整份驗證通過才會全部排程，有任何一行錯就一個都不載入\n"
    "  /plan stop 名稱         → 取消這個 plan 展開的排程（不影響其他排程）\n"
    "  /plan stop all          → 取消所有 plan 展開的排程\n"
    "  （plan 檔放在 data/{帳號ID}/plans/名稱.plan，每行一條 /sched 語法，# 開頭是註解；\n"
    "   　重複載入同一個 plan 會先取消舊的再載入，不會疊成兩份；at= 的時間若已過會排到明天）"
)


def _fmt_wait(seconds: float) -> str:
    """顯示用：距離開始還有多久。載入 plan 時印出來，at= 時間已過而滾到明天的
    情況一眼就看得出來。"""
    if seconds < 1:
        return "立即開始"
    if seconds < 60:
        return f"約 {int(seconds)} 秒後開始"
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return f"約 {minutes} 分鐘後開始"
    return f"約 {minutes // 60} 小時 {minutes % 60} 分後開始"


def _parse_plan(base_dir, account_id, name):
    """讀檔並逐行解析，回傳 [(行號, 原文, ScheduledJob 或 None, 錯誤訊息或 None), ...]。
    plans.PlanError（找不到檔、名稱不合法、空檔案）直接往上丟，由呼叫端印出。"""
    results = []
    for line_no, line in plans.read_plan(base_dir, account_id, name):
        try:
            parsed = parse_sched(line)
        except SchedParseError as e:
            results.append((line_no, line, None, str(e).split("\n")[0]))
            continue
        if not isinstance(parsed, ScheduledJob):
            results.append((line_no, line, None, "plan 內只能放 /sched 排程行（不能是 list / cancel / stop 或其他指令）"))
            continue
        results.append((line_no, line, parsed, None))
    return results


def _describe_start(job: ScheduledJob) -> str:
    """plan show/load 顯示用：一般 job 顯示「多久後開始」；離線 job 顯示編譯預覽
    （幾則、哪個時段；間隔是隨機的，實際送出時會重新抽，所以時間只是預覽）。"""
    if job.offline_at is None:
        return _fmt_wait(job.delay_seconds)
    plan = compile_offline(job)
    return (f"離線排程：共 {len(plan)} 則，{plan[0][0]:%m/%d %H:%M} ～ {plan[-1][0]:%m/%d %H:%M}"
            "（間隔隨機，實際時間以交出時為準）")


def _job_extras(job: ScheduledJob) -> str:
    extra = ""
    if job.lane is not None:
        extra += f" ｜ lane={job.lane}"
    if job.pri is not None:
        extra += f" ｜ pri={_pri_label(job.pri)}"
    if job.hold:
        extra += f" ｜ hold={job.hold:g}s"
    if job.offline_at:
        extra += f" ｜ offlineat={job.offline_at}"
    return extra


def _stop_plan(name: str) -> int:
    """取消某個 plan 展開出來、還在進行中的 job，回傳實際取消的數量。"""
    return sum(1 for jid in _plan_jobs.pop(name, []) if cancel(jid))


async def handle_plan_command(text, base_dir, account_id):
    """/plan 終端機指令的統一入口（登記進 main.py 的 TERMINAL_COMMANDS）。"""
    tokens = text.split()
    if not tokens or tokens[0] != "/plan":
        print(f"[錯誤] 不認得的指令「{text}」，開頭 / 的訊息不會被送出。\n{_PLAN_USAGE}")
        return
    if account_id is None:
        print("[錯誤] 還沒取得帳號 ID（尚未連線完成），無法讀取這個帳號的 plan")
        return
    if len(tokens) == 1:
        print(_PLAN_USAGE)
        return

    sub_cmd = tokens[1].lower()

    if sub_cmd == "list" and len(tokens) == 2:
        names = plans.list_plans(base_dir, account_id)
        if not names:
            print(f"[PLAN] 目前沒有任何 plan（把 .plan 檔放在 {plans.plans_dir(base_dir, account_id)}）")
            return
        for n in names:
            suffix = ""
            if n in _plan_jobs:
                running = sum(1 for jid in _plan_jobs[n] if jid in _active_jobs)
                suffix = f" ｜ 已載入，{running} 個排程進行中" if running else " ｜ 已載入，排程都已結束"
            print(f"  {n}{suffix}")
        return

    if sub_cmd == "stop" and len(tokens) == 3 and tokens[2].lower() == "all":
        total = sum(_stop_plan(n) for n in list(_plan_jobs))
        print(f"[PLAN] 已取消所有 plan 的排程（共 {total} 個進行中）")
        return

    if sub_cmd in ("show", "load", "stop") and len(tokens) == 3:
        name = tokens[2]
        try:
            name = plans.normalize_name(name)
        except plans.PlanError as e:
            print(f"[錯誤] {e}")
            return

        if sub_cmd == "stop":
            if name not in _plan_jobs:
                print(f"[PLAN] plan「{name}」目前沒有載入")
                return
            print(f"[PLAN] 已取消 plan「{name}」的 {_stop_plan(name)} 個進行中的排程")
            return

        try:
            results = _parse_plan(base_dir, account_id, name)
        except plans.PlanError as e:
            print(f"[錯誤] {e}")
            return

        errors = [(n, err) for n, _, job, err in results if job is None]

        if sub_cmd == "show":
            for line_no, line, job, err in results:
                if job is None:
                    print(f"  第 {line_no} 行 ❌ {err}\n         {line}")
                else:
                    print(f"  第 {line_no} 行 ✅ {_describe_start(job)}{_job_extras(job)}\n         {line}")
            print(f"[PLAN] plan「{name}」共 {len(results)} 行，"
                  + (f"{len(errors)} 行有問題" if errors else "全部通過檢查"))
            return

        # load：整份驗證通過才排程，有任何一行錯就一個都不載入。
        if errors:
            print(f"[PLAN] plan「{name}」有 {len(errors)} 行有問題，整份都沒有載入：")
            for line_no, err in errors:
                print(f"  第 {line_no} 行：{err}")
            return

        replaced = _stop_plan(name)
        if replaced:
            print(f"[PLAN] plan「{name}」原本已載入，先取消舊的 {replaced} 個排程再重新載入")

        job_ids = []
        for line_no, line, job, _ in results:
            job.reason = f"plan {name} ({job.job_id})"
            schedule(job)
            job_ids.append(job.job_id)
            print(f"  {job.job_id} ｜ {job.summary} ｜ {_describe_start(job)}{_job_extras(job)}")
        _plan_jobs[name] = job_ids
        print(f"[PLAN] ✅ 已載入 plan「{name}」：{len(job_ids)} 個排程")
        return

    print(f"[錯誤] /plan 用法不對。\n{_PLAN_USAGE}")