# -*- coding: utf-8 -*-
"""
parsing/response_shapes/top_record.py

「陀螺戰績」查詢回覆的 shape module，對齊專案既有慣例
（signature() / parse() / format_for_display()）。

跟 inventory_parsers.py 處理的「陀螺收藏」「綁定一覽」是不同訊息：
這個是查當下樓層進度用的（📊 開頭），不做存檔，純顯示。

=== 2026-08-17 修正 ===
原本這裡直接呼叫 append_recommendation_footer()，把 format_for_display()
改成三參數（parsed, account_id, base_dir）。但 response_parser.py 對所有
shape 一律用單參數呼叫 format_for_display(structured)，沒人知道要多傳
account_id/base_dir——導致 account_id 永遠是 None，footer 永遠不出現，
是個徹底沒被呼叫到的死分支，不是 recommendation_footer 本身壞掉。

正確作法：shape module 只管「文字 → structured → 給人看的顯示文字」，
不碰帳號、不碰 roster。「要不要加建議 footer」是 post-parse 才知道的事
（要讀 roster、跨模組），交給 StrategyPipeline 裡的
query_advisor_strategy.py 處理，讀 parsed['shape']/parsed['raw_text']，
在 display_text 組好之後才附加建議，不用改這裡的函式簽名。

=== 2026-08-19 補上：抽取「出戰：...」那行 ===
陀螺戰績是「目前出戰是誰」的第四個資訊來源（前三個：我的陀螺／綁定
一覽／出戰確認訊息，都已經同步寫回 tops.json 的 status 欄位）。這裡
多抽出 active_name/active_rarity/active_type/active_power 四個欄位，
交給 profile_sync_strategy.py 用跟 active_top_confirmation.py 一樣的
「子字串＋戰力消歧」邏輯同步——陀螺戰績給的名字一樣是簡化過的（拿掉
稱號前綴/強化值/綁定標籤），不能直接跟 tops.json 完整 name 比對相等，
這裡只負責抽取，不做比對（比對邏輯留在 profile_sync_strategy.py，
跟另外兩個來源共用同一份，不要在這裡重新寫一次）。
"""

import re


_SIGNATURE_RE = re.compile(r"的陀螺戰績")

# 出戰：曜金神熊・摸摸太白GO（神・平衡型・戰力 535）
RE_ACTIVE_TOP = re.compile(
    r"出戰[:：](?P<name>.+?)（(?P<rarity>[^・]+)・(?P<type>[^・]+)・戰力\s*(?P<power>\d+)）"
)


def signature(text: str) -> bool:
    return bool(_SIGNATURE_RE.search(text or ""))


def parse(text: str) -> dict:
    """raw_text 給 format_for_display() 原樣顯示用，另外抽取出戰陀螺
    四個欄位給 profile_sync_strategy.py 同步用——這個 shape 不存檔，
    是查詢型指令，抽取出來的欄位不影響顯示，只是多提供資訊給下游。"""
    m = RE_ACTIVE_TOP.search(text or "")
    return {
        "raw_text": text,
        "active_name": m.group("name") if m else None,
        "active_rarity": m.group("rarity") if m else None,
        "active_type": m.group("type") if m else None,
        "active_power": int(m.group("power")) if m else None,
    }


def format_for_display(parsed: dict) -> str:
    """原樣顯示遊戲回覆。建議 footer 不在這裡加，交給
    query_advisor_strategy.py 在 pipeline 後段處理。"""
    return parsed["raw_text"]


if __name__ == "__main__":
    sample = """📊 @envpop 的陀螺戰績
──────────────
目前關卡：第 100 階・摸摸熊・原初真神 🌟神位
最高通關：第 100 階　連勝：0
出戰：曜金神熊・摸摸太白GO（神・平衡型・戰力 535）
收藏：38 顆"""

    print("signature() =", signature(sample))
    parsed = parse(sample)
    print(parsed)
    print()
    print(format_for_display(parsed))