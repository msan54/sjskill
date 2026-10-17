"""验证整理后的仓库入口、发行边界与损坏文件，不改动真实发行目录。"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from validate_package import DISTRIBUTION_DIRS, DISTRIBUTION_FILES, validate


PACKAGE = Path(__file__).resolve().parents[1]


class PackageValidationTests(unittest.TestCase):
    def package_copy(self, omitted=()):
        root = Path(tempfile.mkdtemp(prefix='sj-package-test-')).resolve()
        self.addCleanup(shutil.rmtree, root)

        def excluded(directory, names):
            return [name for name in names
                    if name == '__pycache__' or name.endswith(('.pyc', '.pyo'))
                    or (Path(directory) / name).relative_to(PACKAGE).as_posix() in omitted]

        for directory in DISTRIBUTION_DIRS:
            shutil.copytree(PACKAGE / directory, root / directory, ignore=excluded)
        for name in DISTRIBUTION_FILES:
            if name not in omitted:
                shutil.copy2(PACKAGE / name, root / name)
        return root

    def test_flat_root_default_cli_excludes_development_files(self):
        root = self.package_copy()
        tests = root / 'tests'
        tests.mkdir()
        checker = tests / 'validate_package.py'
        shutil.copy2(PACKAGE / 'tests/validate_package.py', checker)
        (tests / 'development_only.py').write_text('import unavailable_development_dependency\n', encoding='utf-8')
        (root / '.git').mkdir()
        (root / '.git/objects').write_bytes(b'\xff\x00')
        result = subprocess.run([sys.executable, '-B', str(checker)],
                                cwd=tests, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        report = json.loads(result.stdout)
        self.assertTrue(report['passed'])
        self.assertEqual(len(report['skills']), 13)
        self.assertEqual(report['failures'], [])

    def test_cache_and_binary_files_report_failures_without_crashing(self):
        root = self.package_copy()
        cache = root / 'skills/sj-positioning/scripts/__pycache__'
        cache.mkdir()
        (cache / 'render_report.pyc').write_bytes(b'\xff\x00')
        (root / 'skills/sj-positioning/assets/unexpected.bin').write_bytes(b'\xff\x00')
        report = validate(root)
        self.assertFalse(report['passed'])
        self.assertTrue(any('render_report.pyc' in message and '禁止' in message for message in report['failures']))
        self.assertTrue(any('unexpected.bin' in message and '二进制' in message for message in report['failures']))

    def test_missing_required_distribution_files_fail(self):
        for name in ('README.md', '.claude-plugin/plugin.json',
                     'licenses/CC-BY-NC-4.0.txt', 'skills/sj/agents/openai.yaml'):
            with self.subTest(file=name):
                report = validate(self.package_copy(omitted=(name,)))
                self.assertFalse(report['passed'])
                self.assertIn(name + '：缺少必要发行文件', report['failures'])

    def test_runtime_external_dependencies_are_still_rejected(self):
        root = self.package_copy()
        script = root / 'skills/sj-positioning/scripts/unexpected_dependency.py'
        script.write_text('import unavailable_runtime_dependency\n', encoding='utf-8')
        report = validate(root)
        self.assertFalse(report['passed'])
        self.assertTrue(any('unexpected_dependency.py' in message and '外部 Python 依赖' in message
                            for message in report['failures']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
