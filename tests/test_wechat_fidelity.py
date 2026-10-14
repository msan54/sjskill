"""核对真实 HTML，并故意删字、错列、换图和改链接；不访问外部账号。"""
import base64
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPTS = PACKAGE / 'skills/sj-wechat-format/scripts'
sys.path.insert(0, str(SCRIPTS))
import render
from fidelity import read_markdown, verify_html

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
GIF = base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7')


class FidelityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='sj-fidelity-')).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / '甲.png').write_bytes(PNG)
        (self.root / '乙.gif').write_bytes(GIF)
        self.source = self.root / '原稿.md'
        self.copy = self.root / '标注.md'
        self.output = self.root / '公众号HTML输出/原稿/原稿_微信公众号版.html'

    def run_render(self, original, annotated=None, *extra):
        self.source.write_text(original, encoding='utf-8')
        args = [sys.executable, '-B', str(SCRIPTS / 'render.py'), str(self.source)]
        if annotated is not None:
            self.copy.write_text(annotated, encoding='utf-8')
            args = [sys.executable, '-B', str(SCRIPTS / 'render.py'), str(self.copy), '--as', str(self.source)]
        result = subprocess.run(args + list(extra), capture_output=True, text=True)
        self.assertEqual(self.source.read_text(), original)
        if annotated is not None:
            self.assertEqual(self.copy.read_text(), annotated)
        return result

    def assert_rejected(self, original, annotated):
        result = self.run_render(original, annotated)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('保真失败', result.stderr)
        self.assertFalse(self.output.exists())

    def html_check(self, original, html):
        verify_html(read_markdown(original, self.source.stem), html,
                    lambda name: render.find_img(name, str(self.root)))

    def test_formatting_changes_are_accepted_and_checks_are_separate(self):
        original = '# 标题\n\n先核对\n不要跳过核对。\n第一步，保留原文。\n- 甲项\n'
        annotated = '# **标题**\n\n## 先核对\n> ==不要跳过核对。==\n**第一步，**保留原文。\n* 甲项\n'
        result = self.run_render(original, annotated)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('原稿→标注副本保真：通过', result.stdout)
        self.assertIn('输入稿→HTML 保真：通过', result.stdout)
        self.assertIn('兼容自检：通过', result.stdout)

    def test_missing_negation_is_rejected(self):
        self.assert_rejected('# 标题\n不能删除这一句。', '# 标题\n==能删除这一句。==')

    def test_title_change_is_rejected(self):
        self.assert_rejected('# 不推荐\n正文。', '# 推荐\n正文。')

    def test_link_label_and_target_changes_are_rejected(self):
        original = '# 标题\n读[原文](https://example.test/a?x=1&y=2)。'
        for annotated in [original.replace('原文', '改文'), original.replace('/a?', '/b?')]:
            with self.subTest(annotated=annotated):
                self.assert_rejected(original, annotated)

    def test_url_punctuation_is_not_treated_as_formatting(self):
        original = '# 标题\n[原文](https://example.test/**a**)。'
        self.assert_rejected(original, original.replace('**a**', 'a'))

    def test_link_pair_reordering_is_rejected(self):
        original = '# 标题\n[甲](https://example.test/a)和[乙](https://example.test/b)。'
        self.assert_rejected(original, original.replace('/a)', '/temporary)').replace('/b)', '/a)').replace('/temporary)', '/b)'))

    def test_image_reorder_and_alt_change_are_rejected(self):
        original = '# 标题\n![第一张](甲.png)\n![第二张](乙.gif)\n'
        for annotated in [original.replace('甲.png', '暂.png').replace('乙.gif', '甲.png').replace('暂.png', '乙.gif'),
                          original.replace('第一张', '第二张')]:
            with self.subTest(annotated=annotated):
                self.assert_rejected(original, annotated)

    def test_image_position_relative_to_text_is_preserved(self):
        original = '# 标题\n前文![说明](甲.png)后文。'
        self.assert_rejected(original, '# 标题\n前文后文。![说明](甲.png)')
        result = self.run_render(original, '# 标题\n==前文==![说明](甲.png)**后文。**')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_code_and_internal_spaces_are_not_normalized_away(self):
        original = '# 标题\n```\n不能删  两个空格 **保留**\n```\n'
        self.assert_rejected(original, original.replace('  ', ' '))
        self.assert_rejected(original, original.replace('不能删', '能删'))

    def test_code_images_links_and_html_are_literal(self):
        original = '# 标题\n`<table>`\n`![字面图](不存在.png)`和`[字面链接](https://example.test)`\n```\n| 名字 | 值 |\n| --- | --- |\n| A | 不变 |\n```\n'
        result = self.run_render(original)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('参考链接', self.output.read_text())

    def test_tables_and_manual_conversions_cannot_claim_automatic_equivalence(self):
        original = '# 标题\n| 姓名 | 收入 |\n| --- | --- |\n| 甲 | 10 |\n| 乙 | 20 |\n'
        for annotated in [original, '# 标题\n- 姓名：甲；收入：10\n- 姓名：乙；收入：20\n',
                          '# 标题\n- 姓名：甲；收入：20\n- 姓名：乙；收入：10\n']:
            with self.subTest(annotated=annotated):
                result = self.run_render(original, annotated)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('无法自动等价校验表格', result.stderr)
                self.assertFalse(self.output.exists())

    def test_single_column_table_is_rejected_but_literal_pipes_stay(self):
        result = self.run_render('# 标题\n| 内容 |\n| --- |\n| 不删除 |')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())
        self.assert_rejected('# 标题\n甲 | 乙，不可互换。', '# 标题\n甲乙，不可互换。')

    def test_truncated_code_fence_fails(self):
        result = self.run_render('# 标题\n```\n不能默默吞掉下一段。')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('缺少结束围栏', result.stderr)
        self.assertFalse(self.output.exists())

    def test_closing_fence_cannot_silently_drop_trailing_text(self):
        result = self.run_render('# 标题\n```\n代码\n```不能丢失\n后文')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('代码围栏格式不支持', result.stderr)
        self.assertFalse(self.output.exists())

    def test_url_parentheses_are_rejected_instead_of_truncating_target(self):
        result = self.run_render('# 标题\n[百科](https://example.test/a_(b))')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('无法完整解析该 URL', result.stderr)
        self.assertFalse(self.output.exists())
        result = self.run_render('# 标题\n[百科](https://example.test/a_%28b%29)')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('https://example.test/a_%28b%29', self.output.read_text())

    def test_html_mutations_are_detected_from_actual_output(self):
        original = '# 核验标题\n不应删字。\n![甲图](甲.png)\n![乙图](乙.gif)\n读[**来源**](https://example.test/a?x=1&y=2)。\n'
        result = self.run_render(original)
        self.assertEqual(result.returncode, 0, result.stderr)
        html = self.output.read_text()
        self.html_check(original, html)
        uris = re.findall(r'src="([^"]+)"', html)
        switched = html.replace(uris[0], 'TEMP_URI').replace(uris[1], uris[0]).replace('TEMP_URI', uris[1])
        mutations = [html.replace('不应删字。', '应删字。'),
                     html.replace('<title>核验标题</title>', '<title>错误标题</title>'),
                     html.replace('https://example.test/a?', 'https://example.test/b?'),
                     html.replace('alt="甲图"', 'alt="错图"'), switched,
                     html.replace('• <strong', '• 增加字<strong')]
        for changed in mutations:
            with self.subTest(index=mutations.index(changed)):
                with self.assertRaises(ValueError):
                    self.html_check(original, changed)

    def test_dropped_render_text_fails_before_creating_output(self):
        self.source.write_text('# 标题\n不要删字。', encoding='utf-8')
        real_inline = render.inline
        with patch.object(render, 'inline', side_effect=lambda text, style, links: real_inline(text.replace('不', ''), style, links)):
            with self.assertRaisesRegex(ValueError, 'HTML 保真失败'):
                render.render(self.source)
        self.assertFalse((self.root / '公众号HTML输出').exists())

    def test_rejected_copy_does_not_overwrite_previous_html(self):
        original = '# 标题\n不能删字。'
        self.assertEqual(self.run_render(original).returncode, 0)
        before = self.output.read_bytes()
        result = self.run_render(original, original.replace('不能', '能'), '--overwrite')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_bytes(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
