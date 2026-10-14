#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""将定位 JSON 生成无外部依赖的 HTML，可选输出同源 Markdown 核心卡。"""

import argparse
from datetime import date
import html
import json
import os
from pathlib import Path
import re
import sys


ASSETS = Path(__file__).resolve().parent.parent / 'assets'


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
        raise ReportError(f'{path} 必须是非空文字；未知事项请说明影响')
    if any(ord(char) < 32 and char not in '\n\r\t' for char in value):
        raise ReportError(f'{path} 含不可显示的控制字符')
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ReportError(f'{path} 含无效的 Unicode 字符')


def texts(obj, fields, path):
    for field in fields:
        text(obj[field], f'{path}.{field}')


def array(value, path, minimum=0):
    if not isinstance(value, list) or len(value) < minimum:
        raise ReportError(f'{path} 必须是列表，至少 {minimum} 项')
    return value


def text_array(value, path, minimum=0):
    for index, item in enumerate(array(value, path, minimum)):
        text(item, f'{path}[{index}]')


def minutes(value, path, allow_zero=False):
    if value is not None and (type(value) is not int or value < (0 if allow_zero else 1)):
        raise ReportError(f'{path} 必须是{"非负" if allow_zero else "正"}整数分钟或 null')


def validate(data):
    """只验证交付所需字段、类型和预算，不把主观定位判断转换成评分。"""
    root_fields = ('report_title', 'subject', 'recommendation', 'positioning', 'evidence',
                   'pillars', 'homepage', 'resources', 'stages', 'review', 'unknowns')
    record(data, 'report', root_fields, ('report_date', 'case_notice'))
    texts(data, ('report_title', 'subject'), 'report')
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

    rec_fields = ('direction', 'reason', 'first_step', 'tradeoffs', 'judgments')
    rec = record(data['recommendation'], 'recommendation', rec_fields)
    texts(rec, ('direction', 'reason', 'first_step'), 'recommendation')
    text_array(rec['tradeoffs'], 'recommendation.tradeoffs')
    for index, item in enumerate(array(rec['judgments'], 'recommendation.judgments')):
        path = f'recommendation.judgments[{index}]'
        record(item, path, ('label', 'judgment', 'basis'))
        texts(item, ('label', 'judgment', 'basis'), path)

    position_fields = ('one_liner', 'reader', 'reader_problem', 'value', 'basis', 'goal',
                       'platform', 'format', 'voice', 'keywords', 'boundaries')
    position = record(data['positioning'], 'positioning', position_fields)
    texts(position, position_fields[:-2], 'positioning')
    text_array(position['keywords'], 'positioning.keywords')
    text_array(position['boundaries'], 'positioning.boundaries')

    for index, item in enumerate(array(data['evidence'], 'evidence')):
        path = f'evidence[{index}]'
        record(item, path, ('fact', 'source', 'impact'))
        texts(item, ('fact', 'source', 'impact'), path)

    for index, item in enumerate(array(data['pillars'], 'pillars')):
        path = f'pillars[{index}]'
        fields = ('name', 'reader_question', 'purpose', 'format', 'topics')
        record(item, path, fields)
        texts(item, fields[:-1], path)
        for topic_index, topic in enumerate(array(item['topics'], f'{path}.topics', 1)):
            topic_path = f'{path}.topics[{topic_index}]'
            record(topic, topic_path, ('title', 'materials', 'status'))
            texts(topic, ('title', 'materials', 'status'), topic_path)
            if topic['status'] not in ('可制作', '待准备'):
                raise ReportError(f'{topic_path}.status 只能是「可制作」或「待准备」')

    homepage = record(data['homepage'], 'homepage', ('names', 'bio', 'avatar', 'cover'))
    text_array(homepage['names'], 'homepage.names', 1)
    texts(homepage, ('bio', 'avatar', 'cover'), 'homepage')
    resources = record(data['resources'], 'resources', ('weekly_minutes', 'notes', 'materials', 'gaps'))
    minutes(resources['weekly_minutes'], 'resources.weekly_minutes', allow_zero=True)
    text(resources['notes'], 'resources.notes')
    text_array(resources['gaps'], 'resources.gaps')
    for index, item in enumerate(array(resources['materials'], 'resources.materials')):
        path = f'resources.materials[{index}]'
        record(item, path, ('name', 'ownership', 'public_scope'))
        texts(item, ('name', 'ownership', 'public_scope'), path)

    weekly = resources['weekly_minutes']
    stages = array(data['stages'], 'stages', 0 if weekly == 0 else 1)
    if weekly is None and len(stages) > 1:
        raise ReportError('每周时间未知：只安排一轮样稿，实测后再排后续周')
    for index, stage in enumerate(stages):
        path = f'stages[{index}]'
        record(stage, path, ('name', 'focus', 'tasks', 'observe', 'decision'))
        texts(stage, ('name', 'focus', 'observe', 'decision'), path)
        total = 0
        for task_index, task in enumerate(array(stage['tasks'], f'{path}.tasks', 1)):
            task_path = f'{path}.tasks[{task_index}]'
            fields = ('title', 'materials', 'action', 'deliverable', 'estimated_minutes')
            record(task, task_path, fields)
            texts(task, fields[:-1], task_path)
            minutes(task['estimated_minutes'], f'{task_path}.estimated_minutes')
            if task['estimated_minutes'] is None:
                if weekly is not None:
                    raise ReportError(f'{task_path} 缺预计耗时，无法验证已知周预算')
            else:
                total += task['estimated_minutes']
        if weekly is not None and total > weekly:
            raise ReportError(f'{path} 预计 {total} 分钟，超过每周 {weekly} 分钟预算')

    for index, item in enumerate(array(data['review'], 'review')):
        path = f'review[{index}]'
        record(item, path, ('signal', 'interpretation', 'action'))
        texts(item, ('signal', 'interpretation', 'action'), path)
    for index, item in enumerate(array(data['unknowns'], 'unknowns')):
        path = f'unknowns[{index}]'
        record(item, path, ('item', 'impact', 'check'))
        texts(item, ('item', 'impact', 'check'), path)
    return data


