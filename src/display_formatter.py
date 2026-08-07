"""display_formatter.py —— 將 parser 結果整理成終端機顯示文字。"""


def format_display_line(record, parsed):
    """把一筆 raw record 與 parser 結果格式化成人類可讀的一行摘要。"""
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
        return f"[{chat_name} #{message_id}] 🤖伺服器回應 | {preview}"

    if source_type == "announcement":
        return f"[{chat_name} #{message_id}] 📢公告 | {preview}"

    return f"[{chat_name} #{message_id}] {source_type} | {preview}"
