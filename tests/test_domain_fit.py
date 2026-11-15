"""Domain contract checks for generated S11 prompts."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import domain_fit


class DomainFit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s11-domain-fit-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / "src").mkdir()
        (self.repo / "src/orders.py").write_text(
            "def export_orders(records):\n"
            "    # 解析订单数据并生成订单明细文件\n"
            "    return records\n",
            encoding="utf-8",
        )
        self.map = {
            "domain": "数据工程",
            "subdirection": "数据分析与数据应用",
            "topics": [{
                "id": "orders",
                "name": "订单数据集",
                "keywords": ["订单", "数据集"],
                "evidence": ["src/orders.py"],
                "domain_objects": ["订单", "数据集"],
                "domain_results": ["导出文件", "字段完整"],
            }],
            "task_plan": [{
                "sequence": 1,
                "slug": "development",
                "task_type": "功能开发",
                "topic": "orders",
                "domain_fit": {
                    "version": 2,
                    "main_task": "解析订单数据并生成订单明细文件",
                    "business_result": "订单明细文件字段完整",
                    "domain_chain": {
                        "input": "订单数据",
                        "operation": "解析订单数据",
                        "output": "订单明细文件",
                        "observable": "导出文件字段完整",
                    },
                    "prompt_main_request": "请解析订单数据并导出订单明细文件，保证导出文件字段完整。",
                    "genericization": {
                        "rewritten_with_generic_objects": "升级构建工具并调整路由配置",
                        "same_task_without_domain_semantics": False,
                    },
                    "source_evidence": ["src/orders.py"],
                    "domain_anchors": [
                        {
                            "path": "src/orders.py",
                            "start_line": 2,
                            "end_line": 2,
                            "supports": "operation",
                            "excerpt": "解析订单数据并生成订单明细文件",
                        },
                        {
                            "path": "src/orders.py",
                            "start_line": 2,
                            "end_line": 2,
                            "supports": "output",
                            "excerpt": "生成订单明细文件",
                        },
                    ],
                    "conclusion": "pass",
                },
            }],
        }
        self.records = [{
            "子文件夹名称": "cc10003-01-development",
            "任务类型": "功能开发",
            "提示词": "请解析订单数据并导出订单明细文件，保证导出文件字段完整。订单数据集导出后要能在列表中定位。",
        }]

    def test_valid_contract_and_prompt(self):
        self.assertEqual(domain_fit.map_errors(self.map, self.repo), [])
        self.assertEqual(domain_fit.prompt_errors(self.records, self.map), [])

    def test_prompt_must_keep_domain_object_and_result(self):
        self.records[0]["提示词"] = "升级构建工具和路由配置。"
        errors = domain_fit.prompt_errors(self.records, self.map)
        self.assertTrue(any("领域对象" in error for error in errors))
        self.assertTrue(any("领域结果" in error for error in errors))

    def test_source_evidence_must_exist(self):
        self.map["task_plan"][0]["domain_fit"]["source_evidence"] = ["src/missing.py"]
        self.assertTrue(any("不存在" in error for error in domain_fit.map_errors(self.map, self.repo)))

    def test_source_anchor_excerpt_must_match_declared_lines(self):
        self.map["task_plan"][0]["domain_fit"]["domain_anchors"][0]["excerpt"] = "并不存在的数据处理逻辑"
        errors = domain_fit.map_errors(self.map, self.repo)
        self.assertTrue(any("excerpt 不在声明的源码行范围内" in error for error in errors))

    def test_domain_anchor_must_belong_to_the_planned_topic(self):
        (self.repo / "src/other.py").write_text("def unrelated():\n    return None\n", encoding="utf-8")
        self.map["topics"][0]["evidence"] = ["src/other.py"]
        errors = domain_fit.map_errors(self.map, self.repo)
        self.assertTrue(any("与 topic evidence 没有交集" in error for error in errors))

    def test_generic_file_or_snapshot_is_not_a_domain_artifact(self):
        fit = self.map["task_plan"][0]["domain_fit"]
        fit["domain_chain"].update(input="输入文件", output="处理快照")
        fit["prompt_main_request"] = (
            "请读取输入文件，解析订单数据并生成处理快照，保证导出文件字段完整。"
        )
        self.records[0]["提示词"] = (
            fit["prompt_main_request"] + "输入文件、处理快照和导出文件字段完整。"
        )
        errors = domain_fit.map_errors(self.map, self.repo)
        self.assertTrue(any("泛化的“文件/快照”" in error for error in errors))

    def test_prompt_must_keep_the_complete_domain_chain(self):
        self.records[0]["提示词"] = (
            "订单数据集导出的列表需要增加筛选。"
            "请解析订单数据并导出订单明细文件。"
        )
        errors = domain_fit.prompt_errors(self.records, self.map)
        self.assertTrue(any("domain_chain.observable" in error for error in errors))

    def test_domain_main_request_cannot_be_a_trailing_decorative_sentence(self):
        self.records[0]["提示词"] = (
            "先调整路由灰度、权限校验、发布流水线和依赖锁，再补齐服务注册、实例健康和并发版本控制。"
            "继续处理缓存键、故障转移、配置回滚和构建门禁，按批次核对每个服务的发布结果、"
            "健康状态、冲突优先级和恢复位置，确保所有入口的展示结果一致并且可以重复演练。"
            "请解析订单数据并导出订单明细文件，保证导出文件字段完整。"
        )
        errors = domain_fit.prompt_errors(self.records, self.map)
        self.assertTrue(any("必须在前" in error for error in errors))

    def test_legacy_name_only_contract_is_rejected(self):
        fit = self.map["task_plan"][0]["domain_fit"]
        for key in ("version", "domain_chain", "prompt_main_request", "genericization", "domain_anchors"):
            fit.pop(key)
        fit["main_task"] = "优化路由灰度、权限配额和发布流水线"
        errors = domain_fit.map_errors(self.map, self.repo)
        self.assertTrue(any("domain_fit.version" in error for error in errors))
        self.assertTrue(any("domain_chain" in error for error in errors))
        self.assertTrue(any("genericization" in error for error in errors))

    def test_platform_domain_mismatch_batch_is_rejected_even_with_topic_words(self):
        rejected_prompts = [
            "ROUT-0002 的灰度权重与 ROUT-0003 的降级目标由多个入口计算。请把路由匹配逻辑收拢到共用内核。",
            "LIMI-0001 草稿状态执行触发降级会生成回执。请从限流规则动作沿降级回执到路由分发排查并修复。",
            "接入网关配置目前只保存在草稿字段里。请增加网关配置部署预检，检查端口冲突、协议兼容和并发上限。",
            "WHIT-0002 与另一个来源同时申请最后一个日配额上限。请按来源归属与权限重新裁决。",
            "BALA-0002 的两批权重配置同时切换。请按服务列表拆分灰度阶段并执行发布流水线上线切换。",
        ]
        for prompt in rejected_prompts:
            self.records[0]["提示词"] = prompt
            errors = domain_fit.prompt_errors(self.records, self.map)
            self.assertTrue(any("prompt_main_request" in error for error in errors))
            self.assertTrue(any("domain_chain.input" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
