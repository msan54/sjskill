"""检查 SJskill 发行目录的结构、引用、依赖和敏感残留，输出可保存的 JSON。"""
import ast
import json
from pathlib import Path
import re
import sys
import yaml

EXPECTED = {'sj', 'sj-positioning', 'sj-knowledge-base', 'sj-topics', 'sj-xhs-viral',
            'sj-xhs-seo', 'sj-recording', 'sj-learning', 'sj-diagnosis', 'sj-data-review',
            'sj-work-review', 'sj-wechat-format'}
DISTRIBUTION_DIRS = ('skills', 'docs', 'licenses', '.claude-plugin', 'hooks')
DISTRIBUTION_FILES = ('README.md', 'CHANGELOG.md', 'LICENSE', '第三方声明.md', 'ROADMAP.md')
REQUIRED_FILES = (*DISTRIBUTION_FILES, 'docs/新手入门.md',
                  'licenses/CC-BY-NC-4.0.txt', '.claude-plugin/plugin.json',
                  '.claude-plugin/marketplace.json',
                  *(f'skills/{name}/{file}' for name in sorted(EXPECTED)
                    for file in ('SKILL.md', 'LICENSE', 'agents/openai.yaml')))


def validate(root):
    root = root.resolve()
    failures = []
    notes = []
    # 仓库同时含开发测试；只检查实际随技能分发的目录与根文档。
    paths = [root / name for name in DISTRIBUTION_FILES if (root / name).exists() or (root / name).is_symlink()]
    for directory in DISTRIBUTION_DIRS:
        base = root / directory
        if base.is_symlink():
            paths.append(base)
        else:
            paths.extend(p for p in base.rglob('*') if p.is_file() or p.is_symlink())
    for name in REQUIRED_FILES:
        if not (root / name).is_file():
            failures.append(name + '：缺少必要发行文件')
    skills = {p.parent.name for p in (root / 'skills').glob('*/SKILL.md')}
    if skills != EXPECTED:
        failures.append('技能目录与约定不一致：' + str(skills ^ EXPECTED))
    for path in paths:
        relative = str(path.relative_to(root))
        if path.is_symlink() or '__pycache__' in path.parts or path.name.startswith('.env') or path.suffix in ('.pyc', '.pyo'):
            failures.append(relative + '：禁止进入分发包的文件类型')
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            failures.append(relative + '：无法读取为 UTF-8 文本，禁止二进制发行文件')
            continue
        if '\x00' in text:
            failures.append(relative + '：禁止二进制发行文件')
            continue
        if not text.strip():
            failures.append(relative + '：空文件')
        patterns = [r'/Users/(?:apple|macbook)/', r'/private/tmp/', r'03-知识原子/',
                    r'05-知识资产/', r'重建索引\.py', r'06-项目库/',
                    r'sk-[A-Za-z0-9_-]{16,}', r'(?i)(?:api[_-]?key|authorization|cookie)\s*[:=]\s*[\"\x27][^\"\x27]{10,}']
        if any(re.search(pattern, text) for pattern in patterns):
            failures.append(relative + '：私人路径、凭据或旧知识库依赖残留')
        if path.suffix == '.md':
            if re.search(r'富贵|梁靠谱|卡兹克|dontbesilent|dbskill|github\.com', text, re.I):
                failures.append(relative + '：原料品牌或外部仓库残留')
            prose = re.sub(r'```.*?```', '', text, flags=re.S)
            prose = re.sub(r'`[^`]*`', '', prose)
            for target in re.findall(r'\]\(([^)]+)\)', prose):
                if '://' in target or target.startswith('#') or '<' in target:
                    continue
                linked = (path.parent / target.split('#')[0]).resolve()
                if not linked.is_relative_to(root) or not linked.exists():
                    failures.append(relative + '：失效相对链接 ' + target)
            # 代码中的具体参考路径也应存在；泛称目录与模板参数不计入。
            for target in re.findall(r'`((?:references|scripts)/[^`]+\.(?:md|py))`', text):
                skill_root = next((p for p in path.parents if (p / 'SKILL.md').is_file()), path.parent)
                if not (skill_root / target).exists():
                    failures.append(relative + '：失效内联引用 ' + target)
        if path.suffix == '.py':
            tree = ast.parse(text, filename=relative)
            dependencies = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    dependencies.update(n.name.split('.')[0] for n in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    dependencies.add(node.module.split('.')[0])
            # 随脚本一同分发的本地模块不属于外部依赖；同样逐文件检查语法和许可。
            local_modules = {p.stem for p in path.parent.glob('*.py')}
            missing = dependencies - sys.stdlib_module_names - local_modules
            if missing:
                failures.append(relative + '：外部 Python 依赖 ' + str(missing))
            if not (path.parent / 'LICENSE').exists():
                failures.append(relative + '：脚本缺少许可证')
        if path.name == 'SKILL.md':
            match = re.match(r'^---\n(.*?)\n---\n', text, re.S)
            meta = yaml.safe_load(match.group(1)) if match else {}
            if meta.get('name') != path.parent.name or not meta.get('description'):
                failures.append(relative + '：技能前言不正确')
            ui_path = path.parent / 'agents/openai.yaml'
            if ui_path.is_file():
                try:
                    ui = yaml.safe_load(ui_path.read_text(encoding='utf-8'))['interface']
                    if not 25 <= len(ui['short_description']) <= 64 or '$' + path.parent.name not in ui['default_prompt']:
                        failures.append(relative + '：UI 元数据不正确')
                except (OSError, UnicodeError, yaml.YAMLError, KeyError, TypeError):
                    failures.append(relative + '：UI 元数据无法解析')
            notes.append({'skill': path.parent.name, 'lines': len(text.splitlines()),
                          'references': len(list((path.parent / 'references').glob('*.md')))})
    plugin_path = root / '.claude-plugin/plugin.json'
    market_path = root / '.claude-plugin/marketplace.json'
    if plugin_path.is_file() and market_path.is_file():
        try:
            plugin = json.loads(plugin_path.read_text(encoding='utf-8'))
            market = json.loads(market_path.read_text(encoding='utf-8'))
            if market['plugins'][0]['name'] != plugin['name']:
                failures.append('插件与市场名称不一致')
        except (OSError, UnicodeError, ValueError, KeyError, TypeError, IndexError):
            failures.append('插件或市场清单无法解析')
    license_path = root / 'licenses/CC-BY-NC-4.0.txt'
    if license_path.is_file():
        try:
            if len(license_path.read_text(encoding='utf-8').splitlines()) < 350:
                failures.append('CC 许可文本不完整')
        except (OSError, UnicodeError):
            failures.append('CC 许可文本无法读取')
    return {'passed': not failures, 'files': len(paths), 'skills': sorted(notes, key=lambda x:x['skill']),
            'failures': failures, 'checks': ['技能前言与UI', '相对引用闭合', '自包含路径',
            '敏感残留', 'Python语法与标准库依赖', '脚本许可', '插件元数据与许可文本']}


if __name__ == '__main__':
    package = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    result = validate(package)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result['passed'] else 1)
