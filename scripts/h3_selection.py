"""Screen S11 opportunities and preserve time-stamped, versioned decisions before production."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

from s11_policy import TYPE_WEIGHTS

LEDGER = 'h3-decisions.json'
RULE_VERSION = 3
RULE_SHA256 = '9ea197eadaa7dbbed788a1f95ae63911891e5109c83ae2d69d90d87498a73ede'
DECISIONS = ('admit', 'needs_evidence', 'replace')


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def text(value):
    return isinstance(value, str) and bool(value.strip())


def rule_errors(topic, *, parent=None, manifest=None, new=False):
    version = topic.get('h3_rule_version')
    if 'h3_rule_version' in topic and (type(version) is not int or version != RULE_VERSION):
        return ['无法识别 h3_rule_version，V3 使用整数 3']
    if manifest and manifest.get('h3_rule_version') != version:
        return ['h3_rule_version 与已有批次清单不符']
    if parent and (parent / LEDGER).is_file():
        ledger = json.loads((parent / LEDGER).read_text())
        if ledger.get('h3_rule_version') != version:
            return ['h3_rule_version 与冻结选题记录不符']
        if version == RULE_VERSION and ledger.get('h3_rule_sha256') != RULE_SHA256:
            return ['H3 V3 选题台账的规则摘要不符']
        new = False  # A selection frozen before V3 can resume under its original rules.
    if new and version is None:
        return ['新批次须填写 h3_rule_version=3']
    if version == RULE_VERSION:
        rule = Path(__file__).resolve().parents[1] / 'references/h3-rule-v3.md'
        if hashlib.sha256(rule.read_bytes()).hexdigest() != RULE_SHA256:
            return ['H3 V3 规则正文与冻结摘要不符']
    return []


def selection_errors(topic, repo, *, required=False, check_plan=True, new=False):
    version = topic.get('h3_selection_version')
    issues = rule_errors(topic, new=new)
    if issues:
        return issues
    if version is None and not required and 'h3_rule_version' not in topic and not topic.get('h3_candidates'):
        return []
    review_version = topic.get('final_review_version')
    if type(version) is not int or version != 1 or type(review_version) is not int or review_version != 2:
        return ['新选题必须使用 h3_selection_version=1 与 final_review_version=2']
    rows = topic.get('h3_candidates')
    if not isinstance(rows, list) or not rows:
        return ['缺少 H3 候选清单 h3_candidates']
    from difficulty_contract import _source_region
    errors, candidates = [], {}
    for row in rows:
        if not isinstance(row, dict) or not text(row.get('opportunity_id')):
            errors.append('候选缺少 opportunity_id')
            continue
        key = row['opportunity_id']
        if key in candidates:
            errors.append(f'重复候选 {key}')
        candidates[key] = row
        decision = row.get('decision')
        if decision not in DECISIONS or not text(row.get('task_type')) or row['task_type'] not in TYPE_WEIGHTS:
            errors.append(f'{key} 候选结论或题型无效')
        fields = ['reason', 'trigger_input']
        if decision == 'admit':
            fields += ['user_action', 'remaining_choice']
            if topic.get('h3_rule_version') == RULE_VERSION:
                fields += ['decision_unit', 'decision_stage']
            anchor = row.get('source_anchor')
            if not isinstance(anchor, dict) or not text(anchor.get('excerpt')):
                errors.append(f'{key} 准入缺少 source_anchor')
            else:
                errors.extend(_source_region(repo, anchor, key)[0])
        for field in fields:
            if not text(row.get(field)):
                errors.append(f'{key} 缺少 {field}')
        if 'reviewed_prompt' in row and not text(row['reviewed_prompt']):
            errors.append(f'{key} 终稿待核或换位须保留本次正文')
    if check_plan:
        errors.extend(plan_selection_errors(topic.get('task_plan', []), candidates))
    return errors


def plan_selection_errors(plans, candidates):
    """Only admitted candidates of the bound type can occupy a formal seat."""
    errors = []
    used = set()
    if not isinstance(plans, list):
        return ['正式题位 task_plan 必须为数组']
    for plan in plans:
        key = plan.get('opportunity_id') if isinstance(plan, dict) else None
        row = candidates.get(key) if text(key) else None
        if not row or row.get('decision') != 'admit' or plan.get('task_type') != row.get('task_type') or key in used:
            errors.append(f'正式题位必须唯一绑定同题型准入候选：{key}')
        if text(key):
            used.add(key)
    return errors


def requires_screening(parent, manifest=None):
    return (parent / LEDGER).is_file() or bool((manifest or {}).get('h3_selection_version'))


def freeze(parent, project, base, stage, records, *, expected_selection=None, rule_version=None):
    """Append only changed snapshots under lock. Publish the entire revision atomically."""
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / LEDGER
    with (parent / '.h3-decisions.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text()) if path.exists() else {
            'version': 1, 'project': project, 'base_commit': base, 'revisions': []}
        if path.exists() and data.get('h3_rule_version') != rule_version:
            raise ValueError('h3_rule_version 与冻结记录不符')
        if path.exists() and rule_version is not None and data.get('h3_rule_sha256') != RULE_SHA256:
            raise ValueError('H3 V3 选题台账的规则摘要不符')
        if rule_version is not None:
            data['h3_rule_version'] = rule_version
            data['h3_rule_sha256'] = RULE_SHA256
        if (data['project'], data['base_commit']) != (project, base):
            raise ValueError('H3 冻结记录与当前项目或基线不符')
        previous = next((r for r in reversed(data['revisions']) if r['stage'] == stage), None)
        binding = {}
        if expected_selection is not None:
            selected = next((r for r in reversed(data['revisions']) if r['stage'] == 'selection'), None)
            if not selected or selected['records'] != expected_selection:
                raise ValueError('当前候选与冻结版本不符，请先用 h3_selection.py 记录补证或换位')
            binding['selection_frozen_at'] = selected['frozen_at']
        if previous and previous['records'] == records and all(previous.get(k) == v for k, v in binding.items()):
            return
        protected = {p.parent.name for p in parent.glob('*/state.json')}
        protected.update(p.parents[1].name for p in parent.glob('*/review/upload.json'))
        releases = [r for rev in data['revisions'] if rev['stage'] == 'release' for r in rev['records']]
        if protected and (not previous or stage == 'release'):
            raise ValueError('已有运行或提交记录，无法追补 H3 判断')
        if protected:
            old = {r['opportunity_id']: r for r in previous['records']}
            current = {r['opportunity_id']: r for r in records}
            if any(old.get(r['opportunity_id']) != current.get(r['opportunity_id'])
                   for r in releases if r['task_id'] in protected):
                raise ValueError('已运行题位的 H3 判断已冻结')
        if not path.exists() and (parent / 'batch-manifest.json').exists():
            raise ValueError('历史交接批次无法追补 H3 判断')
        if stage == 'selection' and previous:
            old_ids = {r['opportunity_id'] for r in previous['records']}
            if old_ids - {r['opportunity_id'] for r in records}:
                raise ValueError('选题历史中的候选须保留，撤下时登记 replace')
        data['revisions'].append({'stage': stage, 'frozen_at': datetime.now(timezone.utc).isoformat(),
                                  'records': records, **binding})
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=parent, delete=False) as out:
            tmp = Path(out.name)
            try:
                json.dump(data, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
        try:
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def selection_records(topic):
    records = []
    for item in topic['h3_candidates']:
        row = dict(item)
        if 'reviewed_prompt' in row:
            row['prompt_sha256'] = digest(row.pop('reviewed_prompt'))
        records.append(row)
    return sorted(records, key=lambda r: r['opportunity_id'])


def freeze_selection(parent, project, topic):
    freeze(parent, project, topic['base_commit'], 'selection', selection_records(topic),
           rule_version=topic.get('h3_rule_version'))


def freeze_final(parent, manifest, topic, staged):
    project = manifest.get('source_dir') or manifest['source_repo']
    if not (parent / LEDGER).is_file():
        raise ValueError('缺少建仓前的 H3 选题记录')
    plans = {p['sequence']: p for p in topic['task_plan']}
    records = []
    for target, record, _ in staged:
        plan = plans[record['sequence']]
        review = plan.get('final_prompt_review') or {}
        sha = digest(record['prompt'])
        if review.get('prompt_sha256') != sha or review.get('base_commit') != manifest['base_commit']:
            raise ValueError(f'{target.name} 终稿推演与交接正文或基线不符')
        if topic.get('h3_rule_version') == RULE_VERSION and review['H3']['case_review'].get('h3_rule_version') != RULE_VERSION:
            raise ValueError(f'{target.name} 终稿复核缺少冻结的 H3 规则版本')
        records.append({'opportunity_id': plan['opportunity_id'], 'task_id': target.name,
                        'task_type': record['task_type'], 'prompt_sha256': sha,
                        'task_repo': record['publish']['repo_url'], 'case_review': review['H3']['case_review']})
    freeze(parent, project, manifest['base_commit'], 'release', records, expected_selection=selection_records(topic),
           rule_version=topic.get('h3_rule_version'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-dir', type=Path, required=True)
    parser.add_argument('--topic-map', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--snapshot-manifest', type=Path)
    args = parser.parse_args()
    topic = json.loads(args.topic_map.read_text())
    from create_batch_tasks import git, hub, source_snapshot
    if git(args.repo, 'rev-parse', 'HEAD') != topic.get('base_commit') or git(args.repo, 'status', '--porcelain'):
        raise ValueError('选题须绑定当前干净源码提交')
    errors = rule_errors(topic, parent=args.batch_dir, new=True)
    errors += selection_errors(topic, args.repo, required=True, check_plan=False)
    if errors:
        raise ValueError('；'.join(errors))
    project = (source_snapshot.validate_snapshot(args.repo, args.snapshot_manifest)['source_dir']
               if args.snapshot_manifest else hub.source_remote_url(args.repo))
    freeze_selection(args.batch_dir, project, topic)
    print(args.batch_dir / LEDGER)


if __name__ == '__main__':
    main()
