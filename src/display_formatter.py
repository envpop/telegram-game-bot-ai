"""display_formatter.py —— 將 parser 結果整理成終端機顯示文字。"""


def _has_image(record):
    """判斷這則訊息是否有圖片。不用 record['is_image']，因為那個欄位在
    monitor.py 的 DOWNLOAD_MEDIA_ENABLED 關閉時永遠是 False，不可靠；
    改看 media 裡的 has_photo，這個欄位不受下載開關影響。"""
    media = record.get("media") or {}
    if media.get("has_photo"):
        return True
    return bool(record.get("is_image"))


def _trailer(record):
    """按鈕/圖片的共用附加資訊：按鈕顯示文字選項，圖片只在有圖時提一句，
    不下載、不顯示路徑（圖片分析是之後才考慮的事，現在不需要）。"""
    lines = []
    buttons = record.get("buttons") or []
    if buttons:
        for b in buttons:
            row = b.get("row")
            col = b.get("column")
            btn_text = b.get("text") or "<無文字按鈕>"
            lines.append(f"  🔘 [{row},{col}] {btn_text}")
    if _has_image(record):
        lines.append("  🖼️ 有圖片")
    return lines


def format_display_line(record, parsed):
    """把一筆 raw record 與 parser 結果格式化成人類可讀的顯示文字。

    有 shape 判斷的，顯示 parser 解讀過的結果。沒有 shape 判斷的，
    顯示完整原文（不截斷）並標「尚未分類」——因為 monitor.py 在
    main.py 底下是關掉輸出的（monitor.PRINT_ENABLED = False），這裡
    是當下唯一看得到內容的地方，截斷成短短一段會讓人沒有足夠資訊判斷，
    等於瞎眼。截斷過的 preview 只留給「未辨識指令」「parser 執行失敗」
    這種本來就只是定位用、不需要完整內容的情境。

    parsed['market_pulse']：如果 strategy_pipeline 跑過
    market_tracking_strategy 並且有東西可以附加，會出現在這裡，直接讀
    就好，不用知道是哪個 strategy 產生的。
    """
    chat_name = record.get("chat_name", "<unknown>")
    message_id = record.get("message_id", "")
    raw_text = record.get("text") or ""
    single_line_preview = raw_text.replace("\n", " ").strip()
    if len(single_line_preview) > 60:
        single_line_preview = single_line_preview[:60] + "..."
    if not single_line_preview:
        single_line_preview = "<無文字>"

    if parsed is None:
        return f"[{chat_name} #{message_id}] ⚠️ PARSER 執行失敗 | {single_line_preview}"

    market_pulse = parsed.get("market_pulse")
    source_type = parsed.get("source_type")

    if source_type == "user":
        if parsed.get("recognized"):
            command = parsed.get("command")
            arguments = parsed.get("arguments") or []
            arg_str = " ".join(arguments)
            valid_shape = parsed.get("valid_shape")
            shape_flag = "" if valid_shape else " ⚠️參數數量不符"
            return (f"[{chat_name} #{message_id}] 👤指令 | {command} {arg_str}"
                    f" → route={parsed.get('route')}{shape_flag}")
        return f"[{chat_name} #{message_id}] 👤指令 | ⚠️未辨識：{single_line_preview} → review_queue"

    if source_type == "server":
        shape = parsed.get("shape")
        if parsed.get("parsed") and shape:
            # 已知 shape：parser 給的是排版過的多行文字，直接呈現判斷結果
            display_text = parsed.get("display_text") or raw_text or "<無文字>"
            header = f"[{chat_name} #{message_id}] 🤖伺服器回應（{shape}）"
        else:
            # 沒有對應的 shape：顯示完整原文，不截斷，只標「尚未分類」
            display_text = raw_text if raw_text else "<無文字>"
            header = f"[{chat_name} #{message_id}] 🤖伺服器回應（尚未分類）"
        base = f"{header}\n{display_text}"
        trailer = _trailer(record)
        if market_pulse:
            trailer = trailer + [f"  {market_pulse}"]
        return base if not trailer else base + "\n" + "\n".join(trailer)

    if source_type == "announcement":
        display_text = raw_text if raw_text else "<無文字>"
        base = f"[{chat_name} #{message_id}] 📢公告（尚未分類）\n{display_text}"
        trailer = _trailer(record)
        if market_pulse:
            trailer = trailer + [f"  {market_pulse}"]
        return base if not trailer else base + "\n" + "\n".join(trailer)

    return f"[{chat_name} #{message_id}] {source_type} | {single_line_preview}"