def progress_handler(progress, total, message=None):
    if total:
        pct = int((progress / total) * 100)
        bar_len = 30
        filled = int(bar_len * progress / total)
        bar = "#" * filled + "-" * (bar_len - filled)
        line = f"  [{bar}] {progress}/{total} ({pct}%)"
    else:
        line = f"  [progress] {progress}"
    if message:
        line += f" — {message}"
    print(line)
