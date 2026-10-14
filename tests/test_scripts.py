"""在隔离临时目录中验证真实文件行为，不访问个人知识库或外部账号。"""
import base64
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
RENDER = PACKAGE / 'skills/sj-wechat-format/scripts/render.py'
INIT = PACKAGE / 'skills/sj-knowledge-base/scripts/init_library.py'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='sjskill-case-')).resolve()
        self.addCleanup(shutil.rmtree, self.root)

    def run_script(self, script, *args):
        return subprocess.run([sys.executable, '-B', str(script), *map(str, args)], capture_output=True, text=True)

    def article(self, content, name='稿件.md'):
        file = self.root / name
        file.write_text(content, encoding='utf-8')
        return file

    def html(self, name='稿件'):
        return self.root / '公众号HTML输出' / name / (name + '_微信公众号版.html')

    def test_library_preview_create_repeat_preserves_user_text(self):
        target = self.root / '中文 空格库'
        self.assertEqual(self.run_script(INIT, target, '--dry-run').returncode, 0)
        self.assertFalse(target.exists())
        self.assertEqual(self.run_script(INIT, target).returncode, 0)
        self.assertEqual(len([p for p in target.rglob('*') if p.is_dir()]), 10)
        document = target / '知识库说明.md'
        document.write_text('用户自己的存放约定', encoding='utf-8')
        self.assertEqual(self.run_script(INIT, target).returncode, 0)
        self.assertEqual(document.read_text(), '用户自己的存放约定')
        self.assertFalse((target / '定位报告.md').exists())

    def test_library_conflict_leaves_no_partial_folders(self):
        target = self.root / '已有库'
        target.mkdir()
        (target / '素材案例库').write_text('重要原文件')
        self.assertNotEqual(self.run_script(INIT, target).returncode, 0)
        self.assertEqual(list(p.name for p in target.iterdir()), ['素材案例库'])

    def test_library_symlink_is_not_followed(self):
        target = self.root / '库'
        target.symlink_to(self.root, target_is_directory=True)
        self.assertNotEqual(self.run_script(INIT, target).returncode, 0)
        self.assertFalse((self.root / '知识库说明.md').exists())

    def test_library_root_traversal_is_rejected_in_preview(self):
        filesystem_root = Path(self.root.anchor)
        result = self.run_script(INIT, filesystem_root / self.root.relative_to(filesystem_root).parts[0] / '..', '--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('文件系统根目录', result.stderr)

    def test_render_inline_images_code_and_source_preservation(self):
        (self.root / '图 一.png').write_bytes(PNG)
        source = self.article('# 标题\n\n前文![说明](图%20一.png)后文\n\n![[图 一.png|400]]\n\n`**原样** [链接](https://example.test) class= url(`\n\n==重点==和**加粗**\n\n```\n<script>不执行</script>\n```\n')
        before = source.read_bytes()
        result = self.run_script(RENDER, source)
        self.assertEqual(result.returncode, 0, result.stderr)
        html = self.html().read_text()
        self.assertEqual(html.count('src="data:image/png;base64,'), 2)
        for text in ['前文', '后文', 'alt="说明"', '**原样**', '[链接](https://example.test)', '&lt;script&gt;不执行&lt;/script&gt;']:
            self.assertIn(text, html)
        self.assertNotIn('参考链接', html)
        self.assertEqual(source.read_bytes(), before)

    def test_as_resolves_original_images_and_output(self):
        (self.root / '图.png').write_bytes(PNG)
        original = self.article('# 正文\n![](图.png)')
        temporary = self.root / '临时'
        temporary.mkdir()
        copy = temporary / '标注.md'
        copy.write_text('# 正文\n![](图.png)')
        result = self.run_script(RENDER, copy, '--as', original)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.html().is_file())

    def test_wechat_adds_no_author_contact_and_preserves_existing_contact(self):
        signature = '叁斤｜公众号：叁斤｜微信：Msan54'
        for label, content in [('没有署名', '# 用户文章\n\n这是用户自己的正文。\n'),
                               ('已有署名', '# 用户文章\n\n这是用户自己的正文。\n\n' + signature + '\n')]:
            with self.subTest(article=label):
                source = self.article(content, name=label + '.md')
                before = source.read_bytes()
                result = self.run_script(RENDER, source)
                self.assertEqual(result.returncode, 0, result.stderr)
                rendered = self.html(label).read_text(encoding='utf-8')
                self.assertEqual('Msan54' in rendered, 'Msan54' in content)
                if signature in content:
                    self.assertEqual(rendered.count(signature), 1)
                self.assertNotIn('这是叁斤给出的账号定位方案', rendered)
                self.assertEqual(source.read_bytes(), before)

    def test_missing_image_fails_before_creating_output(self):
        result = self.run_script(RENDER, self.article('文字![缺图](不存在.png)尾文'))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / '公众号HTML输出').exists())

    def test_invalid_image_and_remote_image_are_rejected(self):
        (self.root / '假图.png').write_text('不是图片')
        for text in ['![](假图.png)', '![](https://example.test/a.png)']:
            result = self.run_script(RENDER, self.article(text))
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.html().exists())

    def test_existing_output_is_not_replaced_without_flag(self):
        source = self.article('第一稿')
        self.assertEqual(self.run_script(RENDER, source).returncode, 0)
        before = self.html().read_bytes()
        source.write_text('第二稿')
        self.assertNotEqual(self.run_script(RENDER, source).returncode, 0)
        self.assertEqual(self.html().read_bytes(), before)
        self.assertEqual(self.run_script(RENDER, source, '--overwrite').returncode, 0)
        self.assertIn('第二稿', self.html().read_text())

    def test_output_file_symlink_never_changes_source(self):
        source = self.article('原稿必须保留')
        self.html().parent.mkdir(parents=True)
        self.html().symlink_to(source)
        result = self.run_script(RENDER, source, '--overwrite')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(source.read_text(), '原稿必须保留')

    def test_output_directory_symlink_never_writes_elsewhere(self):
        source = self.article('原稿必须保留')
        elsewhere = self.root / '其他文件夹'
        elsewhere.mkdir()
        (self.root / '公众号HTML输出').symlink_to(elsewhere, target_is_directory=True)
        result = self.run_script(RENDER, source, '--overwrite')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_output_hardlink_never_changes_source(self):
        source = self.article('原稿必须保留')
        self.html().parent.mkdir(parents=True)
        self.html().hardlink_to(source)
        result = self.run_script(RENDER, source, '--overwrite')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(source.read_text(), '原稿必须保留')

    def test_color_injection_and_missing_option_value_fail(self):
        source = self.article('正文')
        for args in [('--h-color', 'red; background:url(https://example.test)'), ('--h-color',)]:
            self.assertNotEqual(self.run_script(RENDER, source, *args).returncode, 0)
            self.assertFalse(self.html().exists())
        self.assertEqual(self.run_script(RENDER, source, '--h-color', '#1a7f37').returncode, 0)

    def test_empty_document_is_not_a_success(self):
        result = self.run_script(RENDER, self.article('---\ntags: test\n---\n# 只有标题\n'))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.html().exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
