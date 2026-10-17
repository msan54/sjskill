#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""将定位方案 JSON 生成无外部依赖的 HTML，可选输出同源 Markdown 核心定位卡。"""

import argparse
from datetime import date
import html
import json
import os
from pathlib import Path
import re
import sys


ASSETS = Path(__file__).resolve().parent.parent / 'assets'
STATUSES = ('完整方案', '初稿')
FORMULA_FIELDS = (('核心价值', 'value'), ('目标用户', 'audience'), ('关键人设', 'persona'),
                  ('内容类型', 'format'), ('核心风格', 'style'))
CORE_FIELDS = ('one_liner', *(field for _, field in FORMULA_FIELDS), 'platform', 'goal')
AUDIENCE_FIELDS = (('基本信息', 'basic'), ('经济水平', 'economy'), ('生活状态', 'life'), ('内心世界', 'inner'))
ROLES = ('引流款', '信任款', '人设款')
SCORE_NAMES = ('门槛', '供需', '离钱距离', '执行匹配')
LEVELS = ('高', '中偏高', '中', '中偏低', '低')
ROOT_FIELDS = ('report_title', 'subject', 'status', 'verdict', 'core', 'audience', 'directions',
               'style_guide', 'growth', 'monetization', 'launch', 'score', 'market_keywords')
OPTIONAL_FIELDS = ('report_date', 'case_notice', 'assumptions')
ATTRIBUTION = ('这是叁斤给出的账号定位方案。本方案由叁斤提供的 SJskill 基于当前资料生成。',
               '叁斤｜公众号：叁斤｜微信：Msan54')


class ReportError(ValueError):
    pass


def record(value, path, required, optional=()):
    if not isinstance(value, dict):
        raise ReportError(f'{path} 必须是对象')
    missing = set(required) - value.keys()
    if missing:
        raise ReportError(f'{path} 缺少字段：{", ".join(sorted(missing))}')
    if value.keys() - set(required) - set(optional):
        raise ReportError(f'{path} 含未支持的字段，请对照数据说明')
    return value


