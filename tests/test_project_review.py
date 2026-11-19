import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_cross_project_prompts as cross
import project_review as review


class ProjectReviewTests(unittest.TestCase):
    def test_project_boundary_and_business_outcome(self):
        left = {'repo_id': 'team/demo-01-bugfix', 'prompt': 'FLOW-02 重复归档返回原事件。'}
        right = {'repo_id': 'team/demo-18-refactor', 'prompt': 'FLOW-02 允许重复归档返回首次事件号。'}
        self.assertFalse(cross.compare_platform_prompt(left, right)['ok'])
        right['prompt'] = 'FLOW-02 重复归档返回原清单。'
        self.assertTrue(cross.compare_platform_prompt(left, right)['ok'])
        right['repo_id'] = 'other/demo-18-refactor'
        self.assertEqual(cross.compare_platform_prompt(left, right),
                         cross.compare_platform_prompt({'prompt': left['prompt']}, {'prompt': right['prompt']}))

    def test_independent_review_and_staleness(self):
        prompt = '旧批次按原费率扣费，新费率生效后可按确认日结算或按执行日结算。'
        receipt = dict(base_commit='a' * 40, prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
        for axis, fields in {'H1': {'trigger': '费率切换', 'wrong_result': '重复按新费率扣款'},
                             'H2': {'producer': '计价', 'consumer': '对账', 'downstream_result': '结算金额改变'},
                             'H3': {'trigger': '跨日调费', 'outcome_a': '确认日费率', 'outcome_b': '执行日费率',
                                    'invariant': '一个批次只结算一次', 'correctness_test': '跨日确认后用冻结费率复核账单'}}.items():
            receipt[axis] = {'verdict': 'supported', 'excerpt': prompt[:9], **fields}
        self.assertFalse(review.final_review_errors(prompt, 'a' * 40, receipt))
        self.assertTrue(review.final_review_errors(prompt + '更改', 'a' * 40, receipt))
        receipt['H3']['outcome_a'] = receipt['H3']['outcome_b']
        self.assertTrue(review.final_review_errors(prompt, 'a' * 40, receipt))

    def test_weak_wording_can_use_bound_review_but_not_stale_review(self):
        import json
        import tempfile
        import check_prompt_g16 as g16

        prompt = '旧任务缺少种子时可沿用原批次或生成新版本，数据准备流水线从已确认的配置读取采样结果。'
        name = 'sample-01-bugfix'
        base = 'a' * 40
        card = {'base_commit': base, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
        for axis, fields in {
            'H1': {'trigger': '恢复采样任务', 'wrong_result': '同一批生成两份样本'},
            'H2': {'producer': '采样登记', 'consumer': '数据准备流水线', 'downstream_result': '依据确认的种子计算样本'},
            'H3': {'trigger': '旧批次缺种子', 'outcome_a': '从历史输出回填种子', 'outcome_b': '另建待确认版本',
                   'invariant': '旧结果可复查', 'correctness_test': '重复恢复同一批后核对已有样本与种子'},
        }.items():
            card[axis] = {'verdict': 'supported', 'excerpt': prompt[:10], **fields}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'final-prompt-reviews'
            folder.mkdir()
            (root / 's11-task-repos.json').write_text(json.dumps({'base_commit': base}))
            (folder / (name + '.json')).write_text(json.dumps(card))
            self.assertFalse(g16.check([(name, 'Bugfix', prompt)])['ok'])
            self.assertTrue(g16.check([(name, 'Bugfix', prompt)], batch_dir=root)['ok'])
            self.assertFalse(g16.check([(name, 'Bugfix', prompt + '已更改')], batch_dir=root)['ok'])

    def test_strict_module_hint_uses_only_matching_evidence(self):
        import json
        import tempfile
        from unittest.mock import patch
        import check_prompt_g16 as g16

        prompt = '采集水位迁移后，数据准备流水线按新版本的起点继续读取。'
        card = {'base_commit': 'a' * 40, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                'H2': {'verdict': 'supported', 'excerpt': '按新版本的起点继续读取', 'producer': '采集迁移',
                       'consumer': '数据准备流水线', 'downstream_result': '从已迁移的水位继续取数'}}
        items = [('sample-01-migration', '依赖升级与迁移', prompt)]
        verdict = g16.evaluate(prompt, items[0][1])
        verdict.update(g16_ok=True, platform_learned_blocks=[], g19_hard_block=False, hints=['只读到一个功能模块'])
        with tempfile.TemporaryDirectory() as directory, patch.object(g16, 'evaluate', return_value=verdict):
            root = Path(directory)
            folder = root / 'final-prompt-reviews'
            folder.mkdir()
            (folder / (items[0][0] + '.json')).write_text(json.dumps(card))
            (root / 's11-task-repos.json').write_text(json.dumps({'base_commit': card['base_commit']}))
            self.assertTrue(g16.check(items, strict=True, batch_dir=root)['ok'])
            verdict['hints'].append('疑似不达 G19 中等下限')
            self.assertFalse(g16.check(items, strict=True, batch_dir=root)['ok'])
            verdict['g19_hard_block'] = True
            self.assertFalse(g16.check(items, batch_dir=root)['ok'])
