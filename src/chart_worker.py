"""
chart_worker.py

「行情 [商品]」指令會附帶一張 6 小時走勢圖。這張圖的資訊密度遠高於
同一則指令的文字部分——文字只給查詢當下一個時間點的快照，圖表則
隱含了過去 6 小時、大約每 5 分鐘一筆的完整走勢，要單靠不斷查價重建
同樣密度的資料，需要消耗大量查詢次數，圖表等於是「系統免費幫你算好
的歷史資料」，值得解析。

用像素解析而不是呼叫視覺模型的原因：遊戲的圖表是系統畫出來的固定
模板（不是照片），版面/顏色/座標軸格式每次都一致，適合用確定性的
影像處理——不用每張圖都花錢呼叫視覺模型，結果也不會因為模型每次
理解程度不同而不穩定。

做法：
    1. OCR 動態讀取 Y 軸左側的價格刻度文字，做線性回歸校準
       （不寫死價格數字，因為每個商品、每次查詢的價格區間都不同）
    2. 用色彩篩選抓出折線像素（漲=亮綠、跌=亮紅）
    3. 把每個 x 像素對應到的 y 像素，透過校準關係換算回實際價格
    4. 依「行情」圖表固定 6 小時、遊戲每 5 分鐘結算一輪的規律，
       重新取樣成 (offset_minutes, price) 的時間序列

這是像素解析出來的估計值，不是伺服器直接給的數字，一定有誤差
（實測跟文字訊息的現價對照，誤差落在 0.1～0.2 左右），所以每筆結果
都帶一個 confidence 分數，呼叫端要自己決定信任程度、不要當成精確值。
"""
import re
import os
import glob
import shutil
from datetime import datetime, timezone

import numpy as np
from PIL import Image

try:
    import pytesseract
    _HAS_OCR = True
except ImportError:
    _HAS_OCR = False


def _auto_configure_tesseract():
    """PATH 裡找不到 tesseract.exe 時，去幾個常見安裝位置找一次
    （尤其 winget 裝的路徑常常沒被系統 PATH 抓到，per-user 安裝的路徑
    裡還會帶一串版本號資料夾）。找到就設定給 pytesseract 用，找不到
    就印出來，不要讓它每次都用同一句「不知道為什麼失敗」卡住除錯。"""
    if not _HAS_OCR:
        return
    if shutil.which("tesseract"):
        return  # 系統 PATH 已經找得到，不用做任何事

    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        candidates += glob.glob(
            os.path.join(localappdata, "Microsoft", "WinGet", "Packages",
                         "*Tesseract*", "**", "tesseract.exe"),
            recursive=True,
        )

    for path in candidates:
        if os.path.isfile(path):
            pytesseract.pytesseract.tesseract_cmd = path
            print(f"[chart_worker] 自動找到 tesseract.exe：{path}")
            return

    print("[chart_worker] PATH 跟常見安裝位置都找不到 tesseract.exe，"
          "OCR 校準會持續失敗。麻煩執行以下指令找出實際安裝位置，"
          "回報路徑，我再幫你直接指定：\n"
          '  Get-ChildItem -Path "$env:LOCALAPPDATA\\Microsoft\\WinGet\\Packages" '
          '-Filter tesseract.exe -Recurse -ErrorAction SilentlyContinue')


_auto_configure_tesseract()


CHART_PERIOD_HOURS = 6          # 「行情」指令圖表固定是過去 6 小時
SAMPLE_INTERVAL_MINUTES = 5     # 遊戲每 5 分鐘結算一輪行情


def _find_line_pixels(arr):
    """抓折線像素（漲=亮綠、跌=亮紅），回傳 {x像素: y像素中位數}。"""
    r = arr[:, :, 0].astype(int)
    g = arr[:, :, 1].astype(int)
    b = arr[:, :, 2].astype(int)

    bright_green = (g > 140) & (g - r > 40) & (g - b > 40)
    bright_red = (r > 140) & (r - g > 40) & (r - b > 40)
    line_mask = bright_green | bright_red

    h, w = line_mask.shape
    x_to_y = {}
    for x in range(w):
        ys = np.where(line_mask[:, x])[0]
        if len(ys) > 0:
            x_to_y[x] = float(np.median(ys))
    return x_to_y


