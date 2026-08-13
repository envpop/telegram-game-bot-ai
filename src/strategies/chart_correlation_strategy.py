"""
chart_correlation_strategy.py

「行情 [商品]」「契約行情 [商品]」這兩個指令，遊戲會發兩則 Telegram
訊息：一則文字（現價/本盤/今日），緊接著一則純圖片（6 小時走勢圖）。
這是 Telegram 這邊訊息+圖檔拆開發送的固定模式，語意上是「同一次查詢
的同一份回應」，只是被拆成兩則訊息——這裡負責把它們重新配對起來。

圖片下載策略：不依賴 monitor.py 有沒有預先下載。訊息跟它的媒體參照
在 Telegram 伺服器上不會消失，這裡在真的需要這張圖（判斷出它是行情
查詢的配對圖）的當下，直接用 chat_id + message_id 事後補抓，不需要
在送出指令前預先攔截、也不用讓 monitor.py 或 executor.py 認識任何
遊戲指令語意——維持兩邊「誰也不 import 誰」的界線不變。

這是唯一需要非同步 I/O 的 strategy（要下載圖片），所以 observe() 是
async def，main.py 裡要用 await 呼叫，不能塞進同步的 StrategyPipeline
清單裡（其餘 strategy 都是同步的，例如 market_tracking_strategy）。

流程：
    1. 看到 market_quote shape 的文字 → 記住「現在在等這個商品的圖」
    2. 短時間內、同一個 chat 收到一張圖片訊息 → 配對成功
       - 如果 monitor.py 剛好已經下載了（image_path 有值），直接用
       - 沒有的話，用 chat_id + message_id 重新抓這則訊息、下載圖片
    3. 呼叫 chart_worker 解析圖片，解析出來的時間序列逐點展開成帶
       時間戳記的 market_prices.jsonl 紀錄，跟文字查詢/市集查詢寫進
       同一份檔案，讓下游可以當同一份連續的價格資料庫使用
"""
import json
import time
from pathlib import Path

import chart_worker
from telegram_client import client

CORRELATION_WINDOW_SECONDS = 15


class ChartCorrelationStrategy:
    # shape 名稱 → 要寫進哪個檔名（跟哪個 strategy 各自的 common 資料檔對齊）
    _TRIGGER_SHAPES = {
        "market_quote": "market_prices.jsonl",
        "contract_quote": "contract_prices.jsonl",
    }

    def __init__(self, common_data_dir, media_dir):
        self.common_data_dir = Path(common_data_dir)
        self.common_data_dir.mkdir(parents=True, exist_ok=True)

        # 補抓下來的圖片存放位置，跟 monitor.py 平常下載的媒體資料夾
        # 分開，避免混在一起搞不清楚哪些是 monitor 自動下載、哪些是
        # 這裡按需求補抓的。
        self.media_dir = Path(media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)

        self._pending = None  # {"name": ..., "chat_id": ..., "seen_at": float, "target_file": ...}

    async def observe(self, parsed, record):
        shape = parsed.get("shape")
        if shape in self._TRIGGER_SHAPES:
            structured = parsed.get("structured") or {}
            name = structured.get("name")
            if name:
                self._pending = {
                    "name": name,
                    "chat_id": record.get("chat_id"),
                    "seen_at": time.time(),
                    # 用文字訊息自己的時間當基準，不要等圖片配對成功後
                    # 才用圖片的時間——圖片是三則訊息裡最後到、也最可能
                    # 因為下載/網路延遲晚到的一則，「現價」是文字那則
                    # 報出來的，圖表右端（現在）理論上跟文字回應是同一個
                    # 伺服器端當下算出來的，時間基準不該被圖片的延遲污染。
                    "query_time": record.get("message_date") or record.get("recorded_at"),
                    "target_file": self.common_data_dir / self._TRIGGER_SHAPES[shape],
                }
            return None

        if self._pending is None:
            return None

        has_image = bool((record.get("media") or {}).get("has_photo")) or record.get("is_image")
        if not has_image:
            return None
        if record.get("chat_id") != self._pending["chat_id"]:
            return None
        if time.time() - self._pending["seen_at"] > CORRELATION_WINDOW_SECONDS:
            self._pending = None
            return None

        pending = self._pending
        self._pending = None  # 不管解析成不成功，這次配對都用掉了

        image_path = record.get("image_path")
        if not image_path:
            image_path = await self._download_image(record)
        if not image_path:
            return None  # 補抓失敗，放棄這張，不要用不完整的資料硬湊

        result = chart_worker.parse_chart_image(image_path, text_hint=pending["name"])
        if result.get("series") is None:
            return None

        self._persist_series(pending["name"], pending["query_time"], pending["target_file"], result)
        return None

    async def _download_image(self, record):
        """monitor.py 沒有事先下載這張圖時，事後用 chat_id + message_id
        重新抓一次。訊息跟媒體參照在 Telegram 伺服器上不會因為
        monitor.py 當初沒下載就消失，隨時可以補抓。"""
        chat_id = record.get("chat_id")
        message_id = record.get("message_id")
        try:
            message = await client.get_messages(chat_id, ids=message_id)
            if message is None or message.photo is None:
                return None
            file_path = self.media_dir / f"{chat_id}_{message_id}.jpg"
            await client.download_media(message, file=str(file_path))
            return str(file_path)
        except Exception as e:
            print(f"[WARN] chart_correlation_strategy 補抓圖片失敗："
                  f"chat={chat_id} msg={message_id} 錯誤：{e}")
            return None

    def _persist_series(self, name, base_time, target_file, chart_result):
        confidence = chart_result.get("confidence", 0.0)

        with target_file.open("a", encoding="utf-8") as f:
            for point in chart_result["series"]:
                # offset_minutes 是負值（過去）到 0（現在）。這裡刻意不把它
                # 加進 base_time 換算成絕對時間戳記——時區/跨日的加減處理
                # 容易埋錯誤在看不到的地方，與其算錯還不如兩個欄位都存、
                # 讓消費端自己決定怎麼組合。
                entry = {
                    "recorded_at": base_time,
                    "name": name,
                    "price": point["price"],
                    "offset_minutes": point["offset_minutes"],
                    "source": "chart",
                    "confidence": confidence,
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
