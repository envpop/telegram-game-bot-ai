"""
strategy_pipeline.py

集中管理「每筆訊息都要跑一次」的被動 strategy（記錄/追蹤型：觀察
parsed 結果、自己決定要不要持久化，不主動觸發任何遊戲內動作）。

跟 action_dispatcher.py 的差別：ActionDispatcher 管的是「符合條件就
觸發動作」（例如按按鈕、送出下一個指令），這裡管的是「單純觀察並記
錄」，兩種性質不同，不合併成同一個清單。

之後新增一個這類 strategy（例如 satellite_training_strategy 也要開始
追蹤某種跨指令狀態），main.py 完全不用改，只要在下面 main.py 建立
STRATEGY_PIPELINE 的地方，把新的 strategy 加進清單即可。

這是「大腦程式」出現之前的過渡設計：先把所有被動 strategy 集中在一個
地方跑，之後大腦程式要接管執行順序、或決定要不要往後跑某個 strategy
時，直接把這個清單交給它處理就好，不用重新設計一次。

strategy 的介面約定：
    strategy.observe(parsed, record) -> dict | None

    回傳的 dict（如果有）會被合併進 parsed，讓後面的 display_formatter
    等其他呼叫端可以直接從 parsed 裡讀到，不用額外傳參數、也不用知道
    是哪個 strategy 產生的。回傳 None 代表這筆訊息這個 strategy 沒有
    要附加的東西。
"""


class StrategyPipeline:
    def __init__(self, strategies):
        self.strategies = strategies

    def run(self, parsed, record):
        for strategy in self.strategies:
            extra = strategy.observe(parsed, record)
            if extra:
                parsed.update(extra)
        return parsed