def _calibrate_y_axis(image):
    """OCR 讀左側 Y 軸的價格刻度文字，回傳 (slope, intercept)，
    price = slope * y_pixel + intercept。讀不到足夠刻度就回傳 None，
    呼叫端要視為「這張圖不可信」，不要硬用。"""
    if not _HAS_OCR:
        print("[chart_worker] 校準失敗：沒有安裝 pytesseract")
        return None

    w, h = image.size
    left_strip = image.crop((0, 0, max(1, int(w * 0.1)), h))
    left_strip = left_strip.resize((left_strip.width * 3, left_strip.height * 3))
    try:
        data = pytesseract.image_to_data(left_strip, output_type=pytesseract.Output.DICT, config="--psm 6")
    except Exception as e:
        print(f"[chart_worker] 校準失敗：呼叫 tesseract 時發生錯誤（{e}）——"
              f"通常代表 tesseract 沒裝好，或沒加進系統 PATH")
        return None

    y_pixels, y_values = [], []
    raw_texts = []
    for i, text in enumerate(data["text"]):
        text = text.strip()
        if not text:
            continue
        raw_texts.append(text)
        # 價格刻度可能是一位小數（96.0）也可能是兩位小數（25.88），
        # 這裡放寬成 1~2 位小數都接受，不要寫死成只認一種精度。
        if re.fullmatch(r"\d{1,4}\.\d{1,2}", text):
            y_center = (data["top"][i] + data["height"][i] / 2) / 3
            y_pixels.append(y_center)
            y_values.append(float(text))

    if len(y_pixels) < 2:
        print(f"[chart_worker] 校準失敗：OCR 讀到的文字是 {raw_texts}，"
              f"其中符合價格格式的只有 {len(y_pixels)} 個（至少需要 2 個才能校準）")
        return None

    slope, intercept = np.polyfit(y_pixels, y_values, 1)
    return float(slope), float(intercept)


def extract_trend_series(image_path):
    """把走勢圖轉成 (offset_minutes, price) 的時間序列，每 5 分鐘一筆，
    offset_minutes 是相對「現在」的分鐘數（負值代表過去，0 是現在）。

    回傳 None 代表這張圖沒辦法可靠解析（校準或描線失敗），呼叫端應該
    直接放棄這張圖，不要硬用不可信的資料拼湊結果。
    """
    image = Image.open(image_path).convert("RGB")
    arr = np.array(image)

    calibration = _calibrate_y_axis(image)
    if calibration is None:
        return None
    slope, intercept = calibration

    x_to_y = _find_line_pixels(arr)
    if len(x_to_y) < 10:
        return None

    xs = sorted(x_to_y.keys())
    x_min, x_max = xs[0], xs[-1]
    if x_max <= x_min:
        return None

    prices_by_x = [x_to_y[x] * slope + intercept for x in xs]

    total_minutes = CHART_PERIOD_HOURS * 60
    sample_offsets = list(range(-total_minutes, 1, SAMPLE_INTERVAL_MINUTES))
    xs_as_minutes = [(-total_minutes) + (x - x_min) / (x_max - x_min) * total_minutes for x in xs]

    sampled_prices = np.interp(sample_offsets, xs_as_minutes, prices_by_x)

    return [
        {"offset_minutes": int(offset), "price": round(float(price), 2)}
        for offset, price in zip(sample_offsets, sampled_prices)
    ]


def parse_chart_image(image_path, text_hint=""):
    """回傳結構化的走勢資料。confidence 是粗略估計，不是精確統計量，
    只用來讓呼叫端判斷「這張圖信不信得過」，不是保證準確度。"""
    series = extract_trend_series(image_path)
    if series is None:
        return {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "text_hint": text_hint,
            "series": None,
            "confidence": 0.0,
            "note": "圖表解析失敗（座標軸校準或描線失敗），這筆結果不可信，不要拿去用",
        }

    high = max(series, key=lambda p: p["price"])
    low = min(series, key=lambda p: p["price"])
    current_price = series[-1]["price"]

    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "text_hint": text_hint,
        "period_hours": CHART_PERIOD_HOURS,
        "sample_interval_minutes": SAMPLE_INTERVAL_MINUTES,
        "series": series,
        "current_price": current_price,
        "extremes": {"high": high, "low": low},
        # 像素解析必然有誤差（線條粗細、抗鋸齒），這裡先給一個粗略信心
        # 分數，不是嚴謹統計量——之後如果發現常常抓錯，要重新檢討這個
        # 估計方式或改成依實測誤差動態計算
        "confidence": 0.85,
    }


def main():
    print("chart_worker ready")


if __name__ == "__main__":
    main()