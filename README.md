# 環境重建步驟

## 1. 安裝 Python 3.13.14

## 2. 克隆專案
git clone <你的專案網址>
cd TG_BOT

## 3. 建立虛擬環境
python -m venv .venv

## 4. 啟動虛擬環境
.\.venv\Scripts\Activate.ps1  # Windows PowerShell

## 5. 安裝套件
pip install -r requirements.txt

## 6. 設定環境變數
複製 .env.example 為 .env，填入你的 token

專案設計原則
本專案以 monitor、parser、chart_worker 三層分工為核心。monitor 只負責接收 Telegram 事件、保存 raw 資料與圖片，不負責內容解析或行情判斷。parser 負責將 raw 資料標準化、分類與分流，chart_worker 則只處理已判別好的圖片或行情資料。

資料格式固定
raw 資料欄位名稱會保持固定，不隨意改動。核心欄位包含 recorded_at、event_type、chat_id、chat_name、sender_id、message_id、message_date、text、buttons、media、image_path 與 is_image。若未來需要擴充，只建議新增欄位，不建議修改既有欄位名稱。

時間處理原則
所有時間欄位統一使用本地時間字串，格式維持 ISO 風格。recorded_at 代表 monitor 收到事件的時間，message_date 代表 Telegram 原始訊息時間轉換後的本地時間。未來若調整時區實作方式，輸出格式與欄位名稱仍應維持一致。

帳號與環境管理
若未來有多個帳號共用同一套程式，切換時只更動 session 設定，不更動 monitor 邏輯與資料格式。.env 僅用來保護帳號與環境資訊，避免把敏感設定寫進程式本體。程式邏輯與資料 schema 應盡量與帳號數量無關。

圖片與媒體處理
monitor 只負責將圖片下載到本機並記錄 image_path，不在這一層做圖片辨識。這樣可讓後續 parser 與 chart_worker 專注於自己的工作，不互相耦合。若訊息沒有圖片，則維持 image_path = None、is_image = False 的標準狀態。

new / edited 訊息保留
同一則訊息的 new 與 edited 事件都會被保留在 raw 中，不會在 monitor 階段合併或覆蓋。後續 parser 可以自行決定要保留最後一版、合併版本，或依事件順序進行分析。這樣能最大程度保留原始資訊，方便追查與回溯。

維護方式
monitor 的更新原則是「可以修 bug、可以補落地、可以新增欄位，但不要改欄位名稱與資料格式」。parser 與 chart_worker 則可以持續演進其邏輯，但不應要求 monitor 跟著頻繁變動。整體目標是讓資料收集層穩定、可追溯、可長期維護。