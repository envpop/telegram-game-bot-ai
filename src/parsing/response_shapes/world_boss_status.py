"""
shapes/world_boss_status.py

處理「世界王」查詢指令的回覆／王剛降臨的公告——兩者共用同一種標頭格式,
所以這裡的 parse() 邏輯刻意跟訊息來自哪個 chat 無關(純文字 → 結構化資料)。

=== 2026-09-02 大改版 ===
起因:原本的顯示過於簡化,只有王名/相位數字/弱點/護衛有沒有(布林值),但
實際訊息裡還有大量跟「怎麼打比較划算」直接相關的資訊完全沒被解析出來:
階級技能機制、異變(可能不只一個)、稀有禁制、賞金倍率、相位名稱(本體/
崩相/終相)、總進度、終相同類型傷害懲罰、潮引五行重骰倒數、讀式建議
(含「逆流」反著押的情況)、商會懸賞(🎫雲隱湯屋招待券)、護衛具體存活數
與王減傷%、個人今日次數與累積傷害、排行榜、深淵霸主(週王)狀態。

這次改版拿 138 則實際訊息樣本(涵蓋潮痕級到第 7+ 階、有無護衛、有無異變、
已死/存活、霸主現身中等各種狀態)全部跑過一輪,確認每條 regex 都有命中,
不是憑感覺寫。

同時修正一個先前就存在、影響到「決策」而不只是「顯示」的 bug:每天重生
的第一隻王(潮痕級)不會帶「【第 X 階】」這個階數標記,但原本的標頭 regex
寫死要求一定要有這個括號,導致:
  (a) 這裡的 parse() 對 base tier 訊息完全抓不到王名/類型/屬性
  (b) world_boss_catalog.json 的 name_pattern / trigger_pattern 也是同樣
      寫死的假設,導致「出現」跟「查詢」兩道保險對 base tier 王完全失效
      (詳見 world_boss_catalog.json 內的修正說明)
這裡只負責修 (a);(b) 是決策層,在 catalog JSON 裡另外修。

王名/弱點的抓取沿用 weakness_matcher.WeaknessParser 判斷「目前該打的屬性」
(current_element),避免跟 weakness_matcher.py 裡已驗證過的邏輯重複維護
兩份;其餘欄位(階級技能、異變、相位名稱、護衛細節等)是這則訊息獨有的
格式,不屬於 weakness_matcher 的職責範圍,所以直接在這支模組裡自己解析。

signature(): 判斷一段文字是不是這個 shape
parse(): 抽成結構化資料
format_for_display(): 組出摘要文字(這次改版後資訊量變多,但仍按「跟出手
    決策relevant」的優先順序排列,不是把訊息整段照抄)

=== 2026-08-17 舊版留下的介面限制(這次改版沿用) ===
format_for_display() 只吃一個參數(structured),不吃 account_id/base_dir/
raw_text——response_parser.py 對所有 shape 一律單參數呼叫,建議 footer 的
生成交給 query_advisor_strategy.py 在 pipeline 後段處理,不在這裡做。
"""
import re

import weakness_matcher

# 標頭: 👹 今日世界王【第 7 階】:深淵級・沉鐘（防禦型・🟡土屬性）
#       👹 今日世界王:潮痕級・黑瞳（攻擊型・🟡土屬性）  —— 潮痕級(base tier)沒有階數括號
RE_HEADER = re.compile(
    r"今日世界王(?:【第\s*(?P<stage>\d+)\s*階】)?[:：]\s*(?P<name>[^（]+)"
    r"（(?P<type>[^・]+)・\S*?(?P<element>[火土金木水])屬性）"
)

# 階級技能: 👁️【千面之淵】階級技能〈淵變〉:王每被打掉 20% 血就隨機置換一次五行,弱點屬性跟著重算
RE_CLASS_SKILL = re.compile(r"【(?P<title>[^】]+)】階級技能〈(?P<skill>[^〉]+)〉[:：](?P<desc>.+)")

# 異變區塊開頭: 🧬 異變:👝【貪食】王吞了寶囊:...
# 可能有多個異變,第 2 個以後用全形空白縮排另起一行,例如:
#     　　　🛰【共生】護衛被清空 30 分鐘後,深淵再派一批(每王最多 2 次)
RE_MUTATION_HEAD_PREFIX = re.compile(r"^🧬\s*異變[:：]\s*")
RE_MUTATION_ENTRY = re.compile(r"^.{1,2}【(?P<title>[^】]+)】(?P<desc>.+)$")

