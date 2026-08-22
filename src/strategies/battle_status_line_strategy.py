# -*- coding: utf-8 -*-
"""
battle_status_line_strategy.py

常態附加式的出戰狀態行——不管這次觸發的是哪個指令，都在顯示結果下面
附一行「⚔️ 出戰陀螺 / 副陀螺 / 目前衛星」的精簡狀態。跟
market_tracking_strategy.py 的 market_pulse 是同一種模式：這裡只負責
「算出這行文字」，回傳 {"battle_status_line": line}，合併進 parsed
之後由 display_formatter.py 決定要不要附加、附加在哪裡（跟 market_pulse
的接線方式一致，不在這支檔案裡處理顯示排版本身）。

=== 資料來源（都是已經在跑的同步機制，這裡只負責讀 + 組字串）===
    主陀螺／副陀螺：data/{帳號ID}/tops.json 的 detailed[].status
        （"active"/"secondary"，六條同步路徑已經收斂到這裡，見對話記錄）
    目前衛星：data/{帳號ID}/satellites.json 的 active_satellite_name
        （profile_sync_strategy._handle_satellite() 這次順手補上的欄位）

=== 一行原則（熊要求「盡量簡化，最好一行內結束」）===
    格式：⚔️ {主陀螺短稱}{屬性顏色}{類型簡寫}　副{副陀螺短稱}{屬性顏色}{類型簡寫}　🛰️{衛星名稱}
    三段都可能缺（沒出戰/沒副陀螺/沒衛星資料），缺的就跳過那段，
    不留空白佔位；三段全缺（例如還沒查過陀螺收藏）就回傳 None，
    不附加空行。

用法（main.py，跟 market_tracking 放進同一個 StrategyPipeline）：

    battle_status_line = BattleStatusLineStrategy(
        account_data_dir=BASE_DIR / "data" / str(ACCOUNT_ID),
    )
    STRATEGY_PIPELINE = StrategyPipeline([..., battle_status_line])
"""

import json
import logging
from pathlib import Path

from battle_status import format_top

logger = logging.getLogger(__name__)


class BattleStatusLineStrategy:
    def __init__(self, account_data_dir, enable=True):
        self.account_data_dir = Path(account_data_dir)
        self.enable = enable

    def _load_json(self, filename):
        path = self.account_data_dir / filename
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[出戰狀態行] %s 讀取失敗，略過（%s）", filename, e)
            return None

    def _load_active_main_sub(self):
        data = self._load_json("tops.json")
        if not data:
            return None, None
        detailed = data.get("detailed", [])
        main = next((t for t in detailed if t.get("status") == "active"), None)
        sub = next((t for t in detailed if t.get("status") == "secondary"), None)
        return main, sub

    def _load_active_satellite_name(self):
        data = self._load_json("satellites.json")
        if not data:
            return None
        return data.get("active_satellite_name")

    def _compute_status_line(self):
        main, sub = self._load_active_main_sub()
        satellite_name = self._load_active_satellite_name()

        parts = []
        if main:
            parts.append(format_top(main))
        if sub:
            parts.append("副" + format_top(sub))
        if satellite_name:
            parts.append(f"🛰️{satellite_name}")

        if not parts:
            return None  # 三段全缺（例如還沒查過陀螺收藏），不附加空行

        return "⚔️ " + "　".join(parts)

    def observe(self, parsed, record):
        if not self.enable:
            return None
        line = self._compute_status_line()
        if not line:
            return None
        return {"battle_status_line": line}


if __name__ == "__main__":
    import shutil

    test_dir = Path(__file__).parent / "_test_account"
    test_dir.mkdir(parents=True, exist_ok=True)

    (test_dir / "tops.json").write_text(json.dumps({
        "detailed": [
            {"name": "太初神熊・摸摸原初GO", "type": "平衡型",
             "element": None, "status": "active",
             "binding": {"element_stage": {"element": "木"}}},
            {"name": "磐古神熊・摸摸鎮岳GO", "type": "防禦型",
             "element": None, "status": "secondary",
             "binding": {"element_stage": {"element": "土"}}},
            {"name": "其他", "type": "攻擊型", "element": "火", "status": "bench"},
        ]
    }, ensure_ascii=False), encoding="utf-8")

    (test_dir / "satellites.json").write_text(json.dumps({
        "active_satellite_name": "轟鳴皇",
    }, ensure_ascii=False), encoding="utf-8")

    strategy = BattleStatusLineStrategy(account_data_dir=test_dir)
    result = strategy.observe({}, {})
    print(result["battle_status_line"] if result else "（無狀態）")

    shutil.rmtree(test_dir)