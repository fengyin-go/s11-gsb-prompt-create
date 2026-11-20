"""Pre-production H3 cohorts must stay distinct from retrospective calibration."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import h3_outcomes as audit
import h3_selection as screen


BASE = 'c' * 40
FROZEN = '2026-11-20T00:00:00+00:00'


def submission(ident, claimed):
    name = f'demo-{ident:02d}-bugfix'
    prompt = f'核对批次 {ident} 内更正前后的有效订单。'
    row = dict(opportunity_id=name, task_id=name, task_repo='https://github.com/test/' + name,
               task_type='Bugfix', prompt_sha256=screen.digest(prompt))
    receipt = dict(account='local-test', submission_id=ident, remote_data={'session_id': f'session-{ident}'})
    detail = dict(id=ident, session_id=f'session-{ident}', status='DISCARDED', qc_finished_at='2026-11-20T08:02:00',
                  submitted_at='2026-11-20T08:01:00', env_snapshot=f"{row['task_repo']}/commit/{BASE}",
                  user_prompt=prompt, verdict={'manual': False}, qc_checks=[{
                      'check_key': 'trace_complexity_floor', 'summary': 'H3 关键设计取舍' + ('成立' if claimed else '不成立'),
                      'evidence': {'exempted': False, 'skipped': False, 'tradeoff': {'claimed': claimed}}}])
    return row, receipt, detail


@pytest.mark.parametrize(('change', 'expected'), [
    ('manual', 'manual'), ('exempted', 'exempt_or_skipped'), ('skipped', 'exempt_or_skipped'),
    ('no_check', 'missing_h3'), ('empty_claim', 'missing_h3'), ('pending', 'pending'),
    ('late_freeze', 'unverified_time'), ('no_time', 'unverified_time'), ('sha', 'binding_mismatch'),
    ('session', 'binding_mismatch'), ('snapshot', 'binding_mismatch'), ('missing', 'missing_detail'),
])
def test_only_bound_explicit_terminal_judgements_enter_denominator(change, expected):
    row, receipt, d = submission(1, False)
    if change == 'manual':
        d['verdict']['manual'] = True
    elif change in ('exempted', 'skipped'):
        d['qc_checks'][0]['evidence'][change] = True
    elif change == 'no_check':
        d.update(status='QC_PASSED', qc_checks=[])
    elif change == 'empty_claim':
        d['qc_checks'][0]['summary'] = 'P5 仅检查 H1'
    elif change == 'pending':
        d['status'] = 'SUBMITTED'
    elif change == 'late_freeze':
        d['submitted_at'] = '2026-11-20T07:59:59'
    elif change == 'no_time':
        d['qc_finished_at'] = None
    elif change == 'sha':
        d['user_prompt'] += '新增假设'
    elif change == 'session':
        receipt['remote_data'].clear()
    elif change == 'snapshot':
        d['env_snapshot'] += '0'
    else:
        d = None
    assert audit.judgement(row, BASE, FROZEN, receipt, d) == expected


def test_freeze_preserves_changes_and_blocks_backfill(tmp_path):
    row = {'opportunity_id': 'orders', 'decision': 'needs_evidence', 'reviewed_prompt': '谁使用客户单表？'}
    topic = {'h3_candidates': [row], 'base_commit': BASE}
    screen.freeze_selection(tmp_path, 'demo', topic)
    path = tmp_path / screen.LEDGER
    original = path.read_bytes()
    screen.freeze_selection(tmp_path, 'demo', topic)
    assert path.read_bytes() == original
    row.update(decision='replace')
    with pytest.raises(ValueError, match='冻结版本'):
        screen.freeze(tmp_path, 'demo', BASE, 'release', [], expected_selection=screen.selection_records(topic))
    screen.freeze_selection(tmp_path, 'demo', topic)
    revisions = json.loads(path.read_text())['revisions']
    assert revisions[0] == json.loads(original)['revisions'][0]
    assert revisions[0]['records'][0]['prompt_sha256'] == screen.digest('谁使用客户单表？')
    with pytest.raises(ValueError, match='候选须保留'):
        screen.freeze_selection(tmp_path, 'demo', dict(topic, h3_candidates=[]))
    existing = tmp_path / 'historical'
    (existing / 'task/review').mkdir(parents=True)
    (existing / 'task/review/upload.json').write_text('{}')
    with pytest.raises(ValueError, match='追补'):
        screen.freeze_selection(existing, 'demo', topic)
    assert not (existing / screen.LEDGER).exists()


def test_review_threshold_uses_actual_judged_projects_and_submissions(tmp_path):
    details = {}
    def batch(name, project, ids):
        parent = tmp_path / name
        parent.mkdir()
        released, chosen = [], []
        for ident in ids:
            row, receipt, d = submission(ident, ident > 2)
            released.append(row)
            chosen.append(dict(opportunity_id=row['opportunity_id'], task_type='Bugfix', decision='admit', reason='影响有效样本'))
            p = parent / row['task_id'] / 'review'
            p.mkdir(parents=True)
            (p / 'upload.json').write_text(json.dumps(receipt))
            details[receipt['account'], str(ident)] = d
        hold = dict(opportunity_id='incomplete', task_type='Bugfix', decision='needs_evidence', reason='缺少单表消费者')
        revisions = [dict(stage='selection', frozen_at=FROZEN, records=[hold]),
                     dict(stage='selection', frozen_at=FROZEN, records=chosen + [dict(hold, decision='replace')]),
                     dict(stage='release', frozen_at=FROZEN, records=released)]
        (parent / screen.LEDGER).write_text(json.dumps(dict(project=project, base_commit=BASE, revisions=revisions)))
        return parent
    a = batch('batch-a', 'project-a', range(1, 21))
    b = batch('batch-b', 'project-b', [21])
    same = batch('copy', 'project-a', [1])
    old = tmp_path / 'old'
    old.mkdir()
    lookup = lambda *key: details.get(key)
    details['local-test', '21']['verdict']['manual'] = True
    report = audit.summarize([a, b, a, old, same], lookup)
    assert not report['review_due'] and report['judged_projects'] == 1
    assert report['overall']['false_admissions'] == 2 and report['overall']['judged'] == 20
    assert report['overall']['counts']['duplicate_submission'] == 1
    assert report['by_project']['project-b']['false_admission_rate'] is None
    assert report['untracked_batches'] == [str(old)]
    details['local-test', '21']['verdict']['manual'] = False
    report = audit.summarize([a, b], lookup)
    assert report['review_due'] and report['overall']['judged'] == 21
    assert report['by_type']['Bugfix']['counts']['needs_evidence_to_replace'] == 2
