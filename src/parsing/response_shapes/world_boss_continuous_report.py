# -*- coding: utf-8 -*-
"""
shapes/world_boss_continuous_report.py

處理「連續討伐」指令的回覆——這跟單刀的「討伐」回覆(world_boss_battle_
report.py 認的「⚔️【共鬥討伐】」格式)是完全不同的訊息格式，不是同一個
shape 的變形，標頭都不一樣(這裡是「⚔️⚡【連續討伐】」)，內文是逐刀列表
+ 彙總，不是單刀的弱點/護衛細節。

2026-09-05 因為這個原因漏掉：furnace_loop_strategy.py 送出「連續討伐」
後，原本設計成反應 world_boss_battle_report shape 判斷次數用完，但
「連續討伐」的回覆根本對不上那個 shape 的 signature，導致完全沒有任何
程式碼認得這則訊息，furnace_loop 卡住進不了爐火重置——這支 shape 就是
補這個洞。

只驗證過 1 則實際樣本(7 刀、剛好打到次數用完)，還沒看到：
  - 中途王被打死、次數沒用完就停止的版本(「今日討伐次數用完」這句話
    可能不會出現，用什麼代替不知道)
  - 只打了 1 刀的邊界情況(「共 1 刀」會不會变成不同措辞)
之後補到新樣本再回頭驗證，目前 daily_count/daily_limit 抓不到時就是
None，呼叫端要自己處理「不知道」的情況，不要假設一定抓得到。

signature(): 判斷是不是「連續討伐」回覆
parse(): 抽成結構化資料
format_for_display(): 組出摘要文字
"""
import re

RE_HEADER = re.compile(r"⚔️⚡【連續討伐】世界王\s*(?P<name>\S+)")
RE_HIT_LINE = re.compile(r"第\s*(?P<n>\d+)\s*刀\s*[—-]\s*(?P<dmg>[\d,]+)\s*傷害")
RE_SUMMARY = re.compile(r"共\s*(?P<hits>\d+)\s*刀、(?P<total>[\d,]+)\s*傷害")
RE_DAILY_EXHAUSTED = re.compile(r"今日討伐次數用完\((?P<count>\d+)/(?P<limit>\d+)\)")


def signature(text):
    return "【連續討伐】世界王" in text


def parse(text):
    header = RE_HEADER.search(text)
    summary = RE_SUMMARY.search(text)
    exhausted = RE_DAILY_EXHAUSTED.search(text)
    hits = [int(dmg.replace(",", "")) for _, dmg in RE_HIT_LINE.findall(text)]

    return {
        "boss_name": header.group("name") if header else None,
        "hit_damages": hits,
        "total_hits": int(summary.group("hits")) if summary else (len(hits) or None),
        "total_damage": int(summary.group("total").replace(",", "")) if summary else None,
        "daily_exhausted": bool(exhausted),
        "daily_count": int(exhausted.group("count")) if exhausted else None,
        "daily_limit": int(exhausted.group("limit")) if exhausted else None,
    }


def format_for_display(parsed):
    lines = []
    if parsed["boss_name"]:
        lines.append(f"⚔️⚡ 連續討伐：{parsed['boss_name']}")
    if parsed["total_hits"] is not None and parsed["total_damage"] is not None:
        lines.append(f"共 {parsed['total_hits']} 刀，總傷害 {parsed['total_damage']:,}")
    if parsed["daily_count"] is not None:
        limit_note = "（今日次數已用完）" if parsed["daily_exhausted"] else ""
        lines.append(f"🫵 今日 {parsed['daily_count']}/{parsed['daily_limit']} 次{limit_note}")
    return "\n".join(lines) if lines else "(連續討伐訊息解析失敗)"