# 稀有禁制: 🃏 稀有禁制:SSR 以上打全額,SR ×0.5,更低 ×0.35
RE_RARITY_RESTRICTION = re.compile(r"稀有禁制[:：](?P<text>.+)")

# 賞金: 💰 賞金:擊殺點數獎勵 ×1.5
RE_BOUNTY = re.compile(r"💰\s*賞金[:：](?P<text>.+)")

# 相位: 🌗 相位 2/3【崩相】　總進度 🌫?? 或 總進度 15%
RE_PHASE_LINE = re.compile(
    r"相位\s*(?P<phase>\d+)\s*/\s*(?P<phase_total>\d+)【(?P<phase_name>[^】]+)】"
    r"\s*總進度\s*(?P<progress>\d+%|🌫\?\?)"
)

# 血量條: ███████░░░░░ 35018/56201　或　░░░░░░░░░░░░ 0/41964（已被討伐 ✅）
RE_HEALTH_BAR = re.compile(
    r"[█░]{5,}\s+(?P<cur>[\d,]+)/(?P<total>[\d,]+)(?:（(?P<dead>已被討伐[^）]*)）)?"
)
# 異變「霧障」生效時,血量條被替換成這種文字描述,沒有實際數字
RE_HEALTH_FOG = re.compile(r"🌫\s*濃霧遮蔽了王的身影……(?P<desc>.+)")

# 潮引倒數: 🌊 潮引:再 40 刀五行重骰
RE_ELEMENT_REROLL = re.compile(r"潮引[:：]再\s*(?P<hits_left>\d+)\s*刀五行重骰")

# 終相懲罰: 🩸 終相:跟上一刀同類型傷害只剩三分之一（上一刀:攻擊型）
RE_TERMINAL_PENALTY = re.compile(r"終相[:：]跟上一刀同類型傷害只剩三分之一（上一刀[:：](?P<prev_type>[^）]+)）")

# 讀式建議: 🎯 讀式:王氣息偏【🌀變】,附招「討伐 攻」較可能剋它(...)
# 「逆流」異變生效時,這行有時整行消失,改成只在異變描述裡提示「反著押」,
# reversed 用「逆流」這個關鍵字獨立判斷,不強求兩者同時出現。
RE_READ_PATTERN = re.compile(r"讀式[:：]王氣息偏【(?P<align>[^】]+)】,附招「討伐\s*(?P<move>\S)」")

# 商會懸賞: 🏮 商會懸賞中:此王的首位傷害者,可得 🎫雲隱湯屋招待券 ×1
RE_TICKET_BOUNTY = re.compile(r"商會懸賞中[:：]此王的首位傷害者,可得\s*(?P<reward>.+)")

# 護衛: 🛰️ 衛星護衛 6/6 顆還在 — 王減傷 52%（王照樣打得到）｜...
RE_GUARDS = re.compile(
    r"衛星護衛\s*(?P<alive>\d+)\s*/\s*(?P<total>\d+)\s*顆還在\s*[—-]\s*王減傷\s*(?P<reduction>\d+)%"
)

# 個人進度: 🫵 你今日:0/9 次,本王累積 804 傷害
RE_YOUR_STATS = re.compile(r"你今日[:：]\s*(?P<count>\d+)/(?P<limit>\d+)\s*次,本王累積\s*(?P<accum>[\d,]+)\s*傷害")

# 排行榜: 🏅 🥇@BillCho 18943　🥈@envpop 804　4.@someone 100
# 名次可能是獎牌 emoji(前三名)或「數字.」(自己排名在 4 名以後時)；
# 名字本身可能含空白(例如「Carl 鴨」),所以用「結尾抓數字」的方式反著切,
# 不能直接用空白切 token。
RE_LEADERBOARD_LINE = re.compile(r"🏅\s*(?P<entries>.+)")
RE_LEADERBOARD_ENTRY = re.compile(r"(?P<rank>🥇|🥈|🥉|\d+\.)(?P<name>.+?)\s(?P<damage>[\d,]+)$")

