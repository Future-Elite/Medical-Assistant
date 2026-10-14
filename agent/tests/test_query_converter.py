from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from medrax.query_converter import QwenQueryConverter


class QueryConverterTest(unittest.TestCase):
    def test_english_bypasses_api_and_chinese_query_is_cached(self):
        converter = QwenQueryConverter()
        model = Mock()
        model.invoke.return_value = SimpleNamespace(content='{"concepts":[["asthma"],["treatment"]]}')
        converter.client._get_model = Mock(return_value=model)
        self.assertEqual(converter.convert("asthma treatment"), "asthma treatment")
        converter.client._get_model.assert_not_called()
        self.assertEqual(converter.convert("哮喘治疗"), '("asthma") AND ("treatment")')
        converter.convert("哮喘治疗")
        model.invoke.assert_called_once()

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
