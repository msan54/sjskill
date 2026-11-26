"""验证通用知识库体检脚本的各项扫描、误报豁免、隐私保护和严格只读边界。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / 'skills' / 'sj-kb-checkup' / 'scripts' / 'kb_scan.py'


def set_age_days(path, days):
    ts = time.time() - days * 86400
    os.utime(path, (ts, ts))


class KnowledgeBaseScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sj-kb-checkup-test-')
        self.addCleanup(self.tmp.cleanup)
        self.kb = Path(self.tmp.name).resolve() / '模拟创业大脑'
        self.kb.mkdir()

        # 构造模拟多分区乱库
        (self.kb / '.obsidian').mkdir()
        (self.kb / '00_收件箱' / '待处理').mkdir(parents=True)
        (self.kb / '01_运营共享' / '01-输入' / '待审核').mkdir(parents=True)
        (self.kb / '01_运营共享' / '02-业务卡片' / '项目案例').mkdir(parents=True)
        (self.kb / '01_运营共享' / '02-业务卡片' / '历史').mkdir(parents=True)
        (self.kb / '01_运营共享' / '空分类').mkdir(parents=True)
        (self.kb / '01_运营共享' / 'skills' / 'demo-skill').mkdir(parents=True)
        (self.kb / '01_运营共享' / '归档').mkdir(parents=True)
        (self.kb / '02_公司经营' / '02_业务标准' / '商务' / '历史流程').mkdir(parents=True)
        (self.kb / '02_公司经营' / 'a' / 'b' / 'c' / 'd' / 'e').mkdir(parents=True)
        (self.kb / '归档').mkdir(parents=True)

        # 1. 多端协作说明：根目录下 CLAUDE.md 与 AGENTS.md 不同步，GEMINI.md 与 AGENTS.md 同步
        (self.kb / 'CLAUDE.md').write_text(
            '# 协作说明\n\n'
            '有效路径：`01_运营共享/02-业务卡片/项目案例/案例A.md`\n'
            '失效路径：`01_运营共享/不存在的目录/旧表.md`\n'
            '占位符路径：`01_运营共享/[产品名]/买点.md`\n'
            '已删旧路径 `02_公司经营/已删目录/旧文件.md` 不再使用。\n',
            encoding='utf-8',
        )
        (self.kb / 'AGENTS.md').write_text('# 协作说明（新）\n\n已更新规矩。\n', encoding='utf-8')
        (self.kb / 'GEMINI.md').write_text('# 协作说明（新）\n\n已更新规矩。\n', encoding='utf-8')

        # 2. 子分区协作说明（一致，不应误报不同步）
        shared_rule = '# 运营共享说明\n\n参考 [案例A](02-业务卡片/项目案例/案例A.md)。\n'
        (self.kb / '01_运营共享' / 'CLAUDE.md').write_text(shared_rule, encoding='utf-8')
        (self.kb / '01_运营共享' / 'AGENTS.md').write_text(shared_rule, encoding='utf-8')

        # 3. 真实业务文件、完全重复文件、同名冲突文件、空文件
        case_a = self.kb / '01_运营共享' / '02-业务卡片' / '项目案例' / '案例A.md'
        case_a.write_text('# 案例 A\n\n真实活动记录。\n', encoding='utf-8')
        dup_copy = self.kb / '02_公司经营' / '案例A副本.md'
        dup_copy.write_text('# 案例 A\n\n真实活动记录。\n', encoding='utf-8')
        (self.kb / '01_运营共享' / '报价规范.md').write_text('# 报价规范\n旧版本\n', encoding='utf-8')
        (self.kb / '02_公司经营' / '报价规范.md').write_text('# 报价规范\n新版本\n', encoding='utf-8')
        (self.kb / '02_公司经营' / '空草稿.md').write_text('', encoding='utf-8')

        # 4. 积压与陈旧文件（收件箱 45 天、活跃文档 120 天、归档区 200 天）
        old_inbox = self.kb / '00_收件箱' / '待处理' / '囤积文章.md'
        old_inbox.write_text('# 外部文章\n', encoding='utf-8')
        set_age_days(old_inbox, 45)

        old_active = self.kb / '02_公司经营' / '旧SOP.md'
        old_active.write_text('# 旧流程\n', encoding='utf-8')
        set_age_days(old_active, 120)

        old_archive = self.kb / '归档' / '两年前旧稿.md'
        old_archive.write_text('# 已归档\n', encoding='utf-8')
        set_age_days(old_archive, 200)

        # 5. Skill 清单
        (self.kb / '01_运营共享' / 'skills' / 'demo-skill' / 'SKILL.md').write_text(
            '---\nname: demo-skill\ndescription: 示例\n---\n', encoding='utf-8'
        )

        # 6. 共享区敏感信息（合成假手机号，验证脚本报警但不泄漏明文）
        self.fake_phone = '138' + '00138000'
        (self.kb / '01_运营共享' / '客户合同明细.md').write_text(
            f'联系人电话：{self.fake_phone}\n', encoding='utf-8'
        )

    def run_scan(self, *extra_args):
        return subprocess.run(
            [sys.executable, '-B', str(SCRIPT), str(self.kb), *extra_args],
            capture_output=True, text=True,
        )

    def test_full_scan_detects_all_expected_issues_without_modifying_files(self):
        before = sorted(p.relative_to(self.kb).as_posix() for p in self.kb.rglob('*'))
        result = self.run_scan()
        self.assertEqual(result.returncode, 0, result.stderr)
        # 绝不泄漏敏感明文
        self.assertNotIn(self.fake_phone, result.stdout)
        report = json.loads(result.stdout)
        self.assertTrue(report['has_obsidian'])

        # 结构检查
        structure = report['checks']['structure']
        self.assertIn('01_运营共享/空分类', structure['empty_dirs'])
        self.assertTrue(any(d['depth'] > 4 for d in structure['deep_dirs']))
        self.assertIn('02_公司经营/空草稿.md', structure['empty_files'])

        # 散落历史夹检查（根归档和分区归档不能被误报）
        scattered = report['checks']['scattered']['scattered_history_dirs']
        self.assertIn('01_运营共享/02-业务卡片/历史', scattered)
        self.assertIn('02_公司经营/02_业务标准/商务/历史流程', scattered)
        self.assertNotIn('归档', scattered)
        self.assertNotIn('01_运营共享/归档', scattered)

        # 积压与陈旧（归档区必须排除）
        stale = report['checks']['stale']
        self.assertTrue(any(x['path'] == '00_收件箱/待处理/囤积文章.md' for x in stale['inbox_backlog_top']))
        self.assertTrue(any(x['path'] == '02_公司经营/旧SOP.md' for x in stale['stale_markdown_top']))
        self.assertFalse(any('两年前旧稿' in x['path'] for x in stale['stale_markdown_top']))

        # 重复与 Skill 盘点
        duplicates = report['checks']['duplicates']
        self.assertEqual(duplicates['exact_duplicate_groups_total'], 1)
        self.assertEqual(duplicates['same_name_markdown_groups_total'], 1)
        self.assertIn('demo-skill', duplicates['skills_found'])

        # 多 Agent 规则检查：根目录不同步被抓到，子分区同步不误报，死链抓到但占位符和已删日志行放行
        rules = report['checks']['rules']
        self.assertEqual(rules['rule_files_found_total'], 5)
        self.assertEqual(len(rules['multi_agent_drift']), 1)
        self.assertEqual(rules['multi_agent_drift'][0]['scope'], '.')
        dead_targets = [d['target'] for d in rules['suspected_dead_links']]
        self.assertIn('01_运营共享/不存在的目录/旧表.md', dead_targets)
        self.assertNotIn('01_运营共享/[产品名]/买点.md', dead_targets)
        self.assertNotIn('02_公司经营/已删目录/旧文件.md', dead_targets)

        # 共享区敏感抽检
        sharing = report['checks']['sharing']
        self.assertTrue(sharing['enabled'])
        alert_types = {a['type'] for a in sharing['alerts']}
        self.assertIn('敏感文件名提示', alert_types)
        self.assertIn('疑似未打码手机号', alert_types)

        # 严格只读验证
        after = sorted(p.relative_to(self.kb).as_posix() for p in self.kb.rglob('*'))
        self.assertEqual(before, after)

    def test_only_flag_and_markdown_output(self):
        result = self.run_scan('--only', 'rules,sharing', '--format', 'markdown')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('多 Agent 协作说明', result.stdout)
        self.assertIn('共享区抽检', result.stdout)
        self.assertNotIn('目录与规模', result.stdout)

    def test_nonexistent_root_returns_nonzero(self):
        result = subprocess.run(
            [sys.executable, '-B', str(SCRIPT), str(self.kb / '不存在')],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
