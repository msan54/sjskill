"""定位报告生成器：通过真实 CLI 验证内容、预算和文件保护。"""
import copy
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[1] / 'skills/sj-positioning'
RENDER = SKILL / 'scripts/render_report.py'
EXAMPLE = SKILL / 'assets/example-report.json'


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.script = False
        self.textarea = False
        self.scripts = []
        self.core = ''
        self.links = []
        self.events = []
        self.tags = []
        self.elements = []
        self.regions = {key: '' for key in ('report-attribution', 'homepage', 'position')}

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        values = dict(attrs)
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.elements.append((tag, values.get('id')))
        for key, value in attrs:
            if key in ('src', 'href'):
                self.links.append(value)
            if key.startswith('on'):
                self.events.append(key)
        if tag == 'script':
            self.scripts.append('')
            self.script = True
        if tag == 'textarea' and values.get('id') == 'core-card':
            self.textarea = True

    def handle_endtag(self, tag):
        for index in range(len(self.elements) - 1, -1, -1):
            if self.elements[index][0] == tag:
                del self.elements[index:]
                break
        if tag == 'script':
            self.script = False
        if tag == 'textarea':
            self.textarea = False

    def handle_data(self, data):
        if self.script:
            self.scripts[-1] += data
        if self.textarea:
            self.core += data
        for _, element_id in self.elements:
            if element_id in self.regions:
                self.regions[element_id] += data


class PositioningReportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='sj-positioning-test-')).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        self.data = json.loads(EXAMPLE.read_text(encoding='utf-8'))
        self.source = self.root / '输入.json'
        self.output = self.root / '报告.html'
        self.markdown = self.root / '定位报告.md'

    def write_input(self, data=None):
        self.source.write_text(json.dumps(self.data if data is None else data, ensure_ascii=True), encoding='utf-8')

    def run_cli(self, output=None, markdown=None):
        args = [sys.executable, '-B', str(RENDER), str(self.source), str(output or self.output)]
        if markdown is not None:
            args.extend(['--markdown', str(markdown)])
        return subprocess.run(args, text=True, capture_output=True)

    def assert_rejected(self, expected=None, markdown=True):
        result = self.run_cli(markdown=self.markdown if markdown else None)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        if expected:
            self.assertIn(expected, result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.markdown.exists())
        self.assertNotIn('Traceback', result.stderr)
        return result

    def test_example_generates_html_and_same_source_core_card(self):
        self.write_input()
        before = self.source.read_bytes()
        result = self.run_cli(markdown=self.markdown)
        self.assertEqual(result.returncode, 0, result.stderr)
        page = self.output.read_text(encoding='utf-8')
        parsed = PageParser()
        parsed.feed(page)
        self.assertEqual(parsed.core, self.markdown.read_text(encoding='utf-8'))
        self.assertEqual(self.source.read_bytes(), before)
        for value in [self.data['recommendation']['direction'], self.data['recommendation']['first_step'],
                      '预计 150 分钟 / 周预算 180 分钟', '预计 155 分钟 / 周预算 180 分钟', '虚构教学案例']:
            self.assertIn(value, page)
        self.assertIn('六年修衣经验', parsed.core)
        self.assertEqual(len(parsed.scripts), 1)
        self.assertTrue(all(link.startswith('#') for link in parsed.links))
        self.assertEqual(parsed.events, [])
        self.assertNotIn('@@TITLE@@', page)

    def test_all_user_strings_are_text_not_html_or_javascript(self):
        attack = '</textarea><script>alert("test")</script><img src="https://invalid.test/x" onerror="alert(1)"><style>body{display:none}</style>&@@TITLE@@'
        def contaminate(value):
            if isinstance(value, dict):
                return {key: (item if key in ('report_date', 'status') else contaminate(item)) for key, item in value.items()}
            if isinstance(value, list):
                return [contaminate(item) for item in value]
            return value + attack if isinstance(value, str) else value
        self.write_input(contaminate(self.data))
        result = self.run_cli(markdown=self.markdown)
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = PageParser()
        parsed.feed(self.output.read_text(encoding='utf-8'))
        template = PageParser()
        template.feed((SKILL / 'assets/report.html').read_text(encoding='utf-8'))
        self.assertEqual(parsed.scripts, template.scripts)
        self.assertEqual(parsed.tags.count('textarea'), 1)
        self.assertEqual(parsed.tags.count('style'), 1)
        self.assertNotIn('img', parsed.tags)
        self.assertEqual(parsed.events, [])
        self.assertTrue(all(link.startswith('#') for link in parsed.links))
        self.assertNotIn('<script>', self.markdown.read_text(encoding='utf-8'))
        self.assertEqual(parsed.core, self.markdown.read_text(encoding='utf-8'))

    def test_report_attribution_is_visible_and_separate_from_user_identity(self):
        self.data['subject'] = '小林的修衣账号'
        self.data['homepage']['bio'] = '小林｜用自己的五组修衣照片讲解衣物修改。'
        self.write_input()
        before = self.source.read_bytes()
        result = self.run_cli(markdown=self.markdown)
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = PageParser()
        parsed.feed(self.output.read_text(encoding='utf-8'))
        for value in ['这是叁斤给出的账号定位方案', '叁斤｜公众号：叁斤｜微信：Msan54',
                      '由叁斤提供的 SJskill 基于当前资料生成']:
            self.assertIn(value, parsed.regions['report-attribution'])
        self.assertIn(self.data['homepage']['bio'], parsed.regions['homepage'])
        self.assertIn(self.data['positioning']['one_liner'], parsed.regions['position'])
        for region in ['homepage', 'position']:
            self.assertNotIn('Msan54', parsed.regions[region])
            self.assertNotIn('叁斤', parsed.regions[region])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(parsed.core, self.markdown.read_text(encoding='utf-8'))
        content, attribution = parsed.core.split('## 报告署名\n')
        self.assertNotIn('Msan54', content)
        self.assertNotIn('叁斤', content)
        self.assertIn('叁斤｜公众号：叁斤｜微信：Msan54', attribution)
        self.assertIn('不属于用户身份、主页文案或可用创作素材', attribution)
        self.assertTrue(all(link.startswith('#') for link in parsed.links))

    def test_manual_template_has_attribution_without_user_data_fields(self):
        parsed = PageParser()
        template = (SKILL / 'assets/report.html').read_text(encoding='utf-8')
        parsed.feed(template)
        self.assertIn('这是叁斤给出的账号定位方案', parsed.regions['report-attribution'])
        self.assertIn('叁斤｜公众号：叁斤｜微信：Msan54', parsed.regions['report-attribution'])
        self.assertNotIn('@@', parsed.regions['report-attribution'])

    def test_invalid_json_and_utf8_leave_no_outputs(self):
        for raw in [b'{"report_title":', b'\xff\xfe']:
            with self.subTest(raw=raw):
                self.source.write_bytes(raw)
                self.assert_rejected()

    def test_duplicate_keys_and_nonfinite_numbers_are_rejected(self):
        for raw, expected in [(' {"a": 1, "a": 2}', '重复字段'), ('{"a": NaN}', 'NaN'),
                              ('{"a": Infinity}', 'Infinity')]:
            with self.subTest(raw=raw):
                self.source.write_text(raw, encoding='utf-8')
                self.assert_rejected(expected)

    def test_missing_or_wrong_type_fields_are_rejected(self):
        for field in ['positioning', 'resources', 'stages', 'homepage']:
            with self.subTest(field=field):
                data = copy.deepcopy(self.data)
                del data[field]
                self.write_input(data)
                self.assert_rejected('缺少字段')
        data = copy.deepcopy(self.data)
        data['recommendation']['reason'] = []
        self.write_input(data)
        self.assert_rejected('recommendation.reason')

    def test_extra_fields_are_not_silently_ignored(self):
        self.data['positioning']['fake_score'] = 98
        self.write_input()
        self.assert_rejected('未支持的字段')

    def test_invalid_date_controls_and_surrogates_fail_before_output(self):
        for value in ['2026-02-30', '20261011']:
            with self.subTest(value=value):
                data = copy.deepcopy(self.data)
                data['report_date'] = value
                self.write_input(data)
                self.assert_rejected('report_date')
        for value, expected in [('abc\x00', '控制字符'), ('abc\ud800', 'Unicode')]:
            data = copy.deepcopy(self.data)
            data['subject'] = value
            self.write_input(data)
            self.assert_rejected(expected)

    def test_each_stage_must_fit_known_weekly_budget(self):
        self.data['stages'][1]['tasks'][0]['estimated_minutes'] = 46
        self.write_input()
        self.assert_rejected('stages[1] 预计 181 分钟，超过每周 180 分钟预算')

    def test_known_budget_requires_estimate_for_every_task(self):
        self.data['stages'][0]['tasks'][0]['estimated_minutes'] = None
        self.write_input()
        self.assert_rejected('缺预计耗时')

    def test_minutes_reject_boolean_fraction_negative_and_zero_task(self):
        for value in [True, 1.5, -1, '180']:
            with self.subTest(value=value):
                data = copy.deepcopy(self.data)
                data['resources']['weekly_minutes'] = value
                self.write_input(data)
                self.assert_rejected('weekly_minutes')
        self.data['stages'][0]['tasks'][0]['estimated_minutes'] = 0
        self.write_input()
        self.assert_rejected('estimated_minutes')

    def test_unknown_budget_keeps_unknown_and_does_not_invent_cadence(self):
        self.data['resources']['weekly_minutes'] = None
        self.data['resources']['notes'] = '时间未提供，先做一份样稿。'
        self.data['stages'] = self.data['stages'][:1]
        for task in self.data['stages'][0]['tasks']:
            task['estimated_minutes'] = None
        self.write_input()
        result = self.run_cli(markdown=self.markdown)
        self.assertEqual(result.returncode, 0, result.stderr)
        page = self.output.read_text(encoding='utf-8')
        self.assertIn('本轮耗时待实测；每周预算未提供', page)
        self.assertNotIn('预计 0 分钟', page)
        self.assertNotIn('周预算 0 分钟', page)
        self.assertIn('每周时间未提供；先做一份样稿', self.markdown.read_text(encoding='utf-8'))

    def test_unknown_budget_does_not_accept_multiple_weeks(self):
        self.data['resources']['weekly_minutes'] = None
        self.write_input()
        self.assert_rejected('只安排一轮样稿')

    def test_zero_budget_can_report_no_tasks(self):
        self.data['resources']['weekly_minutes'] = 0
        self.data['stages'] = []
        self.write_input()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('本周可投入时间为零，暂不安排制作任务', self.output.read_text(encoding='utf-8'))

    def test_existing_html_is_never_overwritten(self):
        self.write_input()
        self.output.write_text('原报告', encoding='utf-8')
        result = self.run_cli(markdown=self.markdown)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_text(encoding='utf-8'), '原报告')
        self.assertFalse(self.markdown.exists())

    def test_existing_markdown_prevents_any_html_output(self):
        self.write_input()
        self.markdown.write_text('原核心卡', encoding='utf-8')
        result = self.run_cli(markdown=self.markdown)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.markdown.read_text(encoding='utf-8'), '原核心卡')

    def test_input_output_and_dual_output_conflicts_are_rejected(self):
        self.write_input()
        before = self.source.read_bytes()
        result = self.run_cli(output=self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('输入 JSON 相同', result.stderr)
        self.assertEqual(self.source.read_bytes(), before)
        for destination in [self.output, self.root / 'sub/../报告.html']:
            result = self.run_cli(markdown=destination)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('同一路径', result.stderr)
            self.assertFalse(self.output.exists())

    def test_markdown_cannot_be_input_alias(self):
        self.write_input()
        before = self.source.read_bytes()
        result = self.run_cli(markdown=self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(self.output.exists())

    def test_output_symlink_and_dangling_symlink_are_rejected(self):
        self.write_input()
        target = self.root / '重要文件.md'
        target.write_text('保留', encoding='utf-8')
        for name, destination in [('链接.html', target), ('悬空.html', self.root / '不存在.md')]:
            with self.subTest(name=name):
                output = self.root / name
                output.symlink_to(destination)
                result = self.run_cli(output=output)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(output.is_symlink())
        self.assertEqual(target.read_text(encoding='utf-8'), '保留')
        self.assertFalse((self.root / '不存在.md').exists())

    def test_parent_symlink_is_not_used_including_dotdot(self):
        self.write_input()
        target = self.root / '实际目录'
        target.mkdir()
        link = self.root / '链接目录'
        link.symlink_to(target, target_is_directory=True)
        for output in [link / '报告.html', link / '../绕过.html']:
            result = self.run_cli(output=output)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('软链接', result.stderr)
        self.assertEqual(list(target.iterdir()), [])
        self.assertFalse((self.root / '绕过.html').exists())

    def test_existing_hardlink_is_not_overwritten(self):
        self.write_input()
        before = self.source.read_bytes()
        self.output.hardlink_to(self.source)
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.source.read_bytes(), before)

    def test_missing_parent_or_directory_output_has_no_side_effect(self):
        self.write_input()
        for output in [self.root / '缺目录/报告.html', self.root]:
            result = self.run_cli(output=output)
            self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / '缺目录').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
