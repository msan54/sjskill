#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""知识库体检·通用机械扫描器（仅使用 Python 标准库，严格只读）。

用法：
    python3 kb_scan.py "<知识库根目录>"
    python3 kb_scan.py "<知识库根目录>" --format markdown
    python3 kb_scan.py "<知识库根目录>" --only structure,scattered,stale,duplicates,rules,sharing
    python3 kb_scan.py "<知识库根目录>" --stale-days 90 --inbox-days 30
"""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys


ALL_CHECKS = ('structure', 'scattered', 'stale', 'duplicates', 'rules', 'sharing')

IGNORED_DIRS = {
    '.git', '.obsidian', '.stversions', '.trash', '.Trash', '.venv', 'venv',
    'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache', '.idea',
    '.vscode', '.next', 'dist', 'build',
}

IGNORED_FILES = {'.DS_Store', 'Thumbs.db', 'desktop.ini'}

ARCHIVE_DIR_WORDS = ('归档', '_归档', '存档', '_存档', 'archive', '_archive')
SCATTERED_HISTORY_WORDS = (
    '历史', '旧版', '旧资料', '备份', '废弃', '弃用', '过时',
    'history', 'backup', 'old', 'deprecated', 'bak',
)
INBOX_DIR_WORDS = (
    '收件箱', '待处理', '待审核', '草稿', '待发布', '输入', 'inbox', 'draft', 'todo', 'pending',
)
DONE_DIR_WORDS = ('已处理', '已发布', '已学习', '已完成', '已归档', 'done', 'archive', '归档')
SHARED_DIR_WORDS = ('共享', 'shared', 'team', '团队共享', '运营共享')

PRIMARY_RULE_FILES = ('AGENTS.md', 'CLAUDE.md', 'GEMINI.md')
EXTRA_RULE_FILES = ('.cursorrules', '知识库说明.md', '协作说明.md')

COMMON_STRUCTURAL_NAMES = {
    'readme.md', 'claude.md', 'agents.md', 'gemini.md', 'skill.md', 'license',
    'license.md', 'changelog.md', 'roadmap.md', 'index.md', '_索引.md', '_说明.md',
    '账号卡.md', '知识库说明.md', '协作说明.md', '使用说明.md', '数据总表.md',
}

SENSITIVE_FILENAME_WORDS = (
    '合同', '账单', '报价底线', '成本', '利润', '提成', '薪资', '工资',
    '密码', '密钥', '凭据', '线索明细', '手机号', '身份证',
)

HISTORY_LINE_WORDS = (
    '已删', '已删除', '已归档', '已停用', '已移除', '不再', '原目录', '原路径',
    '旧路径', '旧目录', '示例', '例如', '比如', '如 `', '样例',
)

PLACEHOLDER_TOKENS = ('[', ']', '<', '>', '{', '}', '...', '……', 'xxx', 'XXX', '某某', '示例')
CN_PHONE_RE = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')
TOKEN_PREFIX_RE = re.compile(r'\b(?:ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b')
BACKTICK_PATH_RE = re.compile(r'`([^`\n]+)`')
MD_LINK_RE = re.compile(r'\]\(([^)\n]+)\)')


def is_ignored_part(name):
    return name in IGNORED_DIRS or (name.startswith('.') and name not in ('.cursor', '.claude', '.codex', '.agents'))


def is_archive_root_dir(rel_parts):
    """判断是否为根目录或一级分区下的统一归档目录（合法归档区）。"""
    if not rel_parts:
        return False
    if len(rel_parts) == 1 and any(rel_parts[0].lower() == w or rel_parts[0].endswith(w) for w in ARCHIVE_DIR_WORDS):
        return True
    if len(rel_parts) == 2 and any(rel_parts[1].lower() == w or rel_parts[1].endswith(w) for w in ARCHIVE_DIR_WORDS):
        return True
    return False


def inside_archive(rel_parts):
    for idx in range(1, min(len(rel_parts), 2) + 1):
        if is_archive_root_dir(rel_parts[:idx]):
            return True
    return False


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_text_hash(path):
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''
    normalized = '\n'.join(line.rstrip() for line in text.replace('\r\n', '\n').splitlines()).strip()
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def walk_kb(root):
    """一次只读遍历，收集基础元信息，绝不跟随目录软链接防止死循环。"""
    dirs_info = []
    files_info = []
    skill_names = {}
    has_obsidian = (root / '.obsidian').exists()

    for current, dirnames, filenames in os.walk(root, followlinks=False):
        cur_path = Path(current)
        rel_dir = cur_path.relative_to(root)
        rel_parts = () if rel_dir == Path('.') else rel_dir.parts

        kept = []
        for d in sorted(dirnames):
            full_d = cur_path / d
            if d in IGNORED_DIRS or full_d.is_symlink():
                continue
            if d.startswith('.') and d not in ('.cursor',):
                continue
            kept.append(d)
        dirnames[:] = kept

        visible_files = [f for f in sorted(filenames) if f not in IGNORED_FILES and not f.startswith('.~')]
        if rel_parts:
            dirs_info.append({
                'rel': rel_dir.as_posix(),
                'parts': rel_parts,
                'depth': len(rel_parts),
                'has_subdirs': bool(dirnames),
                'file_count': len(visible_files),
                'in_archive': inside_archive(rel_parts),
            })

        if 'SKILL.md' in visible_files and rel_parts:
            skill_names[cur_path.name] = rel_dir.as_posix()

        for fn in visible_files:
            fp = cur_path / fn
            if fp.is_symlink():
                continue
            try:
                st = fp.stat()
            except OSError:
                continue
            rel_file = (rel_dir / fn) if rel_parts else Path(fn)
            files_info.append({
                'path': fp,
                'rel': rel_file.as_posix(),
                'name': fn,
                'parts': rel_file.parts,
                'depth': len(rel_file.parts) - 1,
                'size': st.st_size,
                'mtime': st.st_mtime,
                'suffix': fp.suffix.lower(),
                'in_archive': inside_archive(rel_parts),
            })

    return dirs_info, files_info, skill_names, has_obsidian


def check_structure(dirs_info, files_info, max_depth, large_kb):
    top_counts = defaultdict(int)
    for f in files_info:
        top = f['parts'][0] if len(f['parts']) > 1 else '(根目录)'
        top_counts[top] += 1

    empty_dirs = [
        d['rel'] for d in dirs_info
        if not d['has_subdirs'] and d['file_count'] == 0 and not d['in_archive']
        and not any(w in d['parts'][0] for w in ('模板', '_模板', 'template'))
    ]
    deep_dirs = [
        {'path': d['rel'], 'depth': d['depth']}
        for d in dirs_info if d['depth'] > max_depth and not d['in_archive']
    ]
    empty_files = [f['rel'] for f in files_info if f['size'] == 0 and not f['in_archive']]
    large_md = [
        {'path': f['rel'], 'size_kb': round(f['size'] / 1024, 1)}
        for f in files_info
        if f['suffix'] == '.md' and f['size'] >= large_kb * 1024 and not f['in_archive']
    ]
    return {
        'total_files': len(files_info),
        'markdown_files': sum(1 for f in files_info if f['suffix'] == '.md'),
        'total_dirs': len(dirs_info),
        'top_level_summary': dict(sorted(top_counts.items())),
        'empty_dirs': empty_dirs[:30],
        'empty_dirs_total': len(empty_dirs),
        'deep_dirs': deep_dirs[:20],
        'deep_dirs_total': len(deep_dirs),
        'empty_files': empty_files[:20],
        'large_markdown_files': sorted(large_md, key=lambda x: -x['size_kb'])[:15],
    }


def check_scattered(dirs_info, files_info):
    scattered_dirs = []
    for d in dirs_info:
        if d['in_archive'] or is_archive_root_dir(d['parts']):
            continue
        leaf = d['parts'][-1].lower()
        if any(w in leaf for w in SCATTERED_HISTORY_WORDS):
            scattered_dirs.append(d['rel'])
        elif any(w in leaf for w in ARCHIVE_DIR_WORDS) and d['depth'] >= 2:
            # 二级以上嵌套归档目录（非分区一级归档）
            scattered_dirs.append(d['rel'])

    bak_files = [
        f['rel'] for f in files_info
        if not f['in_archive'] and (
            f['suffix'] in ('.bak', '.tmp', '.orig')
            or any(tag in f['name'].lower() for tag in ('副本', ' copy', '-old', '_old', '备份'))
        )
    ]
    return {
        'scattered_history_dirs': scattered_dirs[:30],
        'scattered_history_dirs_total': len(scattered_dirs),
        'suspicious_backup_files': bak_files[:25],
        'suspicious_backup_files_total': len(bak_files),
    }


def check_stale(files_info, now_ts, stale_days, inbox_days):
    inbox_backlog = []
    stale_active = []
    for f in files_info:
        if f['in_archive']:
            continue
        age_days = int((now_ts - f['mtime']) // 86400)
        parent_joined = '/'.join(f['parts'][:-1]).lower()
        in_done = any(w in parent_joined for w in DONE_DIR_WORDS)
        in_inbox = any(w in parent_joined for w in INBOX_DIR_WORDS) and not in_done

        if in_inbox and age_days >= inbox_days:
            inbox_backlog.append({
                'path': f['rel'],
                'age_days': age_days,
                'modified': datetime.fromtimestamp(f['mtime'], tz=timezone.utc).strftime('%Y-%m-%d'),
            })
        elif age_days >= stale_days and f['suffix'] == '.md':
            stale_active.append({
                'path': f['rel'],
                'age_days': age_days,
                'modified': datetime.fromtimestamp(f['mtime'], tz=timezone.utc).strftime('%Y-%m-%d'),
            })

    inbox_backlog.sort(key=lambda x: -x['age_days'])
    stale_active.sort(key=lambda x: -x['age_days'])
    return {
        'inbox_days_threshold': inbox_days,
        'inbox_backlog_total': len(inbox_backlog),
        'inbox_backlog_top': inbox_backlog[:20],
        'stale_days_threshold': stale_days,
        'stale_markdown_total': len(stale_active),
        'stale_markdown_top': stale_active[:15],
    }


def check_duplicates(files_info, skill_names):
    by_size = defaultdict(list)
    by_name = defaultdict(list)
    for f in files_info:
        if f['in_archive'] or f['size'] == 0:
            continue
        # 多端主规则文件（AGENTS.md / CLAUDE.md / GEMINI.md）本就应该内容一致，不报重复，由 rules 专项检查
        if f['size'] <= 5 * 1024 * 1024 and f['name'] not in PRIMARY_RULE_FILES:
            by_size[f['size']].append(f)
        if f['suffix'] == '.md' and f['name'].lower() not in COMMON_STRUCTURAL_NAMES:
            by_name[f['name'].lower()].append(f['rel'])

    exact_groups = []
    for group in by_size.values():
        if len(group) < 2:
            continue
        hashes = defaultdict(list)
        for item in group:
            try:
                hashes[file_sha256(item['path'])].append(item['rel'])
            except OSError:
                continue
        for paths in hashes.values():
            if len(paths) >= 2:
                exact_groups.append(sorted(paths))

    same_name_groups = [
        sorted(paths) for paths in by_name.values() if len(paths) >= 2
    ]
    return {
        'exact_duplicate_groups_total': len(exact_groups),
        'exact_duplicate_groups': exact_groups[:15],
        'same_name_markdown_groups_total': len(same_name_groups),
        'same_name_markdown_groups': same_name_groups[:15],
        'skills_found_total': len(skill_names),
        'skills_found': dict(sorted(skill_names.items())),
    }


def discover_rule_files(root):
    """发现根目录与一级子分区中的多 Agent 协作说明文件。"""
    found = []
    candidate_bases = [root]
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.is_symlink() and not is_ignored_part(child.name):
            if not is_archive_root_dir((child.name,)):
                candidate_bases.append(child)

    for base in candidate_bases:
        for name in PRIMARY_RULE_FILES + EXTRA_RULE_FILES:
            p = base / name
            if p.exists() and (p.is_file() or p.is_symlink()):
                found.append((base, p))
        cursor_rules_dir = base / '.cursor' / 'rules'
        if cursor_rules_dir.is_dir():
            for p in sorted(cursor_rules_dir.rglob('*')):
                if p.is_file() and p.suffix.lower() in ('.md', '.mdc'):
                    found.append((base, p))
    return found


def looks_like_rel_path(token):
    if not token or len(token) > 160:
        return False
    if any(bad in token for bad in PLACEHOLDER_TOKENS):
        return False
    if '://' in token or token.startswith(('#', '~', '/', '-', '$', '@', 'python', 'bash', 'cd ', 'ls ')):
        return False
    if ' ' in token and not token.endswith(('.md', '.py', '.json', '.yaml', '.yml', '.csv', '.html', '.txt')):
        return False
    return ('/' in token) or token.endswith(('.md', '.py', '.json', '.yaml', '.yml', '.csv', '.html', '.txt'))


def check_rules(root, skill_names):
    discovered = discover_rule_files(root)
    files_report = []
    by_base_primary = defaultdict(list)
    dead_links = []
    oversized = []

    for base, p in discovered:
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        char_len = len(text)
        item = {
            'path': rel,
            'scope': '.' if base == root else base.relative_to(root).as_posix(),
            'chars': char_len,
            'is_symlink': p.is_symlink(),
            'resolved': os.path.realpath(p) if p.is_symlink() else None,
        }
        files_report.append(item)
        if p.name in PRIMARY_RULE_FILES:
            by_base_primary[item['scope']].append((p, rel, normalized_text_hash(p)))
        if p.name in PRIMARY_RULE_FILES and char_len > 5000:
            oversized.append({'path': rel, 'chars': char_len})

        # 扫描规则文件里的相对路径死链
        in_code_block = False
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith('```'):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            if any(w in line for w in HISTORY_LINE_WORDS):
                continue

            candidates = []
            for m in MD_LINK_RE.findall(line):
                target = m.split('#', 1)[0].strip()
                if looks_like_rel_path(target):
                    candidates.append(target)
            for m in BACKTICK_PATH_RE.findall(line):
                target = m.strip().rstrip('.,;:，。；：')
                if looks_like_rel_path(target):
                    candidates.append(target)

            seen_in_line = set()
            for cand in candidates:
                if cand in seen_in_line:
                    continue
                seen_in_line.add(cand)
                # 允许相对当前规则文件目录、所属分区目录或库根目录解析
                exists = (
                    (p.parent / cand).exists()
                    or (base / cand).exists()
                    or (root / cand).exists()
                )
                if not exists:
                    dead_links.append({'rule_file': rel, 'line': lineno, 'target': cand})

    drift_groups = []
    for scope, entries in sorted(by_base_primary.items()):
        if len(entries) < 2:
            continue
        hashes = {h for _, _, h in entries}
        realpaths = {os.path.realpath(p) for p, _, _ in entries}
        if len(hashes) > 1 and len(realpaths) > 1:
            drift_groups.append({
                'scope': scope,
                'files': [rel for _, rel, _ in entries],
                'status': '内容不一致',
            })

    return {
        'rule_files_found_total': len(files_report),
        'rule_files': files_report,
        'multi_agent_drift': drift_groups,
        'oversized_primary_rules': oversized,
        'suspected_dead_links_total': len(dead_links),
        'suspected_dead_links': dead_links[:25],
        'skills_known_in_kb': len(skill_names),
    }


def check_sharing(root, files_info):
    shared_tops = sorted({
        f['parts'][0] for f in files_info
        if len(f['parts']) > 1 and not f['in_archive']
        and any(w in f['parts'][0].lower() for w in SHARED_DIR_WORDS)
    })
    if not shared_tops:
        return {'enabled': False, 'reason': '未检测到共享分区', 'alerts': []}

    alerts = []
    for f in files_info:
        if f['in_archive'] or len(f['parts']) < 2 or f['parts'][0] not in shared_tops:
            continue
        lower_name = f['name'].lower()
        hit_words = [w for w in SENSITIVE_FILENAME_WORDS if w in lower_name]
        if hit_words:
            alerts.append({'path': f['rel'], 'type': '敏感文件名提示', 'matched_words': hit_words})
        if f['suffix'] == '.md' and 0 < f['size'] <= 512 * 1024:
            try:
                text = f['path'].read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            phones = CN_PHONE_RE.findall(text)
            if phones:
                alerts.append({'path': f['rel'], 'type': '疑似未打码手机号', 'count': len(phones)})
            tokens = TOKEN_PREFIX_RE.findall(text)
            if tokens:
                alerts.append({'path': f['rel'], 'type': '疑似凭据格式', 'count': len(tokens)})

    return {
        'enabled': True,
        'shared_dirs': shared_tops,
        'alerts_total': len(alerts),
        'alerts': alerts[:25],
    }


def format_markdown(report):
    lines = [
        f"# 知识库机械扫描摘要（{report['scanned_at']}）",
        '',
        f"- 知识库根目录：`{report['root']}`",
        f"- Obsidian 库：{'是' if report['has_obsidian'] else '否'}",
        '',
    ]
    if 'structure' in report['checks']:
        s = report['checks']['structure']
        lines.extend([
            '## 1. 目录与规模',
            f"- 文件总数：{s['total_files']}（Markdown：{s['markdown_files']}），子目录：{s['total_dirs']}",
            f"- 空文件夹：{s['empty_dirs_total']}｜超深目录：{s['deep_dirs_total']}｜空文件：{len(s['empty_files'])}｜超大 Markdown：{len(s['large_markdown_files'])}",
            '',
        ])
    if 'scattered' in report['checks']:
        sc = report['checks']['scattered']
        lines.extend([
            '## 2. 散落历史与备份',
            f"- 活跃区散落历史／旧版文件夹：{sc['scattered_history_dirs_total']}",
            f"- 疑似备份／副本文件：{sc['suspicious_backup_files_total']}",
            '',
        ])
    if 'stale' in report['checks']:
        st = report['checks']['stale']
        lines.extend([
            '## 3. 积压与陈旧度',
            f"- 入口／待处理区超过 {st['inbox_days_threshold']} 天未动：{st['inbox_backlog_total']}",
            f"- 活跃区超过 {st['stale_days_threshold']} 天未动 Markdown：{st['stale_markdown_total']}（仅供参考）",
            '',
        ])
    if 'duplicates' in report['checks']:
        dp = report['checks']['duplicates']
        lines.extend([
            '## 4. 重复与 Skill 盘点',
            f"- 内容完全相同重复组：{dp['exact_duplicate_groups_total']}｜同名 Markdown 组：{dp['same_name_markdown_groups_total']}｜发现 Skill：{dp['skills_found_total']}",
            '',
        ])
    if 'rules' in report['checks']:
        rl = report['checks']['rules']
        lines.extend([
            '## 5. 多 Agent 协作说明',
            f"- 发现规则文件：{rl['rule_files_found_total']}｜多端不同步：{len(rl['multi_agent_drift'])}｜超长主规则：{len(rl['oversized_primary_rules'])}｜疑似死链：{rl['suspected_dead_links_total']}",
            '',
        ])
    if 'sharing' in report['checks']:
        sh = report['checks']['sharing']
        status = f"共享目录 {sh['shared_dirs']}，提醒 {sh['alerts_total']} 条" if sh['enabled'] else sh['reason']
        lines.extend([
            '## 6. 共享区抽检',
            f"- {status}",
            '',
        ])
    return '\n'.join(lines)


def scan_kb(root, only=None, stale_days=90, inbox_days=30, max_depth=4, large_kb=100):
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f'知识库目录不存在：{root}')

    selected = tuple(only) if only else ALL_CHECKS
    unknown = [c for c in selected if c not in ALL_CHECKS]
    if unknown:
        raise ValueError(f'未知检查项：{unknown}，可选：{ALL_CHECKS}')

    dirs_info, files_info, skill_names, has_obsidian = walk_kb(root)
    now_ts = datetime.now(tz=timezone.utc).timestamp()

    checks = {}
    if 'structure' in selected:
        checks['structure'] = check_structure(dirs_info, files_info, max_depth=max_depth, large_kb=large_kb)
    if 'scattered' in selected:
        checks['scattered'] = check_scattered(dirs_info, files_info)
    if 'stale' in selected:
        checks['stale'] = check_stale(files_info, now_ts=now_ts, stale_days=stale_days, inbox_days=inbox_days)
    if 'duplicates' in selected:
        checks['duplicates'] = check_duplicates(files_info, skill_names)
    if 'rules' in selected:
        checks['rules'] = check_rules(root, skill_names)
    if 'sharing' in selected:
        checks['sharing'] = check_sharing(root, files_info)

    return {
        'root': str(root),
        'scanned_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'has_obsidian': has_obsidian,
        'selected_checks': list(selected),
        'checks': checks,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='知识库体检通用机械扫描器（只读）')
    parser.add_argument('root', nargs='?', default='.', help='知识库根目录（默认当前目录）')
    parser.add_argument('--only', default='', help='只跑指定检查项，逗号分隔：structure,scattered,stale,duplicates,rules,sharing')
    parser.add_argument('--stale-days', type=int, default=90, help='活跃区陈旧提醒阈值（天），默认 90')
    parser.add_argument('--inbox-days', type=int, default=30, help='收件箱／待处理积压阈值（天），默认 30')
    parser.add_argument('--max-depth', type=int, default=4, help='目录超深阈值，默认 4')
    parser.add_argument('--large-kb', type=int, default=100, help='超大 Markdown 提醒阈值（KB），默认 100')
    parser.add_argument('--format', choices=('json', 'markdown'), default='json', help='输出格式，默认 json')
    args = parser.parse_args(argv)

    only = [item.strip() for item in args.only.split(',') if item.strip()] or None
    try:
        report = scan_kb(
            args.root,
            only=only,
            stale_days=args.stale_days,
            inbox_days=args.inbox_days,
            max_depth=args.max_depth,
            large_kb=args.large_kb,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f'错误：{exc}', file=sys.stderr)
        return 1

    if args.format == 'markdown':
        print(format_markdown(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
