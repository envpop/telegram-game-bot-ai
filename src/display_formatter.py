"""display_formatter.py —— 將 parser 結果整理成終端機顯示文字。"""


def _full_text_block(chat_name, message_id, tag, record):
    """還沒做 shape 時的 fallback：印完整原文，不截斷成單行，
    盡量對齊 monitor.py 原本印的資訊量（文字/按鈕文字/媒體/圖片），
    避免關掉 monitor 自己的輸出後，資訊反而變少。"""
    text = record.get("text") or "<無文字>"
    buttons = record.get("buttons") or []
    media = record.get("media")
    image_path = record.get("image_path")

    lines = [f"[{chat_name} #{message_id}] {tag}", text]

    if buttons:
        lines.append(f"按鈕：{len(buttons)} 個")
        for b in buttons:
            row = b.get("row")
            col = b.get("column")
            btn_text = b.get("text") or "<無文字按鈕>"
            lines.append(f"  [{row},{col}] {btn_text}")

    if image_path:
        lines.append(f"圖片：{image_path}")
    elif media:
        lines.append(f"媒體：{media}")

    return "\n".join(lines)


def format_display_line(record, parsed):
    """把一筆 raw record 與 parser 結果格式化成人類可讀的顯示文字。"""
    chat_name = record.get("chat_name", "<unknown>")
    message_id = record.get("message_id", "")
    text = (record.get("text") or "").replace("\n", " ").strip()
    preview = text if len(text) <= 60 else text[:60] + "..."
    if not preview:
        preview = "<無文字>"

    if parsed is None:
        return f"[{chat_name} #{message_id}] ⚠️ PARSER 執行失敗 | {preview}"

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
        return f"[{chat_name} #{message_id}] 👤指令 | ⚠️未辨識：{preview} → review_queue"

    if source_type == "server":
        shape = parsed.get("shape")
        if parsed.get("parsed") and shape:
            # 已知 shape：parser 給的是排版過的多行文字，不要再截斷成單行 preview
            display_text = parsed.get("display_text") or preview
            header = f"[{chat_name} #{message_id}] 🤖伺服器回應（{shape}）"
            return f"{header}\n{display_text}"
        # 還沒做 shape 的指令：印完整原文，不要截斷
        return _full_text_block(chat_name, message_id, "🤖伺服器回應", record)

    if source_type == "announcement":
        return _full_text_block(chat_name, message_id, "📢公告", record)

    return f"[{chat_name} #{message_id}] {source_type} | {preview}"