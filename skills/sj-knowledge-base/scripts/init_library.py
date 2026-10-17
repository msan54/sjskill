#!/usr/bin/env python3
"""按叁斤的知识库结构新建缺少的目录和知识库说明，只增不覆盖。仅使用 Python 标准库。"""
import argparse
import json
from pathlib import Path
import sys

DIRECTORIES = (
    '说明书', '产品库', '素材库', '方法论',
    '输入', '输入/录音', '输入/录音/待处理', '输入/录音/已处理', '输入/课程学习', '输入/内容学习',
    '选题库', '待发布', '已发布',
)


def create_library(root, dry_run=False):
    root = Path(root).expanduser().absolute()
    for parent in (root, *root.parents):
        if parent.is_symlink():
            raise ValueError(f'路径含符号链接，请确认真实位置后重试：{parent}')
        if parent.exists() and not parent.is_dir():
            raise ValueError(f'目录位置被文件占用：{parent}')
    root = root.resolve()
    if root == Path(root.anchor):
        raise ValueError('请指定专用子目录，不能把文件系统根目录作为知识库。')
    directories = [root / name for name in DIRECTORIES]
    document = root / '知识库说明.md'
    for path in directories:
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError(f'目录冲突：{path}')
    if document.is_symlink() or (document.exists() and not document.is_file()):
        raise ValueError(f'说明文件冲突：{document}')
    planned = [str(p.relative_to(root)) + '/' for p in directories if not p.exists()]
    if not document.exists():
        planned.append(document.name)
    result = {'root': str(root), 'dry_run': dry_run, 'new': planned,
              'preserved': [str(p.relative_to(root)) for p in [*directories, document] if p.exists()]}
    if dry_run:
        return result
    root.mkdir(parents=True, exist_ok=True)
    for directory in directories:
        directory.mkdir(exist_ok=True)
    if not document.exists():
        template = Path(__file__).resolve().parents[1] / 'references' / '知识库说明模板.md'
        content = template.read_text(encoding='utf-8')
        with document.open('x', encoding='utf-8') as stream:
            stream.write(content)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', help='用户选择的知识库目录')
    parser.add_argument('--dry-run', action='store_true', help='只列出计划，不写文件')
    args = parser.parse_args()
    try:
        result = create_library(args.root, args.dry_run)
    except (OSError, ValueError) as error:
        print(f'未完成：{error}', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
