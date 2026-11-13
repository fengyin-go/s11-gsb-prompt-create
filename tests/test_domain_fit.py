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
        (self.repo / "src/orders.py").write_text("def export_orders():\n    return []\n")
        self.map = {
            "domain": "数据工程",
            "subdirection": "数据分析与数据应用",
            "topics": [{
                "id": "orders",
                "name": "订单数据集",
                "keywords": ["订单", "数据集"],
                "domain_objects": ["订单", "数据集"],
                "domain_results": ["导出文件", "字段完整"],
            }],
            "task_plan": [{
                "sequence": 1,
                "slug": "development",
                "task_type": "功能开发",
                "topic": "orders",
                "domain_fit": {
                    "main_task": "新增订单数据集导出",
                    "business_result": "导出文件字段完整",
                    "source_evidence": ["src/orders.py"],
                    "conclusion": "pass",
                },
            }],
        }
        self.records = [{
            "子文件夹名称": "cc10003-01-development",
            "任务类型": "功能开发",
            "提示词": "新增订单数据集导出，生成的导出文件要保证字段完整。",
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


if __name__ == "__main__":
    unittest.main()
