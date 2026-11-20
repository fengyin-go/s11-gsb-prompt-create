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


def final_review_errors(prompt: str, base_commit: str, review, *, axes=tuple(REVIEW_FIELDS), require_h3_case=False, require_compliant=False, rule_version=None) -> list[str]:
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
        elif (axis == 'H3' and row['outcome_a'].strip() == row['outcome_b'].strip()
              and not (require_compliant and isinstance(row.get('case_review'), dict)
                       and row['case_review'].get('path') == 'engineering')):
            errors.append('H3 在同一输入下得到相同终态，重新选择冲突轴')
        elif axis == 'H2' and row['producer'].strip() == row['consumer'].strip():
            errors.append('H2 上下游相同，回源找到独立消费者及业务结果')
    if 'H3' in axes and (require_h3_case or require_compliant):
        h3 = review.get('H3')
        case = h3.get('case_review') if isinstance(h3, dict) else None
        if not isinstance(case, dict):
            errors.append('H3 缺少 case_review，从终稿独立推演一个能区分两种设计的实例')
        else:
            if rule_version == 3:
                if type(case.get('h3_rule_version')) is not int or case['h3_rule_version'] != rule_version:
                    errors.append('H3 case_review 须绑定本批 h3_rule_version=3')
                for field in ('decision_unit', 'decision_stage'):
                    if not isinstance(case.get(field), str) or not case[field].strip():
                        errors.append(f'H3 case_review.{field} 缺少约束作用域')
            for field in ('risk_excerpt', 'observation'):
                quote = case.get(field)
                if not isinstance(quote, str) or not quote.strip() or quote not in prompt:
                    errors.append(f'H3 case_review.{field} 须逐字引用终稿风险或实际观察场景')
            for field in ('basis_a', 'basis_b', 'naive_result'):
                if not isinstance(case.get(field), str) or not case[field].strip():
                    errors.append(f'H3 case_review.{field} 缺少具体观察或依据')
            if require_compliant:
                fields = ['simplest_compliant_solution', 'remaining_cost', 'decisive_reason']
                if case.get('path') == 'engineering':
                    fields += ['failure_window', 'responsibility_a', 'responsibility_b']
                    if str(case.get('responsibility_a')).strip() == str(case.get('responsibility_b')).strip():
                        errors.append('H3 工程路径缺少不同的窗口责任')
                elif case.get('path') != 'business':
                    errors.append('H3 路径应为 business 或 engineering')
                for field in fields:
                    if not isinstance(case.get(field), str) or not case[field].strip():
                        errors.append(f'H3 case_review.{field} 缺少独立推演')
                if case.get('simplest_compliant_solution') == case.get('naive_result'):
                    errors.append('H3 最简单合规做法须独立于错误做法的后果')
            if case.get('basis_a') and case.get('basis_a') == case.get('basis_b'):
                errors.append('H3 两条路径的适用依据或保护边界必须分别说明')
    return errors


def main() -> int:
    """Print only the final prompt and its binding for an independent review."""
    import argparse
    from preflight_batch import _load_candidates

    parser = argparse.ArgumentParser(description='从候选文件导出单题独立复核输入')
    parser.add_argument('--candidates-file', required=True, type=Path)
    parser.add_argument('--topic-map', required=True, type=Path)
    parser.add_argument('--folder', required=True)
    args = parser.parse_args()
    try:
        candidates = _load_candidates(args.candidates_file)
        topic = json.loads(args.topic_map.read_text(encoding='utf-8'))
        base_commit = topic.get('base_commit') if isinstance(topic, dict) else None
        if not isinstance(base_commit, str) or not re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', base_commit):
            raise ValueError('题位图缺少基线提交摘要')
        matches = [r for r in candidates if (r.get('folder') or r.get('子文件夹名称')) == args.folder]
        if len(matches) != 1:
            raise ValueError('候选文件中必须恰有一份该题定稿')
        prompt = matches[0].get('prompt') or matches[0].get('提示词')
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('题面为空')
        prompt = prompt.strip()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({'base_commit': base_commit, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                      **{k: topic[k] for k in ('h3_rule_version',) if k in topic}, 'prompt': prompt}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