def escape(value):
    return html.escape(str(value), quote=True)


def paragraph(value, css=''):
    return f'<p class="{css}">{escape(value)}</p>'


def listing(values, empty='本次未列额外事项。'):
    if not values:
        return paragraph(empty, 'muted')
    return '<ul>' + ''.join(f'<li>{escape(value)}</li>' for value in values) + '</ul>'


def facts(items):
    return '<dl class="facts">' + ''.join(
        f'<div><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>'
        for label, value in items) + '</dl>'


def card(title, body, css=''):
    return f'<article class="card {css}"><h3>{escape(title)}</h3>{body}</article>'


def markdown_text(value):
    # 核心卡可在 Markdown 阅读器使用：保留文字，避免用户字段成为 HTML 或语法。
    value = html.escape(str(value), quote=False)
    return re.sub(r'([\\`*_{}\[\]()#+.!|~\-])', r'\\\1', value).replace('\n', '\n  ')


def core_card(data):
    p = data['positioning']
    lines = [f'# {markdown_text(data["subject"])}｜核心定位卡', '']
    if data.get('case_notice'):
        lines.extend([markdown_text(data['case_notice']), ''])
    fields = [('推荐方向', data['recommendation']['direction']), ('一句话定位', p['one_liner']),
              ('目标读者及处境', p['reader']), ('解决的问题', p['reader_problem']),
              ('内容价值', p['value']), ('本人依据', p['basis']), ('内容目标', p['goal']),
              ('主平台', p['platform']), ('呈现形式', p['format']), ('说话方式', p['voice'])]
    lines.extend(f'- {label}：{markdown_text(value)}' for label, value in fields)
    weekly = data['resources']['weekly_minutes']
    budget = f'每周 {weekly} 分钟' if weekly is not None else '每周时间未提供；先做一份样稿，耗时实测后再定频率'
    lines.extend([f'- 时间资源：{budget}；{markdown_text(data["resources"]["notes"])}', '', '## 内容支柱'])
    for pillar in data['pillars']:
        lines.append(f'- {markdown_text(pillar["name"])}：{markdown_text(pillar["reader_question"])}')
        for topic in pillar['topics']:
            lines.append(f'  - {markdown_text(topic["title"])}｜{markdown_text(topic["status"])}｜材料：{markdown_text(topic["materials"])}')
    for label, values in [('搜索关键词', p['keywords']), ('禁谈与表达边界', p['boundaries'])]:
        lines.extend(['', f'## {label}'])
        lines.extend(f'- {markdown_text(value)}' for value in values)
        if not values:
            lines.append('本次未提供。')
    lines.extend(['', '## 可用素材与公开范围'])
    for item in data['resources']['materials']:
        lines.append(f'- {markdown_text(item["name"])}｜{markdown_text(item["ownership"])}｜{markdown_text(item["public_scope"])}')
    if not data['resources']['materials']:
        lines.append('本次未提供材料。')
    lines.extend(['', '## 事实与证据'])
    for item in data['evidence']:
        lines.append(f'- {markdown_text(item["fact"])}｜来源：{markdown_text(item["source"])}｜影响：{markdown_text(item["impact"])}')
    if not data['evidence']:
        lines.append('暂无可引用事实，定位中的建议保持暂定。')
    lines.extend(['', '## 首轮动作', markdown_text(data['recommendation']['first_step'])])
    for stage in data['stages']:
        lines.append(f'- {markdown_text(stage["name"])}：{markdown_text(stage["focus"])}')
        for task in stage['tasks']:
            timing = '预计耗时待实测' if task['estimated_minutes'] is None else f'预计 {task["estimated_minutes"]} 分钟'
            lines.append(f'  - {markdown_text(task["title"])}｜{timing}｜产物：{markdown_text(task["deliverable"])}')
        lines.append(f'  - 观察：{markdown_text(stage["observe"])}；调整：{markdown_text(stage["decision"])}')
    lines.extend(['', '## 待验证与素材缺口'])
    lines.extend(f'- {markdown_text(item)}' for item in data['resources']['gaps'])
    lines.extend(f'- {markdown_text(item["item"])}｜影响：{markdown_text(item["impact"])}｜核对动作：{markdown_text(item["check"])}'
                 for item in data['unknowns'])
    if not data['unknowns'] and not data['resources']['gaps']:
        lines.append('本次未列额外待验证项。')
    lines.extend(['', '## 报告署名', '这是叁斤给出的账号定位方案。',
                  '叁斤｜公众号：叁斤｜微信：Msan54',
                  '由叁斤提供的 SJskill 基于当前资料生成。',
                  '本节是套件署名，不属于用户身份、主页文案或可用创作素材。'])
    return '\n'.join(lines) + '\n'


