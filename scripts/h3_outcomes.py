"""Compare pre-production H3 decisions with explicit SOLO2 judgements (read only)."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

from h3_selection import LEDGER, digest
from github_helpers import normalize_repo_url


def moment(value, *, platform=False):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (AttributeError, ValueError):
        return None
    if stamp.tzinfo:
        return stamp
    if platform:
        return stamp.replace(tzinfo=timezone(timedelta(hours=8)))
    return None


def judgement(row, base, frozen_at, receipt, detail):
    if not detail:
        return 'missing_detail'
    if str(detail.get('id')) != str(receipt['submission_id']):
        return 'binding_mismatch'
    if detail.get('status') not in ('QC_PASSED', 'DISCARDED'):
        return 'pending'
    submitted = moment(detail.get('submitted_at'), platform=True)
    finished = moment(detail.get('qc_finished_at'), platform=True)
    frozen = moment(frozen_at)
    if not (frozen and submitted and finished and frozen < submitted <= finished):
        return 'unverified_time'
    session = (receipt.get('remote_data') or {}).get('session_id')
    if (not session or detail.get('session_id') != session or detail.get('env_snapshot') != f"{row['task_repo']}/commit/{base}"
            or not isinstance(detail.get('user_prompt'), str) or digest(detail['user_prompt']) != row['prompt_sha256']):
        return 'binding_mismatch'
    verdict = detail.get('verdict') or {}
    if verdict.get('manual') is True:
        return 'manual'
    checks = [c for c in detail.get('qc_checks') or [] if c.get('check_key') == 'trace_complexity_floor']
    if len(checks) != 1 or verdict.get('manual') is not False:
        return 'missing_h3'
    evidence = checks[0].get('evidence') or {}
    if evidence.get('exempted') or evidence.get('exempt_only') or evidence.get('skipped'):
        return 'exempt_or_skipped'
    if evidence.get('error') or evidence.get('exempted') is not False or evidence.get('skipped') is not False:
        return 'missing_h3'
    tradeoff = evidence.get('tradeoff') or {}
    summary = checks[0].get('summary') or ''
    if tradeoff.get('claimed') is True and 'H3 关键设计取舍不成立' not in summary and (
            tradeoff.get('evidence') or tradeoff.get('note') or 'H3 关键设计取舍成立' in summary):
        return 'supported'
    if tradeoff.get('claimed') is False and ('H3 关键设计取舍不成立' in summary or tradeoff.get('rejected')):
        return 'unsupported'
    return 'missing_h3'


def summarize(batches, lookup):
    groups = defaultdict(Counter)
    release_by_rule = defaultdict(Counter)
    untracked, outcomes, candidates, seen, projects = [], [], [], set(), set()
    for batch in sorted({p.resolve() for p in batches}):
        if not batch.is_dir():
            raise ValueError(f'批次目录不存在 {batch}')
        path = batch / LEDGER
        if not path.exists():
            manifest = batch / 's11-task-repos.json'
            if manifest.is_file() and json.loads(manifest.read_text()).get('h3_selection_version'):
                raise ValueError(f'新批次缺少冻结记录 {path}')
            untracked.append(str(batch))
            continue
        ledger = json.loads(path.read_text())
        rule = ledger.get('h3_rule_version')
        project = normalize_repo_url(ledger['project']) if 'github.com' in ledger['project'] else ledger['project']
        selection, release, held = {}, None, set()
        for revision in ledger['revisions']:
            if revision['stage'] == 'selection':
                selection = {r['opportunity_id']: r for r in revision['records']}
                held.update(k for k, r in selection.items() if r['decision'] == 'needs_evidence')
            elif revision['stage'] == 'release':
                release = revision
        for key, row in selection.items():
            counts = groups[project, row['task_type']]
            counts[row['decision']] += 1
            candidates.append(dict(batch=batch.name, h3_rule_version=rule, project=project, opportunity_id=key, task_type=row['task_type'],
                                   decision=row['decision'], reason=row['reason'], previously_held=key in held))
            if key in held and row['decision'] != 'needs_evidence':
                counts['needs_evidence_to_' + row['decision']] += 1
        for row in (release or {}).get('records', []):
            counts = groups[project, row['task_type']]
            counts['released'] += 1
            receipt_path = batch / row['task_id'] / 'review/upload.json'
            receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
            key = receipt.get('account'), str(receipt.get('submission_id') or '')
            if not key[1]:
                status = 'not_submitted'
            elif not key[0]:
                status = 'binding_mismatch'
            elif key in seen:
                status = 'duplicate_submission'
            else:
                status = judgement(row, ledger['base_commit'], release['frozen_at'], receipt, lookup(*key))
                if status in ('supported', 'unsupported'):
                    seen.add(key)
                    projects.add(project)
            counts[status] += 1
            release_by_rule[str(rule) if rule is not None else 'legacy'][status] += 1
            outcomes.append(dict(batch=batch.name, h3_rule_version=rule, task=row['task_id'], account=key[0], submission_id=key[1], result=status))
    def metrics(counts):
        n = counts['supported'] + counts['unsupported']
        return dict(counts=counts, false_admissions=counts['unsupported'], judged=n,
                    false_admission_rate=counts['unsupported'] / n if n else None)
    total = sum(groups.values(), Counter())
    by_project, by_type = defaultdict(Counter), defaultdict(Counter)
    for (project, kind), counts in groups.items():
        by_project[project].update(counts)
        by_type[kind].update(counts)
    return dict(overall=metrics(total), released_by_h3_rule={k: metrics(c) for k, c in release_by_rule.items()},
                by_project_type=[dict(project=p, task_type=t, **metrics(c))
                for (p, t), c in sorted(groups.items())], judged_projects=len(projects),
                by_project={k: metrics(c) for k, c in by_project.items()},
                by_type={k: metrics(c) for k, c in by_type.items()}, candidates=candidates,
                review_due=len(projects) >= 2 and len(seen) >= 20, untracked_batches=untracked, outcomes=outcomes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-dir', action='append', required=True, type=Path)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--live', action='store_true', help='按回执账号只读获取平台详情')
    source.add_argument('--details', type=Path, help='JSON 数组，每项为 {account, detail} 原始单条详情')
    args = parser.parse_args()
    saved, clients = {}, {}
    if args.details:
        for item in json.loads(args.details.read_text()):
            detail = item['detail']
            key = item['account'], str(detail['id'])
            if key in saved:
                raise ValueError(f'详情文件含重复提交 {key}')
            saved[key] = detail
    if args.live:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 's11-feedback-run/scripts'))
        import fbr_platform

    def lookup(account, ident):
        if args.live:
            if account not in clients:
                clients[account] = fbr_platform.Client(account=account)
            return clients[account].detail(ident)
        return saved.get((account, ident))
    print(json.dumps(summarize(args.batch_dir, lookup), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