# 深淵霸主(週王,跟每日世界王是不同的王):
#   👹 深淵霸主:本週已落幕
#   👹 深淵霸主:本週全服已斬 6/15 隻(斬滿現身)
#   👹 深淵霸主現身中!打「霸主」看戰況、「討伐霸主」出刀
RE_WEEKLY_BOSS = re.compile(r"👹\s*深淵霸主(?P<text>[^\n]*)")
RE_WEEKLY_PROGRESS = re.compile(r"本週全服已斬\s*(?P<killed>\d+)\s*/\s*(?P<total>\d+)\s*隻")

# 全服增益: 💥 護衛全清增益:全服傷害 +15%（還有 10 分鐘）
RE_GLOBAL_BUFF = re.compile(r"護衛全清增益[:：](?P<text>.+)")

# 下一階倒數: ⏳ 第 7 階世界王將於 13:56 現身!
RE_NEXT_STAGE_TIMER = re.compile(r"第\s*(?P<stage>\d+)\s*階世界王將於\s*(?P<time>\d{2}:\d{2})\s*現身")

# 王已被討伐時,查詢回覆會帶這個字樣(出現在血量條那行的括號裡,例如
# 「░░░░░░░░░░░░ 0/41964（已被討伐 ✅）」)，substring 比對就夠。
RE_ALIVE_CHECK = re.compile(r"已被討伐 ✅")


def signature(text):
    # 不再要求一定要有「【第 X 階】」——潮痕級(base tier)沒有這個括號,
    # 舊版寫法會讓 base tier 訊息整個 fallback 顯示原文，不進這支 shape。
    return "今日世界王" in text


def _extract_mutations(text):
    """異變可能有多筆,第一筆接在「🧬 異變:」同一行,後續每筆各自一行、
    用全形空白縮排。逐行掃描,遇到縮排行就繼續收，遇到非縮排行就收工。
    """
    mutations = []
    lines = text.splitlines()
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("🧬"):
            in_block = True
            after = RE_MUTATION_HEAD_PREFIX.sub("", stripped)
            m = RE_MUTATION_ENTRY.match(after)
            if m:
                mutations.append({"title": m.group("title"), "description": m.group("desc").strip()})
            continue
        if in_block:
            if line.startswith("\u3000") or line.startswith("　"):
                m = RE_MUTATION_ENTRY.match(stripped)
                if m:
                    mutations.append({"title": m.group("title"), "description": m.group("desc").strip()})
                continue
            in_block = False
    return mutations


def _extract_leaderboard(text):
    m = RE_LEADERBOARD_LINE.search(text)
    if not m:
        return []
    entries = []
    for chunk in m.group("entries").split("\u3000"):
        chunk = chunk.strip()
        if not chunk:
            continue
        em = RE_LEADERBOARD_ENTRY.search(chunk)
        if em:
            entries.append({
                "rank": em.group("rank"),
                "name": em.group("name").strip(),
                "damage": int(em.group("damage").replace(",", "")),
            })
    return entries


def _extract_weekly_boss(text):
    m = RE_WEEKLY_BOSS.search(text)
    if not m:
        return None
    raw = m.group("text").strip().lstrip(":：").strip()
    if not raw:
        return None
    progress = RE_WEEKLY_PROGRESS.search(raw)
    if progress:
        status = "in_progress"
        killed = int(progress.group("killed"))
        total = int(progress.group("total"))
    elif "已落幕" in raw:
        status, killed, total = "ended", None, None
    elif "現身中" in raw:
        status, killed, total = "active", None, None
    else:
        status, killed, total = "unknown", None, None
    return {"status": status, "killed": killed, "total": total, "raw": raw}


