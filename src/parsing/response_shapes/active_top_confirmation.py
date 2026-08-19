# -*- coding: utf-8 -*-
"""
parsing/response_shapes/active_top_confirmation.py

「出戰 N」指令送出後，遊戲回覆的確認訊息：
    ✅ 出戰陀螺設為：終式・獄熊・雷咆斬（UR・攻擊型・戰力 395）

這則訊息本身不影響 tops.json 裡任何欄位——除非有東西去讀它、寫回快照。
在這支 shape 出現以前，這條路徑完全沒人處理，導致 tops.json 記錄的
「目前出戰是誰」在切換之後立刻過期，只有下次查「我的陀螺」/「綁定一覽」
才會重新對。清護衛半自動化因此撞到「同一個切換動作被重複執行」的迴圈
（見對話記錄 2026-08-19 的實測 log）。這支 shape 解析出來的結果，
搭配 profile_sync_strategy.py 的存檔動作，讓「目前出戰」隨時保持準確。

名字比對注意事項：確認訊息裡的名字是簡化過的（拿掉稱號前綴、強化值、
綁定標籤），不能拿來跟 tops.json 的完整 name 欄位直接比對相等：
    確認訊息："終式・獄熊・雷咆斬"
    tops.json："荒野碎鐵・終式・獄熊・雷咆斬 +17💥爆擊綁定III"（原始，
                實際存檔的 name 欄位本身不含強化值/綁定，這裡是原文示意）
比對邏輯（子字串 + 戰力消歧）留給 profile_sync_strategy.py 處理，
這支 shape 只負責把確認訊息拆成 name/rarity/type/power 四個欄位，
不做比對。

=== 2026-08-19 補上：夾帶的副陀螺自動卸下資訊 ===
實測發現這則訊息常常夾帶第二段，說明副陀螺被遊戲自動卸下（遊戲規則：
副陀螺五行不能跟主陀螺相同，切換主陀螺後如果撞到這條限制就會自動卸）。
有兩種子格式：
    (a) 指名道姓：
        🌗 副陀螺「極・天熊・滅卻牙」跟新主手同樣是🔴火屬性,已自動卸下。
    (b) 不指名（因為卸下的正是剛切換成主陀螺的那顆自己）：
        🌗 這顆原本是你的副陀螺,已自動卸下（主副不能是同一顆）。

這兩種都代表「副陀螺欄位要清空／改回 bench」，profile_sync_strategy.py
處理時：(a) 用名字去 tops.json 找到那顆改掉；(b) 不用找，反正這顆
本來就會被主同步邏輯標記成 active，跟 secondary 互斥，不用另外處理。
這裡只負責把兩種情況都解析出來，標成統一的欄位：
    secondary_auto_unequipped: bool     有沒有夾帶這段
    secondary_unequipped_name: str|None (a) 情況有名字；(b) 情況是 None
"""

import re


RE_CONFIRMATION = re.compile(
    r"✅\s*出戰陀螺設為[:：]\s*(?P<name>.+?)"
    r"（(?P<rarity>[^・]+)・(?P<type>[^・]+)・戰力\s*(?P<power>\d+)）"
)

# 🌗 副陀螺「極・天熊・滅卻牙」跟新主手同樣是🔴火屬性,已自動卸下。
RE_SUB_UNEQUIP_NAMED = re.compile(r"副陀螺「(?P<name>.+?)」.*?已自動卸下")

# 🌗 這顆原本是你的副陀螺,已自動卸下（主副不能是同一顆）。
RE_SUB_UNEQUIP_SELF = re.compile(r"這顆原本是你的副陀螺.*?已自動卸下")


def signature(text: str) -> bool:
    return bool(RE_CONFIRMATION.search(text or ""))


def parse(text: str) -> dict:
    m = RE_CONFIRMATION.search(text)

    named = RE_SUB_UNEQUIP_NAMED.search(text or "")
    is_self = bool(RE_SUB_UNEQUIP_SELF.search(text or ""))

    if not m:
        return {
            "name": None, "rarity": None, "type": None, "power": None,
            "secondary_auto_unequipped": bool(named or is_self),
            "secondary_unequipped_name": named.group("name") if named else None,
            "raw_text": text,
        }

    return {
        "name": m.group("name"),
        "rarity": m.group("rarity"),
        "type": m.group("type"),
        "power": int(m.group("power")),
        "secondary_auto_unequipped": bool(named or is_self),
        "secondary_unequipped_name": named.group("name") if named else None,
        "raw_text": text,
    }


def format_for_display(parsed: dict) -> str:
    return parsed["raw_text"]


if __name__ == "__main__":
    samples = [
        "✅ 出戰陀螺設為：終式・獄熊・雷咆斬（UR・攻擊型・戰力 395）",
        "✅ 出戰陀螺設為：焚天神熊・摸摸赤焱GO（神・攻擊型・戰力 631）\n🌗 副陀螺「極・天熊・滅卻牙」跟新主手同樣是🔴火屬性,已自動卸下。",
        "✅ 出戰陀螺設為：焚天神熊・摸摸赤焱GO（神・攻擊型・戰力 631）\n🌗 這顆原本是你的副陀螺,已自動卸下（主副不能是同一顆）。",
    ]
    for s in samples:
        print("signature() =", signature(s))
        print(parse(s))
        print()