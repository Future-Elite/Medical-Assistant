from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from medrax.query_converter import QwenQueryConverter


class QueryConverterTest(unittest.TestCase):
    def test_english_and_chinese_queries_use_cached_concept_groups(self):
        converter = QwenQueryConverter()
        model = Mock()
        model.invoke.return_value = SimpleNamespace(content='{"concepts":[["asthma"],["treatment"]]}')
        converter.client._get_model = Mock(return_value=model)
        self.assertEqual(converter.convert("asthma treatment"), '("asthma") AND ("treatment")')
        converter.convert("asthma treatment")
        self.assertEqual(model.invoke.call_count, 1)
        self.assertEqual(converter.convert("哮喘治疗"), '("asthma") AND ("treatment")')
        converter.convert("哮喘治疗")
        self.assertEqual(model.invoke.call_count, 2)

    def test_empty_conversion_is_rejected(self):
        converter = QwenQueryConverter()
        model = Mock()
        model.invoke.return_value = SimpleNamespace(content='{"concepts":[]}')
        converter.client._get_model = Mock(return_value=model)
        with self.assertRaises(ValueError):
            converter.convert("哮喘治疗")

    def test_synonyms_cannot_create_a_topic_free_or_route(self):
        converter = QwenQueryConverter()
        model = Mock()
        model.invoke.return_value = SimpleNamespace(content='{"concepts":[["asthma"],["treatment","therapy"]]}')
        converter.client._get_model = Mock(return_value=model)
        self.assertEqual(converter.convert("哮喘治疗"), '("asthma") AND ("treatment" OR "therapy")')
        model.invoke.return_value = SimpleNamespace(content='{"concepts":[["asthma OR guideline"]]}')
        with self.assertRaises(ValueError):
            converter.convert("另一条哮喘问题")