def parse(text):
    header = RE_HEADER.search(text)
    weakness = weakness_matcher.WeaknessParser.parse(text)

    class_skill_m = RE_CLASS_SKILL.search(text)
    rarity_m = RE_RARITY_RESTRICTION.search(text)
    bounty_m = RE_BOUNTY.search(text)
    phase_m = RE_PHASE_LINE.search(text)
    health_m = RE_HEALTH_BAR.search(text)
    fog_m = RE_HEALTH_FOG.search(text)
    reroll_m = RE_ELEMENT_REROLL.search(text)
    terminal_m = RE_TERMINAL_PENALTY.search(text)
    read_m = RE_READ_PATTERN.search(text)
    ticket_m = RE_TICKET_BOUNTY.search(text)
    guards_m = RE_GUARDS.search(text)
    your_stats_m = RE_YOUR_STATS.search(text)
    global_buff_m = RE_GLOBAL_BUFF.search(text)
    next_stage_m = RE_NEXT_STAGE_TIMER.search(text)

    progress_raw = phase_m.group("progress") if phase_m else None
    progress_obscured = progress_raw == "🌫??"

    return {
        "stage": int(header.group("stage")) if header and header.group("stage") else None,
        "boss_name": header.group("name") if header else (weakness.boss_name if weakness else None),
        "boss_type": header.group("type") if header else None,
        "boss_element": header.group("element") if header else None,
        "current_element": weakness.current_element if weakness else None,
        "alive": not bool(RE_ALIVE_CHECK.search(text)),

        "class_skill": {
            "title": class_skill_m.group("title"),
            "skill_name": class_skill_m.group("skill"),
            "description": class_skill_m.group("desc").strip(),
        } if class_skill_m else None,
        "mutations": _extract_mutations(text),
        "rarity_restriction": rarity_m.group("text").strip() if rarity_m else None,
        "bounty_multiplier": bounty_m.group("text").strip() if bounty_m else None,

        "phase": int(phase_m.group("phase")) if phase_m else None,
        "phase_total": int(phase_m.group("phase_total")) if phase_m else None,
        "phase_name": phase_m.group("phase_name") if phase_m else None,
        "progress_pct": None if (not phase_m or progress_obscured) else int(progress_raw.rstrip("%")),
        "progress_obscured": progress_obscured,
        "health_current": int(health_m.group("cur").replace(",", "")) if health_m else None,
        "health_total": int(health_m.group("total").replace(",", "")) if health_m else None,
        "health_obscured_text": fog_m.group("desc").strip() if fog_m else None,

        "element_reroll_hits_left": int(reroll_m.group("hits_left")) if reroll_m else None,
        "terminal_penalty": {"previous_type": terminal_m.group("prev_type")} if terminal_m else None,
        "read_pattern": {
            "alignment": read_m.group("align"),
            "suggested_move": read_m.group("move"),
        } if read_m else None,
        "read_reversed": "逆流" in text,

        "ticket_bounty": bool(ticket_m),
        "ticket_bounty_reward": ticket_m.group("reward").strip() if ticket_m else None,

        "guards_alive": int(guards_m.group("alive")) if guards_m else None,
        "guards_total": int(guards_m.group("total")) if guards_m else None,
        "guard_damage_reduction_pct": int(guards_m.group("reduction")) if guards_m else None,
        "has_guards": (int(guards_m.group("alive")) > 0) if guards_m else (weakness.has_guards if weakness else None),

        "your_daily_count": int(your_stats_m.group("count")) if your_stats_m else None,
        "your_daily_limit": int(your_stats_m.group("limit")) if your_stats_m else None,
        "your_accumulated_damage": int(your_stats_m.group("accum").replace(",", "")) if your_stats_m else None,

        "leaderboard": _extract_leaderboard(text),
        "weekly_boss": _extract_weekly_boss(text),
        "global_buff": global_buff_m.group("text").strip() if global_buff_m else None,
        "next_stage_timer": {
            "stage": int(next_stage_m.group("stage")),
            "time": next_stage_m.group("time"),
        } if next_stage_m else None,
    }


