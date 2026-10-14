"""安装后的第一次会话展示短说明。同一份说明只出现一次。"""
import json
import os
from pathlib import Path
import sys


def find_manual():
    root = os.environ.get('CLAUDE_PLUGIN_ROOT', '').strip()
    if root:
        candidate = Path(root) / 'docs' / '安装后说明.md'
        if candidate.is_file():
            return candidate
    start = Path(__file__).resolve().parent
    for parent in [start, *start.parents]:
        candidate = parent / 'docs' / '安装后说明.md'
        if candidate.is_file():
            return candidate
    return None


def marker_path():
    override = os.environ.get('SJ_MANUAL_STATE', '').strip()
    if override:
        return Path(override)
    data = os.environ.get('CLAUDE_PLUGIN_DATA', '').strip()
    if data:
        return Path(data) / 'manual-shown'
    return Path.home() / '.sjskill' / 'manual-shown'


def main():
    manual = find_manual()
    if manual is None:
        return 0
    text = manual.read_text(encoding='utf-8').strip()
    if not text:
        return 0
    marker = marker_path()
    seen = Path.home() / '.sjskill' / 'seen'
    try:
        previous = marker.read_text(encoding='utf-8').strip() if marker.is_file() else None
        already_shown = previous == text or (previous is None and seen.is_file())
    except OSError:
        return 0
    payload = {
        'suppressOutput': True,
        'systemMessage': text,
        'hookSpecificOutput': {
            'hookEventName': 'SessionStart',
            'additionalContext': '用户已在会话开头看到 SJskill 使用说明。除非用户询问怎么用，不要复述这份说明。',
        },
    }
    if not already_shown:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    if previous != text:
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(text + '\n', encoding='utf-8')
        except OSError:
            pass
    # 与独立技能共用首次提示标记；老版本只留下 manual-shown 时也补齐。
    try:
        seen.parent.mkdir(parents=True, exist_ok=True)
        with seen.open('x', encoding='utf-8'):
            pass
    except OSError:
        pass
    return 0


if __name__ == '__main__':
    try:
        sys.stdin.read()
    except OSError:
        pass
    raise SystemExit(main())
