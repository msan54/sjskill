"""定位报告生成器：通过真实 CLI 验证内容结构、核心卡同源和文件保护。"""
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
REGIONS = ('report-attribution', 'report-status', 'verdict', 'assumptions', 'core', 'formula', 'audience',
           'directions', 'style-guide', 'bio', 'growth', 'monetization', 'launch', 'score', 'market')
VOID = ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr')


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
        self.regions = {key: '' for key in REGIONS}

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        values = dict(attrs)
        if tag not in VOID:
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

    def render_ok(self, data=None):
        self.write_input(data)
        result = self.run_cli(markdown=self.markdown)
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = PageParser()
        parsed.feed(self.output.read_text(encoding='utf-8'))
        return parsed

    def assert_rejected(self, expected=None, data=None):
        self.write_input(data)
        result = self.run_cli(markdown=self.markdown)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        if expected:
            self.assertIn(expected, result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.markdown.exists())
        self.assertNotIn('Traceback', result.stderr)
        return result

    def changed(self, mutate):
        data = copy.deepcopy(self.data)
        mutate(data)
        return data

    # 内容结构

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
        self.assertIn(self.data['verdict'], parsed.regions['verdict'])
        self.assertIn('虚构示例', page)
        self.assertEqual(len(parsed.scripts), 1)
        self.assertTrue(all(link.startswith('#') for link in parsed.links))
        self.assertEqual(parsed.events, [])
        self.assertNotIn('@@', page)

    def test_formula_uses_five_items_in_order_for_page_and_core_card(self):
        parsed = self.render_ok()
        self.assertIn('价值 × 用户 × 人设 × 类型 × 风格', parsed.regions['formula'])
        labels = ['核心价值', '目标用户', '关键人设', '内容类型', '核心风格']
        positions = [parsed.core.index(f'- {label}：') for label in labels]
        self.assertEqual(positions, sorted(positions))
        for field in ('value', 'audience', 'persona', 'format', 'style'):
            self.assertIn(self.data['core'][field], parsed.regions['formula'])
            self.assertIn(self.data['core'][field], parsed.core)
        for field in ('one_liner', 'platform', 'goal'):
            self.assertIn(self.data['core'][field], parsed.core)
        for item in self.data['core']['red_lines']:
            self.assertIn(item, parsed.core)
        for item in self.data['directions']:
            self.assertIn(item['name'], parsed.core)

    def test_core_card_stays_short_enough_to_paste(self):
        parsed = self.render_ok()
        self.assertLessEqual(len(parsed.core.splitlines()), 30)
        self.assertIn('先把这张卡贴给它', parsed.core)
        for detail in (self.data['audience']['inner'], self.data['score']['verdict'], self.data['style_guide']['bio']):
            self.assertNotIn(detail, parsed.core)

    def test_every_section_shows_its_data(self):
        parsed = self.render_ok()
        for value in self.data['audience'].values():
            self.assertIn(value, parsed.regions['audience'])
        for item in self.data['directions']:
            for value in (item['name'], item['role'], item['share'], item['why'], *item['topics']):
                self.assertIn(value, parsed.regions['directions'])
        style = self.data['style_guide']
        for value in (*style['names'], style['name_note'], style['avatar'], style['cover'], *style['voice_rules']):
            self.assertIn(value, parsed.regions['style-guide'])
        self.assertIn(style['bio'], parsed.regions['bio'])
        for item in self.data['growth']:
            self.assertIn(item['stage'], parsed.regions['growth'])
            self.assertIn(item['plan'], parsed.regions['growth'])
        money = self.data['monetization']
        for value in (money['short_term'], money['long_term'], *money['warnings']):
            self.assertIn(value, parsed.regions['monetization'])
        launch = self.data['launch']
        for value in (*launch['week1'], *launch['weeks2_4'], *launch['benchmark'], launch['reminder']):
            self.assertIn(value, parsed.regions['launch'])
        for item in self.data['score']['items']:
            for value in item.values():
                self.assertIn(value, parsed.regions['score'])
        self.assertIn(self.data['score']['verdict'], parsed.regions['score'])
        for word in self.data['market_keywords']:
            self.assertIn(word, parsed.regions['market'])
        self.assertIn('三样里过两样，直接开干', parsed.regions['market'])

    def test_required_sections_and_fields_cannot_be_omitted(self):
        paths = [(field,) for field in ('verdict', 'core', 'audience', 'directions', 'style_guide', 'growth',
                                        'monetization', 'launch', 'score', 'market_keywords')]
        paths += [('core', field) for field in self.data['core']]
        paths += [('audience', field) for field in self.data['audience']]
        paths += [('style_guide', field) for field in self.data['style_guide']]
        paths += [('monetization', field) for field in self.data['monetization']]
        paths += [('launch', field) for field in self.data['launch']]
        for path in paths:
            with self.subTest(path=path):
                data = copy.deepcopy(self.data)
                target = data if len(path) == 1 else data[path[0]]
                del target[path[-1]]
                self.assert_rejected('缺少字段', data)

    def test_status_only_accepts_complete_or_draft(self):
        for status in ('探索草案', '', 1, None):
            with self.subTest(status=status):
                self.assert_rejected('status', self.changed(lambda d: d.__setitem__('status', status)))

    def test_draft_must_list_guesses_and_shows_them_first(self):
        def to_draft(data):
            data['status'] = '初稿'
            data.pop('assumptions', None)
        self.assert_rejected('assumptions', self.changed(to_draft))
        guesses = ['用户没说每周时间，按每周 3 小时排的节奏', '没问到红线，暂按不露脸写']

        def draft(data):
            data['status'] = '初稿'
            data['assumptions'] = guesses
            data['directions'] = data['directions'][:1]
            data['directions'][0]['topics'] = data['directions'][0]['topics'][:1]
            data['growth'] = data['growth'][:1]
            data['market_keywords'] = data['market_keywords'][:1]
        parsed = self.render_ok(self.changed(draft))
        self.assertIn('初稿', parsed.regions['report-status'])
        for item in guesses:
            self.assertIn(item, parsed.regions['assumptions'])
            self.assertIn(item, parsed.core)

    def test_complete_plan_needs_three_to_five_directions_with_two_topics(self):
        for count in (0, 1, 2, 6):
            with self.subTest(count=count):
                data = self.changed(lambda d: d.__setitem__('directions', [copy.deepcopy(d['directions'][0]) for _ in range(count)]))
                self.assert_rejected('directions', data)
        self.assert_rejected('directions[1].topics',
                             self.changed(lambda d: d['directions'][1].__setitem__('topics', d['directions'][1]['topics'][:1])))
        self.assert_rejected('role', self.changed(lambda d: d['directions'][0].__setitem__('role', '干货款')))

    def test_style_guide_needs_names_and_three_voice_rules(self):
        self.assert_rejected('style_guide.names', self.changed(lambda d: d['style_guide'].__setitem__('names', ['只有一个'])))
        self.assert_rejected('style_guide.names', self.changed(lambda d: d['style_guide'].__setitem__('names', list('一二三四'))))
        self.assert_rejected('voice_rules', self.changed(lambda d: d['style_guide'].__setitem__('voice_rules', ['一条', '两条'])))

    def test_growth_needs_three_stages(self):
        self.assert_rejected('growth', self.changed(lambda d: d.__setitem__('growth', d['growth'][:2])))

    def test_score_has_four_named_items_with_levels_not_numbers(self):
        self.assert_rejected('score.items', self.changed(lambda d: d['score'].__setitem__('items', d['score']['items'][:3])))
        self.assert_rejected('应为「供需」', self.changed(lambda d: d['score']['items'][1].__setitem__('name', '热度')))
        for level in ('7.5 分', '较高', 'high', ''):
            with self.subTest(level=level):
                self.assert_rejected('score.items[0]', self.changed(lambda d: d['score']['items'][0].__setitem__('level', level)))

    def test_market_check_needs_three_to_five_keywords(self):
        for words in (['电商运营', '转行'], [str(index) for index in range(6)]):
            with self.subTest(count=len(words)):
                self.assert_rejected('market_keywords', self.changed(lambda d: d.__setitem__('market_keywords', words)))

    def test_monetization_and_launch_lists_cannot_be_empty(self):
        self.assert_rejected('monetization.warnings', self.changed(lambda d: d['monetization'].__setitem__('warnings', [])))
        for field in ('week1', 'weeks2_4', 'benchmark'):
            with self.subTest(field=field):
                self.assert_rejected(f'launch.{field}', self.changed(lambda d: d['launch'].__setitem__(field, [])))

    def test_no_red_lines_is_stated_not_left_blank(self):
        parsed = self.render_ok(self.changed(lambda d: d['core'].__setitem__('red_lines', [])))
        self.assertIn('红线：用户没有提出', parsed.core)

    # 署名与安全

    def test_report_attribution_is_visible_and_separate_from_user_identity(self):
        parsed = self.render_ok()
        for value in ['这是叁斤给出的账号定位方案', '叁斤｜公众号：叁斤｜微信：Msan54', '由叁斤提供的 SJskill 基于当前资料生成']:
            self.assertIn(value, parsed.regions['report-attribution'])
        for region in ('bio', 'formula'):
            self.assertNotIn('Msan54', parsed.regions[region])
            self.assertNotIn('叁斤', parsed.regions[region])
        content, attribution = parsed.core.split('\n---\n')
        self.assertNotIn('Msan54', content)
        self.assertNotIn('叁斤', content)
        self.assertIn('叁斤｜公众号：叁斤｜微信：Msan54', attribution)
        self.assertIn('不写进你的昵称、简介、选题和笔记', attribution)

    def test_manual_template_has_attribution_without_user_data_fields(self):
        parsed = PageParser()
        parsed.feed((SKILL / 'assets/report.html').read_text(encoding='utf-8'))
        self.assertIn('这是叁斤给出的账号定位方案', parsed.regions['report-attribution'])
        self.assertIn('叁斤｜公众号：叁斤｜微信：Msan54', parsed.regions['report-attribution'])
        self.assertNotIn('@@', parsed.regions['report-attribution'])

    def test_all_user_strings_are_text_not_html_or_javascript(self):
        attack = '</textarea><script>alert("test")</script><img src="https://invalid.test/x" onerror="alert(1)"><style>body{display:none}</style>&@@TITLE@@'
        fixed = {'report_date', 'status', 'role', 'level', 'name'}

        def contaminate(value, key=None):
            if isinstance(value, dict):
                return {k: (item if k in fixed else contaminate(item, k)) for k, item in value.items()}
            if isinstance(value, list):
                return [contaminate(item, key) for item in value]
            return value + attack if isinstance(value, str) else value
        parsed = self.render_ok(contaminate(self.data))
        template = PageParser()
        template.feed((SKILL / 'assets/report.html').read_text(encoding='utf-8'))
        self.assertEqual(parsed.scripts, template.scripts)
        self.assertEqual(parsed.tags.count('textarea'), 1)
        self.assertEqual(parsed.tags.count('style'), 1)
        self.assertNotIn('img', parsed.tags)
        self.assertEqual(parsed.events, [])
        self.assertTrue(all(link.startswith('#') for link in parsed.links))
        markdown = self.markdown.read_text(encoding='utf-8')
        self.assertNotIn('<script>', markdown)
        self.assertEqual(parsed.core, markdown)

    # 输入校验

    def test_invalid_json_and_utf8_leave_no_outputs(self):
        for raw in [b'{"report_title":', b'\xff\xfe']:
            with self.subTest(raw=raw):
                self.source.write_bytes(raw)
                result = self.run_cli(markdown=self.markdown)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.markdown.exists())

    def test_duplicate_keys_and_nonfinite_numbers_are_rejected(self):
        for raw, expected in [(' {"a": 1, "a": 2}', '重复字段'), ('{"a": NaN}', 'NaN'), ('{"a": Infinity}', 'Infinity')]:
            with self.subTest(raw=raw):
                self.source.write_text(raw, encoding='utf-8')
                result = self.run_cli()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertFalse(self.output.exists())

    def test_wrong_types_and_extra_fields_are_rejected(self):
        self.assert_rejected('report', self.changed(lambda d: d.__setitem__('verdict', [])))
        self.assert_rejected('core.one_liner', self.changed(lambda d: d['core'].__setitem__('one_liner', '  ')))
        self.assert_rejected('未支持的字段', self.changed(lambda d: d['core'].__setitem__('fake_score', 98)))
        self.write_input([1, 2])
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('必须是对象', result.stderr)

    def test_invalid_date_controls_and_surrogates_fail_before_output(self):
        for value in ['2026-02-30', '20261011']:
            with self.subTest(value=value):
                self.assert_rejected('report_date', self.changed(lambda d: d.__setitem__('report_date', value)))
        for value, expected in [('abc\x00', '控制字符'), ('abc\ud800', 'Unicode')]:
            with self.subTest(value=repr(value)):
                self.assert_rejected(expected, self.changed(lambda d: d.__setitem__('subject', value)))

    # 文件保护

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
