# -*- coding: utf-8 -*-
"""
parsing/response_shapes/    

「我的陀螺」（🧰 你的陀螺收藏）的 shape module。

=== 顯示規則（熊確認）===
    收藏 < 50 顆：原文照樣顯示，不精簡（數量不多，看全部沒負擔）。
    收藏 >= 50 顆：
        神／UR（含旗下旋神／旋王／UR精選／鑄造）── 全部列出，保留原編號
        SSR／SR／R／N ── 只顯示數量，不列編號

    重點限制：編號是「出戰 編號」下指令用的，一定要跟伺服器的原始編號
    完全相符，不能重編。所以 50 顆以上模式下的每一行都是直接用
    parse_my_tops() 解析出來的 structured 欄位（index/name/type/...）
    重建，不是憑印象改寫原文——這樣編號保證跟 structured['detailed']
    一致，不會因為重組文字時手滑打錯。

    SSR 以下之所以只留數量：戰力不夠、不會拿來出戰或當建議候選，
    只是「可以再利用（分解/合成之類）的素材庫存」，知道編號沒有實際
    用途，列出來只會把畫面塞滿、真正重要的神/UR 反而要往下捲很多才看得到。
"""

from inventory_parsers import is_my_tops_message, parse_my_tops

# 收藏達到這個數量才轉成精簡模式（UR以上全列＋SSR以下只列數量）。
# 沒到門檻就原文顯示，不折騰。
SUMMARY_THRESHOLD = 50

# rarity_summary 裡，神/UR 已經在 detailed 裡完整列出，這裡統計「其餘」
# 時要排除，只列 SSR/SR/R/N 這幾種數量。順序照戰力等級高到低排，
# 符合閱讀直覺；不在這個順序清單裡的稀有度（理論上不會出現）保底放最後。
_SUMMARY_ORDER = ["SSR", "SR", "R", "N"]
_DETAILED_RARITIES = {"神", "UR"}


def signature(text: str) -> bool:
    return is_my_tops_message(text)


def parse(text: str) -> dict:
    result = parse_my_tops(text)
    result["raw_text"] = text
    return result


def _format_top_line(t: dict) -> str:
    """完全用 structured 欄位重建一行，跟原文格式一致，但不依賴原文
    字串本身——保證編號一定是 structured['detailed'] 裡的 index，
    不會因為重組文字漏字/打錯而跟伺服器實際編號對不上。"""
    marker = {"active": "⭐", "secondary": "🌗"}.get(t.get("status"), "")
    stars = "✦" * t.get("stars", 0)
    evo = t.get("evolution_marker") or ""
    enh = f" +{t['enhancement']}" if t.get("enhancement") else ""
    bind = f"{t['bind_type']}{t.get('bind_tier') or ''}" if t.get("bind_type") else ""

    return (
        f"{t['index']}. {marker}{evo} {t['name']}{enh}{bind}"  #把{stars}稀有度拿掉
        f"｜{t['rarity']}・{t['type']}・戰力 {t['power']}"
    )


def _extract_trailer(raw_text: str) -> str:
    """原文最後一段操作說明（換出戰/綁定提示…），精簡模式下照樣保留，
    只是不重列 SSR 以下逐筆——操作說明跟收藏內容無關，值得保留。"""
    parts = raw_text.split("──────────────")
    if len(parts) >= 3:
        return parts[-1].strip()
    return ""


def format_for_display(parsed: dict) -> str:
    total = parsed.get("total_count", 0)

    if total < SUMMARY_THRESHOLD:
        return parsed["raw_text"]

    detailed = parsed.get("detailed") or []
    rarity_summary = parsed.get("rarity_summary") or {}

    lines = [f"🧰 你的陀螺收藏（共 {total} 顆，UR以上全列・SSR以下只列數量）", "──────────────"]
    for t in detailed:
        lines.append(_format_top_line(t))

    other_counts = {r: c for r, c in rarity_summary.items() if r not in _DETAILED_RARITIES}
    if other_counts:
        parts = [f"{r}×{other_counts[r]}" for r in _SUMMARY_ORDER if r in other_counts]
        for r, c in other_counts.items():  # 保底：非預期稀有度也不遺漏
            if r not in _SUMMARY_ORDER:
                parts.append(f"{r}×{c}")
        lines.append("──────────────")
        lines.append("（其餘，只列數量）" + "　".join(parts))

    trailer = _extract_trailer(parsed["raw_text"])
    if trailer:
        lines.append("──────────────")
        lines.append(trailer)

    return "\n".join(lines)


if __name__ == "__main__":
    # 50 顆以下：原文顯示
    small_sample = """🧰 你的陀螺收藏（共 3 顆,⭐=出戰　🌗=副陀螺,依戰力排序）
──────────────
1. ⭐✦✦✦✦ 泥巴星球｜UR・防禦型・戰力 208
2. ✦✦✦✦ 光熊・幻滅爪｜UR・平衡型・戰力 206
3. ✦✦✦✦ 絕・煉熊・碎星牙｜UR・防禦型・戰力 200
──────────────
換出戰：打「出戰 編號」｜🌗 副陀螺：打「副陀螺 編號」｜🔧 綁定過的打「我的天賦」"""

    print("=== <50 顆：原文 ===")
    parsed = parse(small_sample)
    print(format_for_display(parsed))
    print()

    # 手動組一份 >=50 顆的假資料測精簡模式（真實情境靠實際 131 顆訊息驗證）
    fake_detailed = [
        {"index": 1, "name": "泥巴星球", "match_key": "x", "rarity": "UR", "type": "防禦型",
         "power": 208, "stars": 4, "status": "active", "evolution_marker": None,
         "enhancement": None, "bind_type": None, "bind_tier": None},
        {"index": 9, "name": "磐岩旋王・絕盾GO", "match_key": "x", "rarity": "UR", "type": "防禦型",
         "power": 491, "stars": 4, "status": "bench", "evolution_marker": "👑",
         "enhancement": 15, "bind_type": "🌀回歸綁定", "bind_tier": "III"},
    ]
    fake_parsed = {
        "total_count": 131,
        "raw_text": small_sample,  # 只是借用來測 trailer 抓取，內容不影響邏輯
        "detailed": fake_detailed,
        "rarity_summary": {"神": 8, "UR": 30, "SSR": 3, "SR": 23, "R": 32, "N": 38},
    }
    print("=== >=50 顆：精簡模式 ===")
    print(format_for_display(fake_parsed))