# -*- coding: utf-8 -*-
"""
actions.py —— Action：觸發模組 decide(ctx) 的統一回傳格式，
跟 mode 對應的實際執行方式（EXECUTORS 登記表）。

=== 決策跟執行分兩層 ===
decide(ctx) 只回傳「想做什麼」的 Action，不直接呼叫 executor／scheduler——
這點跟搬移前的 strategy 模組規則一致（strategy 只決定跟持久化，不直接呼叫
executor）。真正動手是這裡的 execute()，依 action.mode 查 EXECUTORS 表，
交給對應函式送出去。

以少控多：現在有 send_now / send_sequence / click_button / schedule 四種，
之後要加新的 mode（例如「暫停其他 trigger」「切換戰鬥模式」），只要在
EXECUTORS 補一筆對照，dispatch() 的迴圈本身完全不用改。

=== Action.stop 的意義 ===
決定 dispatcher 的觸發清單迴圈要不要在這裡停下來：
    stop=True  —— 這則訊息已經確定歸這支模組管，不管有沒有實際動作，
                   都不要再讓後面的 trigger／reaction_rules 有機會處理。
    stop=False —— 雖然判斷過，但允許（或應該）讓其他人接手，例如自動
                   開關關閉、或判斷沒把握時放行給 reaction_rules 兜底。
decide(ctx) 回傳 None（不是 Action）代表「這則訊息根本不歸我管」，
不印任何 log，直接安靜地換下一個 trigger 試——這跟「判斷過但沒動作」
（用 none() 回傳）是兩種不同語意，分開表達比較清楚。
"""
from dataclasses import dataclass, field
from typing import Optional

import executor
import scheduler


@dataclass
class Action:
    mode: Optional[str] = None
    stop: bool = True
    log: Optional[str] = None
    payload: dict = field(default_factory=dict)


def none(log: Optional[str] = None, stop: bool = False) -> Action:
    """判斷過、沒有動作要送出的簡寫。stop 預設 False（放行給下一個 trigger），
    需要「認領但不動作」時（例如自動開關關閉但要提醒手動處理）自己傳 stop=True。"""
    return Action(mode=None, stop=stop, log=log)


def send_now(text, chat_id=None, reason=None, log=None, stop=True) -> Action:
    return Action(mode="send_now", stop=stop, log=log,
                  payload={"text": text, "chat_id": chat_id, "reason": reason})


def send_sequence(commands, chat_id=None, interval_seconds=2, reason=None, log=None, stop=True) -> Action:
    return Action(mode="send_sequence", stop=stop, log=log,
                  payload={"commands": commands, "chat_id": chat_id,
                           "interval_seconds": interval_seconds, "reason": reason})


def click_button(chat_id, message_id, data, button_text=None, reason=None, log=None, stop=True) -> Action:
    return Action(mode="click_button", stop=stop, log=log,
                  payload={"chat_id": chat_id, "message_id": message_id,
                           "data": data, "button_text": button_text, "reason": reason})


def schedule(steps, delay_seconds, chat_id=None, reason=None, log=None, stop=True) -> Action:
    return Action(mode="schedule", stop=stop, log=log,
                  payload={"steps": steps, "delay_seconds": delay_seconds,
                           "chat_id": chat_id, "reason": reason})


async def _run_send_now(payload):
    await executor.send_now(payload["text"], chat_id=payload.get("chat_id"), reason=payload.get("reason"))


async def _run_send_sequence(payload):
    await executor.send_sequence(
        payload["commands"], interval_seconds=payload.get("interval_seconds", 2),
        chat_id=payload.get("chat_id"), reason=payload.get("reason"),
    )


async def _run_click_button(payload):
    await executor.click_button(
        chat_id=payload["chat_id"], message_id=payload["message_id"],
        data=payload["data"], button_text=payload.get("button_text"), reason=payload.get("reason"),
    )


async def _run_schedule(payload):
    job = scheduler.ScheduledJob(
        steps=payload["steps"], delay_seconds=payload["delay_seconds"],
        chat_id=payload.get("chat_id"), reason=payload.get("reason"),
    )
    job_id = scheduler.schedule(job)
    print(f"⏳ {payload.get('reason')}，已排程 {job_id}"
          f"（{payload['delay_seconds']:.0f} 秒後執行，"
          f"可用 /sched list 查看、/sched cancel {job_id} 取消）")


# mode -> 執行函式，登記制。新增 mode 時只要在這裡加一筆，dispatch() 不用改。
EXECUTORS = {
    "send_now": _run_send_now,
    "send_sequence": _run_send_sequence,
    "click_button": _run_click_button,
    "schedule": _run_schedule,
}


async def execute(action: Action) -> None:
    """依 action.mode 查表執行；mode=None 只印 log、不執行任何 executor/scheduler 呼叫。"""
    if action.log:
        print(action.log)
    if action.mode is None:
        return
    runner = EXECUTORS.get(action.mode)
    if runner is None:
        print(f"⚠️ 未知的 action mode：{action.mode}，略過執行（trigger 模組寫錯 mode 字串？）")
        return
    await runner(action.payload)