def format_for_display(parsed):
    """組出摘要文字。順序依「跟出手決策相關程度」排列：先講清楚現在該怎麼打
    (弱點/讀式/護衛/特殊機制)，再講進度類資訊(相位/血量)，最後才是排行榜、
    全服狀態這類參考資訊。建議 footer 不在這裡加，交給
    query_advisor_strategy.py 在 pipeline 後段處理。"""
    lines = []

    if parsed["boss_name"]:
        stage = f"第{parsed['stage']}階 " if parsed["stage"] else ""
        type_element = ""
        if parsed["boss_type"] and parsed["boss_element"]:
            type_element = f"（{parsed['boss_type']}・{parsed['boss_element']}屬性）"
        lines.append(f"👹 {stage}{parsed['boss_name']}{type_element}")

    if not parsed["alive"]:
        lines.append("💀 已被討伐")

    if parsed["current_element"]:
        lines.append(f"🔮 弱點：{parsed['current_element']}屬性")

    if parsed["read_pattern"]:
        rp = parsed["read_pattern"]
        reversed_note = "（王「逆流」中，建議反著押！）" if parsed["read_reversed"] else ""
        lines.append(f"🎯 讀式：氣息偏【{rp['alignment']}】→附招「討伐 {rp['suggested_move']}」{reversed_note}")
    elif parsed["read_reversed"]:
        lines.append("🎯 讀式：王「逆流」中，讀式建議反著押（無固定附招字樣）")

    if parsed["guards_alive"] is not None:
        lines.append(
            f"🛰️ 護衛：{parsed['guards_alive']}/{parsed['guards_total']} 顆還在"
            f"，王減傷 {parsed['guard_damage_reduction_pct']}%"
        )
    elif parsed["has_guards"] is False:
        lines.append("🛰️ 護衛：已清空")

    if parsed["ticket_bounty"]:
        lines.append(f"🏮 商會懸賞：首位傷害者可得 {parsed['ticket_bounty_reward']}")

    if parsed["terminal_penalty"]:
        lines.append(f"🩸 終相懲罰：跟上一刀同類型（{parsed['terminal_penalty']['previous_type']}）傷害只剩 1/3")

    if parsed["element_reroll_hits_left"] is not None:
        lines.append(f"🌊 潮引：再 {parsed['element_reroll_hits_left']} 刀五行重骰")

    if parsed["phase"] and parsed["phase_total"]:
        phase_name = f"【{parsed['phase_name']}】" if parsed["phase_name"] else ""
        if parsed["progress_obscured"]:
            progress = "（霧障遮蔽，進度未知）"
        elif parsed["progress_pct"] is not None:
            progress = f"　總進度 {parsed['progress_pct']}%"
        else:
            progress = ""
        lines.append(f"🌗 相位 {parsed['phase']}/{parsed['phase_total']}{phase_name}{progress}")

    if parsed["health_current"] is not None and parsed["health_total"] is not None:
        lines.append(f"❤️ 血量：{parsed['health_current']:,}/{parsed['health_total']:,}")
    elif parsed["health_obscured_text"]:
        lines.append(f"🌫 血量：{parsed['health_obscured_text']}")

    if parsed["class_skill"]:
        cs = parsed["class_skill"]
        lines.append(f"👁️ 階級技能〈{cs['skill_name']}〉：{cs['description']}")

    for mutation in parsed["mutations"]:
        lines.append(f"🧬 異變【{mutation['title']}】：{mutation['description']}")

    if parsed["rarity_restriction"]:
        lines.append(f"🃏 稀有禁制：{parsed['rarity_restriction']}")

    if parsed["bounty_multiplier"]:
        lines.append(f"💰 賞金：{parsed['bounty_multiplier']}")

    if parsed["your_daily_count"] is not None:
        lines.append(
            f"🫵 你今日：{parsed['your_daily_count']}/{parsed['your_daily_limit']} 次"
            f"，本王累積 {parsed['your_accumulated_damage']:,} 傷害"
        )

    if parsed["leaderboard"]:
        board = "　".join(f"{e['rank']}{e['name']} {e['damage']:,}" for e in parsed["leaderboard"])
        lines.append(f"🏅 {board}")

    if parsed["global_buff"]:
        lines.append(f"💥 全服增益：{parsed['global_buff']}")

    if parsed["next_stage_timer"]:
        nt = parsed["next_stage_timer"]
        lines.append(f"⏳ 第 {nt['stage']} 階世界王將於 {nt['time']} 現身")

    if parsed["weekly_boss"]:
        wb = parsed["weekly_boss"]
        if wb["status"] == "in_progress":
            lines.append(f"👹 深淵霸主：本週全服已斬 {wb['killed']}/{wb['total']} 隻(斬滿現身)")
        elif wb["status"] == "active":
            lines.append("👹 深淵霸主現身中！打「霸主」看戰況、「討伐霸主」出刀")
        elif wb["status"] == "ended":
            lines.append("👹 深淵霸主：本週已落幕")
        else:
            lines.append(f"👹 深淵霸主：{wb['raw']}")

    return "\n".join(lines) if lines else "(世界王狀態解析失敗)"