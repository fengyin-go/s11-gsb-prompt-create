"""S11 task labels, integer allocation and topic-plan validation."""
from collections import Counter

TYPE_BY_SLUG = {
    "development": "功能开发", "bugfix": "Bugfix", "optimization": "功能优化",
    "refactor": "代码重构", "migration": "依赖升级与迁移",
}
TYPE_WEIGHTS = dict(zip(TYPE_BY_SLUG.values(), (30, 30, 30, 5, 5)))
ALIASES = {**{k.lower(): v for k, v in TYPE_BY_SLUG.items()},
           **{v.lower(): v for v in TYPE_BY_SLUG.values()}}


def canonical_type(raw):
    return ALIASES.get(str(raw).strip().lower())


def allocate_counts(total):
    """Largest remainders; ties use development, bugfix, optimization, refactor, migration."""
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("任务总数必须为非负整数")
    counts = {name: total * weight // 100 for name, weight in TYPE_WEIGHTS.items()}
    order = sorted(TYPE_WEIGHTS, key=lambda name: -(total * TYPE_WEIGHTS[name] % 100))
    for name in order[:total - sum(counts.values())]:
        counts[name] += 1
    return counts


def validate_type_counts(counts, total, *, policy="capacity-first"):
    """Ratios are advisory by default; explicit strict-ratio remains available."""
    if policy not in {"capacity-first", "strict-ratio"}:
        raise ValueError(f"未知题型策略: {policy}")
    if set(counts) - set(TYPE_WEIGHTS) or sum(counts.values()) != total:
        raise ValueError("题型计数包含未知类型或总数不一致")
    actual = {name: counts.get(name, 0) for name in TYPE_WEIGHTS}
    if policy == "strict-ratio" and actual != allocate_counts(total):
        raise ValueError(f"题型配比不一致: 实际 {actual}; 应为 {allocate_counts(total)}")
    return actual


def validate_plan(topic_map):
    plan = topic_map.get("task_plan", [])
    if not plan:
        raise ValueError("task_plan 不能为空；先按真实容量安排业务题位")
    counts = Counter()
    seats = Counter()
    families = set()
    topics = {t["id"]: t for t in topic_map["topics"]}
    for item in plan:
        expected = TYPE_BY_SLUG.get(item["slug"])
        if not expected or item["task_type"] != expected:
            raise ValueError(f"sequence={item['sequence']} 的 slug/task_type 不属于 S11 五类或不一致")
        topic = topics[item["topic"]]
        if not item.get("family") or not item.get("angle"):
            raise ValueError("每个题位必须有 family 与 angle")
        if topic.get("allowed_families") and item["family"] not in topic["allowed_families"]:
            raise ValueError(f"{item['topic']} 不允许 family={item['family']}")
        key = (item["topic"], item["family"])
        if key in families:
            raise ValueError(f"同一 topic 的题位必须跨家族: {key}")
        families.add(key)
        seats[item["topic"]] += 1
        if seats[item["topic"]] > topic["seat_capacity"]:
            raise ValueError(f"topic {item['topic']} 超过 seat_capacity")
        counts[expected] += 1
    return validate_type_counts(counts, len(plan), policy=topic_map.get("type_policy", "capacity-first"))