def text(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ReportError(f'{path} 必须是非空文字')
    if any(ord(char) < 32 and char not in '\n\r\t' for char in value):
        raise ReportError(f'{path} 含不可显示的控制字符')
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ReportError(f'{path} 含无效的 Unicode 字符')


def texts(obj, fields, path):
    for field in fields:
        text(obj[field], f'{path}.{field}')


def array(value, path, minimum=0, maximum=None):
    if not isinstance(value, list) or len(value) < minimum or (maximum is not None and len(value) > maximum):
        span = f'{minimum}—{maximum}' if maximum is not None else f'至少 {minimum}'
        raise ReportError(f'{path} 必须是列表，{span} 项')
    return value


def text_array(value, path, minimum=0, maximum=None):
    for index, item in enumerate(array(value, path, minimum, maximum)):
        text(item, f'{path}[{index}]')


def validate(data):
    """检查交付所需的字段、类型与数量；方案写得好不好由模板里的质量自检负责。"""
    record(data, 'report', ROOT_FIELDS, OPTIONAL_FIELDS)
    texts(data, ('report_title', 'subject', 'status', 'verdict'), 'report')
    if data['status'] not in STATUSES:
        raise ReportError('status 只能是「完整方案」或「初稿」')
    complete = data['status'] == '完整方案'
    if 'case_notice' in data:
        text(data['case_notice'], 'case_notice')
    if 'report_date' in data:
        text(data['report_date'], 'report_date')
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', data['report_date']):
                raise ValueError
            date.fromisoformat(data['report_date'])
        except ValueError:
            raise ReportError('report_date 必须是 YYYY-MM-DD 日期') from None
    if 'assumptions' in data:
        text_array(data['assumptions'], 'assumptions')
    if not complete and not data.get('assumptions'):
        raise ReportError('初稿必须用 assumptions 列出哪几处是推测的')

    core = record(data['core'], 'core', (*CORE_FIELDS, 'red_lines'))
    texts(core, CORE_FIELDS, 'core')
    text_array(core['red_lines'], 'core.red_lines')

    audience = record(data['audience'], 'audience', (*(field for _, field in AUDIENCE_FIELDS), 'attention'))
    texts(audience, audience.keys(), 'audience')

    directions = array(data['directions'], 'directions', 3 if complete else 1, 5)
    for index, item in enumerate(directions):
        path = f'directions[{index}]'
        record(item, path, ('name', 'role', 'share', 'why', 'topics'))
        texts(item, ('name', 'role', 'share', 'why'), path)
        if item['role'] not in ROLES:
            raise ReportError(f'{path}.role 只能是「引流款」「信任款」或「人设款」')
        text_array(item['topics'], f'{path}.topics', 2 if complete else 1)

    style = record(data['style_guide'], 'style_guide', ('names', 'name_note', 'bio', 'avatar', 'cover', 'voice_rules'))
    text_array(style['names'], 'style_guide.names', 2 if complete else 1, 3)
    texts(style, ('name_note', 'bio', 'avatar', 'cover'), 'style_guide')
    text_array(style['voice_rules'], 'style_guide.voice_rules', 3 if complete else 1)

    for index, item in enumerate(array(data['growth'], 'growth', 3 if complete else 1)):
        path = f'growth[{index}]'
        record(item, path, ('stage', 'plan'))
        texts(item, ('stage', 'plan'), path)

    money = record(data['monetization'], 'monetization', ('short_term', 'long_term', 'warnings'))
    texts(money, ('short_term', 'long_term'), 'monetization')
    text_array(money['warnings'], 'monetization.warnings', 1)

    launch = record(data['launch'], 'launch', ('week1', 'weeks2_4', 'benchmark', 'reminder'))
    for field in ('week1', 'weeks2_4', 'benchmark'):
        text_array(launch[field], f'launch.{field}', 1)
    text(launch['reminder'], 'launch.reminder')

    score = record(data['score'], 'score', ('items', 'verdict'))
    text(score['verdict'], 'score.verdict')
    items = array(score['items'], 'score.items', 4, 4)
    for index, (item, name) in enumerate(zip(items, SCORE_NAMES)):
        path = f'score.items[{index}]'
        record(item, path, ('name', 'level', 'reason', 'line'))
        texts(item, ('name', 'level', 'reason', 'line'), path)
        if item['name'] != name:
            raise ReportError(f'{path}.name 应为「{name}」，四项按门槛、供需、离钱距离、执行匹配排列')
        if item['level'] not in LEVELS:
            raise ReportError(f'{path}.level 只能是「高」「中偏高」「中」「中偏低」「低」，用档位不用分数')

    text_array(data['market_keywords'], 'market_keywords', 3 if complete else 1, 5)
    return data


def escape(value):
    return html.escape(str(value), quote=True)


def paragraph(value, css=''):
    attribute = f' class="{css}"' if css else ''
    return f'<p{attribute}>{escape(value)}</p>'


def listing(values, css='', empty='无'):
    if not values:
        return paragraph(empty, 'muted')
    attribute = f' class="{css}"' if css else ''
    return f'<ul{attribute}>' + ''.join(f'<li>{escape(value)}</li>' for value in values) + '</ul>'


def facts(items):
    return '<dl class="facts">' + ''.join(
        f'<div><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>' for label, value in items) + '</dl>'


def card(title, body, css='', badge='', element_id=''):
    classes = 'card' + (f' {css}' if css else '')
    identifier = f' id="{element_id}"' if element_id else ''
    tag = f'<span class="badge">{escape(badge)}</span>' if badge else ''
    return f'<article class="{classes}"{identifier}><h3><span>{escape(title)}</span>{tag}</h3>{body}</article>'


def plain(value):
    # 核心卡是贴给 AI 的纯文本：只转义尖括号，防止在 Markdown 阅读器里变成标签；换行缩进保持条目归属。
    return str(value).replace('<', '&lt;').replace('>', '&gt;').replace('\n', '\n  ')


def core_card(data):
    core = data['core']
    lines = [f'# {plain(data["subject"])}｜核心定位卡', '']
    if data.get('case_notice'):
        lines.extend([plain(data['case_notice']), ''])
    if data['status'] != '完整方案':
        guesses = '；'.join(plain(item) for item in data['assumptions'])
        lines.extend([f'方案状态：初稿。推测项：{guesses}', ''])
    lines.append(f'- 一句话定位：{plain(core["one_liner"])}')
    lines.extend(f'- {label}：{plain(core[field])}' for label, field in FORMULA_FIELDS)
    lines.append(f'- 主平台：{plain(core["platform"])}')
    lines.append(f'- 账号目标：{plain(core["goal"])}')
    directions = '／'.join(f'{plain(item["name"])}（{plain(item["role"])}，{plain(item["share"])}）'
                          for item in data['directions'])
    lines.append(f'- 内容方向：{directions}')
    red_lines = '；'.join(plain(item) for item in core['red_lines']) or '用户没有提出'
    lines.append(f'- 红线：{red_lines}')
    lines.extend(['', '用法：以后让任何 AI 写笔记、想选题、改简介，先把这张卡贴给它，再提需求。', '',
                  '---', f'来源：{ATTRIBUTION[0]}', ATTRIBUTION[1],
                  '这一段只说明方案来源，不写进你的昵称、简介、选题和笔记。'])
    return '\n'.join(lines) + '\n'


def render(data, template):
    core = data['core']
    formula = ''.join(
        f'<article class="flow-card"><span class="number">{index:02d}</span><h3>{escape(label)}</h3>{paragraph(core[field])}</article>'
        for index, (label, field) in enumerate(FORMULA_FIELDS, 1))
    core_extra = facts([('主平台', core['platform']), ('账号目标', core['goal']),
                        ('红线', '；'.join(core['red_lines']) or '用户没有提出')])
    assumptions = ''
    if data.get('assumptions'):
        assumptions = ('<div id="assumptions" class="callout"><h2>这几处是推测的，补上会改方案</h2>' +
                       listing(data['assumptions']) + '</div>')
    audience = ''.join(card(label, paragraph(data['audience'][field])) for label, field in AUDIENCE_FIELDS)
    audience += card('她刷到什么会停下来', paragraph(data['audience']['attention']), 'highlight')
    directions = ''.join(
        card(f'{index:02d} · {item["name"]}', paragraph(item['why'], 'muted') + listing(item['topics'], 'topics'),
             badge=f'{item["role"]} · {item["share"]}')
        for index, item in enumerate(data['directions'], 1))
    style = data['style_guide']
    names = listing(style['names']) + paragraph(style['name_note'], 'muted')
    style_html = card('昵称（第一个是首选）', names)
    style_html += card('简介：可以直接复制', paragraph(style['bio'], 'bio'), element_id='bio')
    style_html += card('头像与封面', facts([('头像', style['avatar']), ('封面与主页', style['cover'])]))
    style_html += card('说话规矩', listing(style['voice_rules']))
    growth = ''.join(card(item['stage'], paragraph(item['plan'])) for item in data['growth'])
    money = data['monetization']
    money_html = card('短期：起号期先做的铺垫', paragraph(money['short_term']))
    money_html += card('中长期：主路径和备选', paragraph(money['long_term']))
    money_html += card('变现红线', listing(money['warnings']), 'highlight')
    launch = data['launch']
    launch_html = card('第 1 周', listing(launch['week1']))
    launch_html += card('第 2—4 周', listing(launch['weeks2_4']))
    launch_html += card('找对标的标准', listing(launch['benchmark']))
    launch_html += card('最后一句', paragraph(launch['reminder']), 'highlight')
    score = ''.join(
        '<article class="card"><h3><span>' + escape(item['name']) + '</span><span class="level">' +
        escape(item['level']) + '</span></h3>' + paragraph(item['reason']) +
        paragraph(item['line'], 'score-line') + '</article>'
        for item in data['score']['items'])
    keywords = ''.join(f'<li>{escape(word)}</li>' for word in data['market_keywords'])
    core_text = core_card(data)
    values = {
        'TITLE': escape(data['report_title']), 'SUBJECT': escape(data['subject']),
        'DATE': escape(data.get('report_date', '报告日期未提供')), 'STATUS': escape(data['status']),
        'CASE_NOTICE': paragraph(data['case_notice'], 'case-notice') if data.get('case_notice') else '',
        'ASSUMPTIONS': assumptions, 'VERDICT': paragraph(data['verdict']),
        'ONE_LINER': escape(core['one_liner']), 'FORMULA': formula, 'CORE_EXTRA': core_extra,
        'CORE_CARD': escape(core_text), 'AUDIENCE': audience, 'DIRECTIONS': directions,
        'STYLE_GUIDE': style_html, 'GROWTH': growth, 'MONETIZATION': money_html, 'LAUNCH': launch_html,
        'SCORE': score, 'SCORE_VERDICT': '<strong>总判词</strong>' + paragraph(data['score']['verdict']),
        'MARKET_KEYWORDS': keywords,
    }
    found = set(re.findall(r'@@([A-Z_]+)@@', template))
    if found != values.keys():
        raise ReportError('HTML 模板字段不完整或不受支持，请使用随技能分发的模板')
    # 只对模板做一遍替换，用户文字里出现的占位符不会被再次解释。
    output = re.sub(r'@@([A-Z_]+)@@', lambda match: values[match[1]], template)
    return output, core_text


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReportError('JSON 含重复字段，请保留唯一值')
        result[key] = value
    return result


def output_path(raw):
    path = Path(raw).expanduser().absolute()
    # 在规范化 .. 之前检查原路径，避免用 link/.. 绕过父目录软链接检测。
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ReportError('输出路径及父目录不能是软链接；请使用真实目录路径')
    path = Path(os.path.abspath(path))
    if path.exists():
        raise ReportError(f'输出已存在，未覆盖：{path}')
    if not path.parent.is_dir():
        raise ReportError(f'输出父目录不存在或不是目录：{path.parent}')
    return path


def write_outputs(outputs):
    """所有内容和路径预检后独占创建；失败仅清理本次创建且未被替换的文件。"""
    opened = []
    try:
        for path, content in outputs:
            stream = path.open('x', encoding='utf-8', newline='\n')
            identity = os.fstat(stream.fileno())
            opened.append((path, stream, identity, content))
        for _, stream, _, content in opened:
            stream.write(content)
            stream.flush()
        for _, stream, _, _ in opened:
            stream.close()
    except (OSError, UnicodeError):
        for path, stream, identity, _ in opened:
            try:
                stream.close()
            except (OSError, UnicodeError):
                pass
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    path.unlink()
            except OSError:
                pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description='从同一份 JSON 生成离线定位 HTML 和可选 Markdown 核心定位卡；不覆盖文件。')
    parser.add_argument('input', help='UTF-8 JSON 数据')
    parser.add_argument('output', help='HTML 输出路径，父目录须已存在')
    parser.add_argument('--markdown', help='可选的 Markdown 核心定位卡输出路径')
    args = parser.parse_args(argv)
    try:
        source = Path(args.input).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ReportError('输入必须是 JSON 文件')
        raw_paths = [Path(args.output).expanduser().absolute()]
        if args.markdown:
            raw_paths.append(Path(args.markdown).expanduser().absolute())
        resolved = [path.resolve() for path in raw_paths]
        if source in resolved:
            raise ReportError('输出不能与输入 JSON 相同')
        if len(set(resolved)) != len(resolved):
            raise ReportError('HTML 与 Markdown 输出不能使用同一路径')
        paths = [output_path(path) for path in raw_paths]

        def invalid_constant(_):
            raise ReportError('JSON 不允许 NaN 或 Infinity')

        data = json.loads(source.read_text(encoding='utf-8'), object_pairs_hook=unique_object,
                          parse_constant=invalid_constant)
        validate(data)
        template = (ASSETS / 'report.html').read_text(encoding='utf-8')
        document, markdown = render(data, template)
        outputs = [(paths[0], document)]
        if args.markdown:
            outputs.append((paths[1], markdown))
        write_outputs(outputs)
        for path, _ in outputs:
            print(f'已生成：{path}')
        return 0
    except json.JSONDecodeError as error:
        print(f'错误：JSON 格式无效，行 {error.lineno}，列 {error.colno}；未生成报告。', file=sys.stderr)
    except (ReportError, OSError, UnicodeError, RuntimeError) as error:
        print(f'错误：{error}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