def render(data, template):
    p, rec, resource = data['positioning'], data['recommendation'], data['resources']
    weekly = resource['weekly_minutes']
    flow = [('01', '给谁看', p['reader']), ('02', '解决什么', p['reader_problem']),
            ('03', '提供什么价值', p['value']), ('04', '凭什么由你讲', p['basis']),
            ('05', '在哪里、怎样讲', p['platform'] + ' · ' + p['format']), ('06', '先实现什么', p['goal'])]
    flow_html = ''.join(f'<article class="flow-card"><span class="number">{number}</span><h3>{escape(label)}</h3>{paragraph(value)}</article>'
                        for number, label, value in flow)
    judgments = ''.join(card(item['label'], paragraph(item['judgment']) + paragraph('依据：' + item['basis'], 'muted'))
                        for item in rec['judgments'])
    evidence = ''.join(card(item['fact'], facts([('来源', item['source']), ('对方案的影响', item['impact'])]))
                       for item in data['evidence']) or paragraph('暂无可引用事实，相关判断保持暂定。', 'muted')
    pillars = []
    for index, item in enumerate(data['pillars'], 1):
        topics = ''.join('<li><div class="topic-top"><strong>' + escape(topic['title']) + '</strong><span class="badge">' +
                         escape(topic['status']) + '</span></div>' + paragraph('材料：' + topic['materials'], 'muted') + '</li>'
                         for topic in item['topics'])
        body = paragraph(item['reader_question'], 'lead-small') + facts([('形式', item['format']), ('目的', item['purpose'])])
        body += '<ul class="topic-list">' + topics + '</ul>'
        pillars.append(card(f'{index:02d} · {item["name"]}', body))
    stages = []
    for index, stage in enumerate(data['stages'], 1):
        estimates = [task['estimated_minutes'] for task in stage['tasks']]
        total = sum(value for value in estimates if value is not None)
        if weekly is not None:
            budget_note = f'预计 {total} 分钟 / 周预算 {weekly} 分钟 · 尚未分配 {weekly - total} 分钟'
        elif None in estimates:
            budget_note = (f'已列估算 {total} 分钟，另有耗时待实测；每周预算未提供' if total else
                           '本轮耗时待实测；每周预算未提供，先完成样稿再定频率')
        else:
            budget_note = f'本轮预计 {total} 分钟；每周预算未提供，完成后再定频率'
        tasks = []
        for task in stage['tasks']:
            timing = '耗时待实测' if task['estimated_minutes'] is None else f'预计 {task["estimated_minutes"]} 分钟'
            tasks.append('<li><div class="task-title"><h4>' + escape(task['title']) + '</h4><span class="badge">' +
                         escape(timing) + '</span></div>' + facts([('准备材料', task['materials']), ('具体动作', task['action']),
                                                                 ('交付物', task['deliverable'])]) + '</li>')
        stages.append('<article class="stage"><div class="stage-head"><span class="number">' + f'{index:02d}' +
                      '</span><div><h3>' + escape(stage['name']) + '</h3>' + paragraph(budget_note, 'budget') + '</div></div>' +
                      paragraph(stage['focus'], 'lead-small') + '<ol class="task-list">' + ''.join(tasks) + '</ol>' +
                      '<div class="stage-review">' + facts([('何时、观察什么', stage['observe']), ('怎样决定下一步', stage['decision'])]) + '</div></article>')
    materials = ''.join(card(item['name'], facts([('归属', item['ownership']), ('公开范围', item['public_scope'])]))
                        for item in resource['materials']) or paragraph('本次未提供素材，先按缺口准备。', 'muted')
    review = ''.join(card(item['signal'], facts([('如何理解', item['interpretation']), ('对应动作', item['action'])]))
                     for item in data['review']) or paragraph('本次未提供反馈判断；拿到可观察反馈后再更新。', 'muted')
    unknowns = ''.join(card(item['item'], facts([('影响', item['impact']), ('最小核对动作', item['check'])]))
                       for item in data['unknowns']) or paragraph('本次未列额外待确认事项。', 'muted')
    homepage = data['homepage']
    home_html = card('昵称候选', listing(homepage['names'])) + card('可用简介', paragraph(homepage['bio'], 'bio'))
    home_html += card('头像与封面', facts([('头像', homepage['avatar']), ('封面', homepage['cover']), ('说话方式', p['voice'])]))
    core = core_card(data)
    values = {
        'TITLE': escape(data['report_title']), 'SUBJECT': escape(data['subject']),
        'DATE': escape(data.get('report_date', '报告日期未提供')),
        'CASE_NOTICE': paragraph(data['case_notice'], 'case-notice') if data.get('case_notice') else '',
        'DIRECTION': escape(rec['direction']), 'REASON': escape(rec['reason']),
        'FIRST_STEP': escape(rec['first_step']), 'ONE_LINER': escape(p['one_liner']),
        'FLOW': flow_html, 'JUDGMENTS': judgments, 'TRADEOFFS': listing(rec['tradeoffs']),
        'EVIDENCE': evidence, 'PILLARS': ''.join(pillars) or paragraph('现有材料尚不足以展开栏目，先完成一份样稿。', 'muted'),
        'KEYWORDS': listing(p['keywords'], '暂未提供搜索词，不虚构搜索量。'),
        'HOMEPAGE': home_html, 'STAGES': ''.join(stages) or paragraph('本周可投入时间为零，暂不安排制作任务。', 'muted'),
        'BUDGET': escape(f'每周 {weekly} 分钟' if weekly is not None else '每周时间待确认'),
        'RESOURCE_NOTES': escape(resource['notes']), 'MATERIALS': materials, 'GAPS': listing(resource['gaps']),
        'REVIEW': review, 'UNKNOWNS': unknowns, 'BOUNDARIES': listing(p['boundaries']), 'CORE_CARD': escape(core),
    }
    found = set(re.findall(r'@@([A-Z_]+)@@', template))
    if found != values.keys():
        raise ReportError('HTML 模板字段不完整或不受支持，请使用随技能分发的模板')
    # 只对模板做一遍替换，用户文本中的占位符不会被再次解释。
    output = re.sub(r'@@([A-Z_]+)@@', lambda match: values[match[1]], template)
    return output, core


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
    parser = argparse.ArgumentParser(description='从同一份 JSON 生成离线定位 HTML 和可选 Markdown 核心卡；不覆盖文件。')
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
        data = json.loads(source.read_text(encoding='utf-8'), object_pairs_hook=unique_object, parse_constant=invalid_constant)
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
