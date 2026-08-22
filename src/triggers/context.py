# -*- coding: utf-8 -*-
"""
context.py —— TriggerContext：所有觸發模組共用的唯一入參。

=== 為什麼要有這個 ===
搬移前，四支觸發模組（guard_clear／main_tower_battle／satellite_training／
world_boss）各自宣告要吃哪些參數：decide_action(parsed, roster)、
decide_action(text, buttons, base_dir)、decide_action(structured, buttons)……
每支不一樣。每次新增一支需要「同時參考多種狀態」的複雜觸發，就得先想清楚
這支要新增什麼參數、action_dispatcher.py 要怎麼組出來傳進去，介面本身
一直在長。

現在所有觸發模組一律吃同一個 TriggerContext，要用什麼自己從上面取，
之後新增觸發不用再改介面、不用再改 action_dispatcher.py 怎麼組參數。

=== 欄位分兩種 ===
- 直接資料：record / parsed 這類 dispatch() 每則訊息本來就有的東西，
  用 property 包一層方便存取（ctx.text 比 ctx.record.get("text") or "" 好讀）。
- 惰性存取：roster 這類需要讀檔案的資料，只有真的呼叫 ctx.roster 時才去讀，
  避免每則訊息都白白讀一次沒用到的檔案；同一個 ctx 內重複存取不會重讀。

=== state 屬性 ===
跨訊息的暫存狀態（例如「等待培育回覆」，之後可能有「櫻花模式暫停中」）
統一透過 ctx.state 存取，見 runtime_state.py 的說明。
"""
from dataclasses import dataclass, field
from typing import Any, Optional

import auto_toggle
from roster_loader import load_roster

from triggers import runtime_state


@dataclass
class TriggerContext:
    record: dict
    parsed: dict
    base_dir: Any
    account_id: str

    # 由 dispatch() 在建立 ctx 前算好傳進來（見該處說明：這個旗標不管
    # 最後是哪支 trigger 處理這則訊息，都只消費一次，所以不適合做成
    # ctx 上的惰性 property，得在 ctx 建立當下就是定值）。
    awaiting_training_reply: bool = False

    _roster_cache: Any = field(default=None, repr=False, compare=False)
    _roster_loaded: bool = field(default=False, repr=False, compare=False)

    # ---- 直接資料的 property 包裝 ----
    @property
    def text(self) -> str:
        return self.record.get("text") or ""

    @property
    def buttons(self):
        return self.record.get("buttons")

    @property
    def chat_id(self):
        return self.record.get("chat_id")

    @property
    def chat_name(self) -> str:
        return self.record.get("chat_name") or ""

    @property
    def message_id(self):
        return self.record.get("message_id")

    @property
    def shape(self) -> Optional[str]:
        return self.parsed.get("shape")

    @property
    def structured(self):
        return self.parsed.get("structured")

    # ---- 惰性存取 ----
    @property
    def roster(self):
        """惰性載入 tops.json，同一個 ctx 內重複存取不會重讀檔案。
        沒有用到 roster 的 trigger（例如 satellite_training）完全不會觸發這次讀檔。"""
        if not self._roster_loaded:
            self._roster_cache = load_roster(self.base_dir, self.account_id)
            self._roster_loaded = True
        return self._roster_cache

    def is_enabled(self, system_key: str) -> bool:
        """查 auto_toggle 開關狀態，包一層方便 trigger 模組不用各自 import auto_toggle。"""
        return auto_toggle.is_enabled(self.base_dir, system_key)

    @property
    def state(self):
        """共用的跨訊息暫存狀態存取點，見 runtime_state.py。"""
        return runtime_state
