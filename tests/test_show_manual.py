"""确认短说明只在第一次会话出现，说明改动后会再出现一次。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / 'hooks' / 'show_manual.py'
MANUAL = PACKAGE / 'docs' / '安装后说明.md'


class ShowManualTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sjskill-manual-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.script = self.root / 'hooks' / 'show_manual.py'
        self.script.parent.mkdir()
        shutil.copy2(SCRIPT, self.script)
        self.manual = self.root / 'docs' / '安装后说明.md'
        self.manual.parent.mkdir()
        self.manual.write_text(MANUAL.read_text(encoding='utf-8'), encoding='utf-8')
        self.home = self.root / 'home'
        self.home.mkdir()
        self.seen = self.home / '.sjskill' / 'seen'
        self.state = self.root / 'manual-shown'
        self.env = os.environ.copy()
        self.env.update(HOME=str(self.home), USERPROFILE=str(self.home), SJ_MANUAL_STATE=str(self.state))
        self.env.pop('CLAUDE_PLUGIN_ROOT', None)
        self.env.pop('CLAUDE_PLUGIN_DATA', None)

    def run_script(self):
        return subprocess.run(
            [sys.executable, '-B', str(self.script)],
            input='{"hook_event_name":"SessionStart"}\n',
            capture_output=True, text=True, env=self.env)

    def test_shows_once_then_again_after_text_changes(self):
        first = self.run_script()
        self.assertEqual(first.returncode, 0, first.stderr)
        payload = json.loads(first.stdout)
        self.assertEqual(payload['systemMessage'], self.manual.read_text(encoding='utf-8').strip())
        self.assertEqual(self.state.read_text(encoding='utf-8').strip(), payload['systemMessage'])
        self.assertEqual(self.seen.read_bytes(), b'')

        second = self.run_script()
        self.assertEqual(second.stdout, '')

        self.manual.write_text(payload['systemMessage'] + '\n\n补了一句。\n', encoding='utf-8')
        third = self.run_script()
        self.assertIn('补了一句。', json.loads(third.stdout)['systemMessage'])

    def test_existing_manual_marker_silently_creates_shared_seen_marker(self):
        self.state.write_text(self.manual.read_text(encoding='utf-8'), encoding='utf-8')
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.seen.read_bytes(), b'')

    def test_skill_seen_marker_skips_first_manual_but_preserves_later_updates(self):
        self.seen.parent.mkdir()
        self.seen.write_text('用户已有标记', encoding='utf-8')
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.state.read_text(encoding='utf-8'), self.manual.read_text(encoding='utf-8'))
        self.assertEqual(self.seen.read_text(encoding='utf-8'), '用户已有标记')

        self.manual.write_text(self.manual.read_text(encoding='utf-8') + '\n说明更新。\n', encoding='utf-8')
        updated = self.run_script()
        self.assertIn('说明更新。', json.loads(updated.stdout)['systemMessage'])

    def test_default_and_plugin_state_paths_share_home_seen_marker(self):
        self.env.pop('SJ_MANUAL_STATE')
        for label in ('default', 'plugin-data'):
            with self.subTest(state=label):
                home = self.root / label
                home.mkdir()
                self.env.update(HOME=str(home), USERPROFILE=str(home))
                if label == 'plugin-data':
                    data = self.root / 'plugin-data-state'
                    self.env['CLAUDE_PLUGIN_DATA'] = str(data)
                    marker = data / 'manual-shown'
                else:
                    marker = home / '.sjskill' / 'manual-shown'
                result = self.run_script()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['systemMessage'], self.manual.read_text(encoding='utf-8').strip())
                self.assertTrue(marker.is_file())
                self.assertEqual((home / '.sjskill' / 'seen').read_bytes(), b'')

    def test_unwritable_seen_marker_does_not_block_manual(self):
        (self.home / '.sjskill').write_text('目录位置被文件占用', encoding='utf-8')
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['systemMessage'], self.manual.read_text(encoding='utf-8').strip())
        self.assertTrue(self.state.is_file())

    def test_skills_share_one_short_first_task_notice(self):
        expected = (PACKAGE / 'skills' / 'sj' / 'SKILL.md').read_text(encoding='utf-8')
        start = expected.index('## 第一次使用')
        end = expected.index('或者「怎么做账号定位」。') + len('或者「怎么做账号定位」。')
        notice = expected[start:end]
        skills = [path for path in (PACKAGE / 'skills').iterdir() if (path / 'SKILL.md').is_file()]
        self.assertEqual(len(skills), 12)
        for skill in skills:
            text = (skill / 'SKILL.md').read_text(encoding='utf-8')
            self.assertIn(notice, text)
            self.assertNotIn('## 首次说明', text)
            self.assertFalse((skill / 'scripts' / 'show_once.py').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
