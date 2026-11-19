"""S11 source-project identity, pair evidence and final-prompt review receipts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

TASK_RE = re.compile(r'(.+)-\d{2,}-(?:development|bugfix|optimization|migration|refactor)$')
ENTITY = re.compile(r'\b[A-Z][A-Z0-9_]+-\d+\b', re.ASCII)
REPLAY_ACTION = re.compile(r'(?:重复|再次)([\u4e00-\u9fff]{2,5}?)(?:时)?(?:仍)?返回(?:原(?:有|来)?|首次|已有)([\u4e00-\u9fff]{2})')


def bind_project(items, batch_dir: Path):
    """Attach identity only to task repositories actually in this batch manifest."""
    path = batch_dir / 's11-task-repos.json'
    if not path.is_file():
        return items
    manifest = json.loads(path.read_text(encoding='utf-8'))
    repos = {t['folder']: t.get('repo') for t in manifest.get('tasks', []) if t.get('folder')}
    for item in items:
        name = str(item.get('label', '')).removeprefix('历史:').split('[')[0]
        if repos.get(name):
            item['repo_id'] = repos[name]
            item['source_project'] = (manifest.get('source_dir') if manifest.get('source_mode') == 'directory_snapshot'
                                      else manifest.get('source_repo'))
    return items


def same_project(left, right):
    a, b = left.get('source_project'), right.get('source_project')
    if a and b:
        return a == b
    # Platform rows have only owner/task-repo. Match the complete S11 task naming scheme.
    a = TASK_RE.fullmatch(str(left.get('repo_id') or ''))
    b = TASK_RE.fullmatch(str(right.get('repo_id') or ''))
    return bool(a and b and '/' in a[1] and a[1] == b[1])


def pair_evidence(left: str, right: str, raw: dict) -> list[dict]:
    if left.strip() and left.strip() == right.strip():
        return [{'kind': 'same_project_identical', 'message': '同项目题面完全相同，换题位'}]
    ids = set(ENTITY.findall(left)) & set(ENTITY.findall(right))
    actions = set(REPLAY_ACTION.findall(left)) & set(REPLAY_ACTION.findall(right))
    # The platform's Chinese acceptance windows retain example numbers while removing
    # ASCII field prefixes (e.g. SINK-0002 and WIND-0002). Only inspect matching
    # windows around acceptance examples to avoid treating shared nouns as decisions.
    def acceptance_windows(text):
        normalized = re.sub(r'[^\u4e00-\u9fff0-9]', '', text)
        return {normalized[start:start + 40] for match in re.finditer('验收样例', normalized)
                for start in range(max(0, match.end() - 40), min(match.start(), len(normalized) - 40) + 1)}

    common = acceptance_windows(left) & acceptance_windows(right)
    if common:
        return [{'kind': 'same_project_acceptance_template', 'fragment': sorted(common)[0],
                 'message': '两题复用含业务约束和验收样例的同一窗口，重写验收场景'}]
    if ids and actions:
        return [{'kind': 'same_project_replay_result', 'entities': sorted(ids), 'actions': [list(action) for action in sorted(actions)],
                 'message': '同一实例、重复业务动作及复用原结果重合，请更换重复的业务题位'}]
    return [v for v in raw.get('violations', []) if v['kind'] in ('raw_a5_confirmed_fragment','raw_a5_platform_window')]


REVIEW_FIELDS = {
    'H1': ('trigger', 'wrong_result'),
    'H2': ('producer', 'consumer', 'downstream_result'),
    'H3': ('trigger', 'outcome_a', 'outcome_b', 'invariant', 'correctness_test'),
}


def final_review_errors(prompt: str, base_commit: str, review, *, axes=tuple(REVIEW_FIELDS)) -> list[str]:
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    if not re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', str(base_commit or '')):
        return ['独立复核必须绑定实际基准提交的完整 Git 摘要']
    if not isinstance(review, dict) or review.get('prompt_sha256') != digest or review.get('base_commit') != base_commit:
        return [f'重新独立复核最终正文与基线，填写 final_prompt_review，prompt_sha256={digest}，base_commit={base_commit}']
    errors = []
    for axis in axes:
        fields = REVIEW_FIELDS[axis]
        row = review.get(axis)
        if not isinstance(row, dict) or row.get('verdict') != 'supported':
            reason = str(row.get('reason') or '') if isinstance(row, dict) else ''
            errors.append(f'{axis} 未获独立证实，按实际缺项重写或替换题位，再复验三项。{reason}')
            continue
        quote = row.get('excerpt')
        if not isinstance(quote, str) or len(quote.strip()) < 4 or quote not in prompt:
            errors.append(f'{axis} 必须引用最终正文里的连续业务条件')
        if not all(isinstance(row.get(key), str) and row[key].strip() for key in fields):
            errors.append(f'{axis} 独立复核缺少业务依据 {", ".join(fields)}')
        elif axis == 'H3' and row['outcome_a'].strip() == row['outcome_b'].strip():
            errors.append('H3 在同一输入下得到相同终态，重新选择冲突轴')
        elif axis == 'H2' and row['producer'].strip() == row['consumer'].strip():
            errors.append('H2 上下游相同，回源找到独立消费者及业务结果')
    return errors
