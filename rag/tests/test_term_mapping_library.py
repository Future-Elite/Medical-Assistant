"""v6.7.0 术语映射库（通用机制）：加载校验、库行展开、请求优先、trace 溯源、构建器。

离线测试约束：不联网、不加载模型。映射库只做查询扩展，行 provenance
（origin/source/review_status）必须逐条可审计；默认不配置库时不得改变
请求级 term_mapping 的既有语义。
"""
import json
import tempfile
import unittest
from pathlib import Path

from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline
from rag.query import build_query_plan, mapping_active, primary_mapping_entry
from rag.term_mapping_library import TermMappingLibrary
from rag.tests.helpers import fixture_registry

# 研发侧构建器 tools/build_term_mapping_library.py 不随发布分支分发：
# 缺失时相关测试显式跳过，而不是把发布件报成失败。
BUILDER_PATH = Path(__file__).resolve().parents[2] / "tools" / "build_term_mapping_library.py"
BUILDER_AVAILABLE = BUILDER_PATH.exists()

SOURCES = [{"source_id": "pubmed-fixture", "kinds": ["pubmed"]}, {"source_id": "guideline-fixture", "kinds": ["guideline"]}]

NEONATAL_ROW = {
    "concept": "新生儿呼吸窘迫综合征",
    "aliases": ["NRDS"],
    "english": ["Neonatal Respiratory Distress Syndrome"],
    "mesh": ["Respiratory Distress Syndrome, Newborn"],
    "metadata": {"source": "dataset_labels:test.json", "review_status": "dataset_derived",
                 "mesh_review_status": "needs_review", "label_count": 10},
}


class LibraryLoaderTests(unittest.TestCase):
    def test_from_rows_and_describe(self):
        library = TermMappingLibrary.from_rows([NEONATAL_ROW], source="unit")
        self.assertEqual(library.source, "unit")
        self.assertEqual(len(library.entries), 1)
        self.assertEqual(library.describe()["concepts"], ["新生儿呼吸窘迫综合征"])
        self.assertEqual(library.describe()["mesh_status"], {"needs_review": 1})  # provenance 平铺可读

    def test_empty_and_duplicate_and_invalid_rows_rejected(self):
        with self.assertRaises(ValueError):
            TermMappingLibrary.from_rows([])
        with self.assertRaises(ValueError):
            TermMappingLibrary.from_rows([NEONATAL_ROW, dict(NEONATAL_ROW)])
        with self.assertRaises(ValueError):
            TermMappingLibrary.from_rows([{"concept": "x", "english": [], "mesh": []}])
        with self.assertRaises(ValueError):
            TermMappingLibrary.from_rows([{"english": ["only english"]}])

    def test_from_json_file_shapes_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "lib.json"
            wrapper.write_text(json.dumps({"library": "t", "rows": [NEONATAL_ROW]}, ensure_ascii=False), encoding="utf-8")
            library = TermMappingLibrary.from_json_file(wrapper)
            self.assertEqual(library.source, "t")
            bare = Path(tmp) / "bare.json"
            bare.write_text(json.dumps([NEONATAL_ROW], ensure_ascii=False), encoding="utf-8")
            self.assertEqual(TermMappingLibrary.from_json_file(bare).entries[0].concept, "新生儿呼吸窘迫综合征")
            with self.assertRaises(ValueError):
                TermMappingLibrary.from_json_file(Path(tmp) / "missing.json")
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                TermMappingLibrary.from_json_file(bad)


class LibraryExpansionTests(unittest.TestCase):
    def test_library_expands_without_request_mapping(self):
        library = TermMappingLibrary.from_rows([NEONATAL_ROW], source="unit")
        request = RetrievalRequest.from_dict({"question": "该患儿新生儿呼吸窘迫综合征的呼吸支持证据？"})
        plan = build_query_plan(request, SOURCES, library=library)
        self.assertIn("Neonatal Respiratory Distress Syndrome", plan.english_terms)
        self.assertIn("Respiratory Distress Syndrome, Newborn", plan.mesh_terms)
        entry = next(item for item in plan.mapping_trace if item.get("concept") == "新生儿呼吸窘迫综合征")
        self.assertEqual(entry["status"], "expanded")
        self.assertEqual(entry["source"], "term_mapping_library")
        self.assertEqual(entry["origin"], "library")
        self.assertTrue(mapping_active(plan))
        self.assertEqual(primary_mapping_entry(plan).concept, "新生儿呼吸窘迫综合征")

    def test_library_alias_surface_matches(self):
        library = TermMappingLibrary.from_rows([NEONATAL_ROW], source="unit")
        request = RetrievalRequest.from_dict({"question": "NRDS 患儿如何呼吸支持？"})
        plan = build_query_plan(request, SOURCES, library=library)
        entry = next(item for item in plan.mapping_trace if item.get("concept") == "新生儿呼吸窘迫综合征")
        self.assertEqual(entry["status"], "expanded")
        self.assertEqual(entry["matched_surface"], "NRDS")

    def test_request_row_wins_on_concept_collision(self):
        library = TermMappingLibrary.from_rows([NEONATAL_ROW], source="unit")
        request = RetrievalRequest.from_dict({
            "question": "该患儿新生儿呼吸窘迫综合征的证据？",
            "term_mapping": [{"concept": "新生儿呼吸窘迫综合征", "english": ["request english"], "mesh": []}],
        })
        plan = build_query_plan(request, SOURCES, library=library)
        self.assertIn("request english", plan.english_terms)
        self.assertNotIn("Neonatal Respiratory Distress Syndrome", plan.english_terms)
        origins = [item.get("origin", "request") for item in plan.mapping_trace if item.get("concept") == "新生儿呼吸窘迫综合征"]
        self.assertEqual(origins, ["request"])

    def test_unmatched_library_row_traced_not_silent(self):
        library = TermMappingLibrary.from_rows([NEONATAL_ROW], source="unit")
        request = RetrievalRequest.from_dict({"question": "该患儿湿肺如何处理？"})
        plan = build_query_plan(request, SOURCES, library=library)
        entry = next(item for item in plan.mapping_trace if item.get("concept") == "新生儿呼吸窘迫综合征")
        self.assertEqual(entry["status"], "not_matched")
        self.assertEqual(entry["origin"], "library")
        self.assertFalse(mapping_active(plan))

    def test_no_library_keeps_legacy_trace_shape(self):
        request = RetrievalRequest.from_dict({"question": "该患儿新生儿呼吸窘迫综合征的证据？"})
        plan = build_query_plan(request, SOURCES)
        self.assertEqual(plan.mapping_trace, ())
        self.assertEqual(plan.term_mapping, ())
        self.assertFalse(mapping_active(plan))

    def test_library_unmatched_rows_do_not_spam_findings(self):
        """库是词库：未命中是常态，只计数；请求行未命中才是键写错，要逐条列。"""
        from rag.query import review_query_plan

        library = TermMappingLibrary.from_rows([NEONATAL_ROW], source="unit")
        # 问题命中库概念：其余库行未命中不得进 findings
        plan = build_query_plan(
            RetrievalRequest.from_dict({"question": "该患儿新生儿呼吸窘迫综合征的证据？"}),
            SOURCES, library=library,
        )
        report = review_query_plan(plan, english_source_present=True)
        joined = " ".join(report["findings"])
        self.assertNotIn("未展开", joined)
        coverage = next(c for c in report["checks"] if c["item"] == "term_mapping_coverage")
        self.assertIn("库行未命中 0/1", coverage["detail"])
        # 请求行未命中仍逐条列出
        plan2 = build_query_plan(
            RetrievalRequest.from_dict({
                "question": "该患儿湿肺如何处理？",
                "term_mapping": [{"concept": "新生儿呼吸窘迫综合征", "english": ["respiratory distress syndrome"]},
                                 {"concept": "湿肺", "english": ["transient tachypnea"]}],
            }),
            SOURCES, library=library,
        )
        report2 = review_query_plan(plan2, english_source_present=True)
        self.assertTrue(any("未展开" in f and "新生儿呼吸窘迫综合征" in f for f in report2["findings"]))


class PipelineTraceTests(unittest.TestCase):
    def _pipeline(self, **kwargs):
        return EvidenceRetrievalPipeline(fixture_registry(), **kwargs)

    def test_library_summary_keys_only_when_library_active(self):
        library = TermMappingLibrary.from_rows([NEONATAL_ROW], source="unit")
        request = {"question": "该患儿新生儿呼吸窘迫综合征的指南推荐是什么？", "as_of": "2026-04-15",
                   "english_terms": ["respiratory distress syndrome"], "top_k": 3}
        with_library = self._pipeline(term_mapping_library=library).retrieve(RetrievalRequest.from_dict(request))
        summary = with_library.trace["query_plan"]["term_mapping"]
        self.assertEqual(summary["library_rows"], 1)
        self.assertEqual(summary["library_expanded"], 1)
        without = self._pipeline().retrieve(RetrievalRequest.from_dict(request))
        legacy_summary = without.trace["query_plan"]["term_mapping"]
        self.assertNotIn("library_rows", legacy_summary)
        self.assertNotIn("library_expanded", legacy_summary)

    def test_library_rows_reach_external_terms(self):
        library = TermMappingLibrary.from_rows([NEONATAL_ROW], source="unit")
        package = self._pipeline(term_mapping_library=library).retrieve(RetrievalRequest.from_dict(
            {"question": "NRDS 的最新证据？", "as_of": "2026-04-15", "top_k": 3}))
        self.assertIn("Neonatal Respiratory Distress Syndrome", package.trace["external_terms"])


@unittest.skipUnless(BUILDER_AVAILABLE, "研发侧构建器不随发布分支分发")
class BuilderExtractionTests(unittest.TestCase):
    def test_collect_from_rows_splits_combos_and_extracts_aliases(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "build_term_mapping_library",
            Path(__file__).resolve().parents[2] / "tools" / "build_term_mapping_library.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows = [
            {"disease_label": "新生儿呼吸窘迫综合征，新生儿肺炎",
             "disease_label_en": "Neonatal Respiratory Distress Syndrome (NRDS), Neonatal Pneumonia"},
            {"disease_label": "湿肺", "disease_label_en": "Neonatal Transient Tachypnea (TTN)"},
            {"disease_label": "气胸", "disease_label_en": "pneumothorax"},
            {"disease_label": "气胸", "disease_label_en": "Pneumothorax"},
            {"disease_label": "未见明显异常", "disease_label_en": "No obvious abnormalities"},
        ]
        out, stats = module.collect_from_rows(rows, source_name="unit.json", include_negative=False)
        concepts = {row["concept"] for row in out}
        self.assertEqual(concepts, {"新生儿呼吸窘迫综合征", "新生儿肺炎", "湿肺", "气胸"})
        rds = next(row for row in out if row["concept"] == "新生儿呼吸窘迫综合征")
        self.assertIn("NRDS", rds["aliases"])
        self.assertEqual(rds["english"], ["Neonatal Respiratory Distress Syndrome"])
        ttn = next(row for row in out if row["concept"] == "湿肺")
        self.assertIn("TTN", ttn["aliases"])
        self.assertIn("新生儿湿肺", ttn["aliases"])
        pt = next(row for row in out if row["concept"] == "气胸")
        self.assertEqual(pt["english"], ["Pneumothorax"])
        self.assertEqual(pt["metadata"]["mesh_review_status"], "needs_review")
        self.assertEqual(stats["rows"], 5)


@unittest.skipUnless(BUILDER_AVAILABLE, "研发侧构建器不随发布分支分发")
class LexiconMergeTests(unittest.TestCase):
    """词典扩展源：补别名/候选词、收表外概念；产出一律 needs_review。"""

    def _module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "build_term_mapping_library",
            Path(__file__).resolve().parents[2] / "tools" / "build_term_mapping_library.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_load_lexicon_shapes_and_errors(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "lex.json"
            good.write_text(json.dumps({"lexicon": "t", "entries": [
                {"concept": "新生儿肺炎", "aliases": ["肺炎"]},
                {"concept": "新生儿低血糖", "english": ["neonatal hypoglycemia"], "mesh": ["Hypoglycemia"]},
            ]}, ensure_ascii=False), encoding="utf-8")
            name, entries = module.load_lexicon(good)
            self.assertEqual(name, "t")
            self.assertEqual(len(entries), 2)
            bare = Path(tmp) / "bare.json"
            bare.write_text(json.dumps([{"concept": "湿肺", "aliases": ["TTN"]}], ensure_ascii=False), encoding="utf-8")
            self.assertEqual(module.load_lexicon(bare)[0], "bare.json")
            for bad_payload in (
                {"lexicon": "x", "entries": []},
                {"lexicon": "x", "entries": [{"concept": "", "aliases": ["a"]}]},
                {"lexicon": "x", "entries": [{"concept": "无词"}]},
            ):
                bad = Path(tmp) / "bad.json"
                bad.write_text(json.dumps(bad_payload, ensure_ascii=False), encoding="utf-8")
                with self.assertRaises(SystemExit):
                    module.load_lexicon(bad)
            with self.assertRaises(SystemExit):
                module.load_lexicon(Path(tmp) / "missing.json")

    def test_merge_supplements_and_creates_with_provenance(self):
        module = self._module()
        rows = [{
            "concept": "新生儿肺炎", "aliases": [], "english": ["Neonatal Pneumonia"], "mesh": ["Pneumonia"],
            "metadata": {"source": "dataset_labels:t.json", "review_status": "dataset_derived",
                         "mesh_review_status": "needs_review", "label_count": 100},
        }]
        name, entries = module.load_lexicon(Path(__file__).resolve().parents[1] / "contracts/examples/term_lexicon_neonatal_example.json")
        merged, stats = module.merge_lexicon(rows, name, entries)
        # 词表 13 行：新生儿肺炎已有→补充；气胸/胸腔积液/肺不张是纯别名行→补不了新行（跳过留痕）；其余 9 行新建
        self.assertEqual(stats["rows_created"], 9)
        self.assertEqual(stats["rows_supplemented"], 1)
        self.assertEqual(stats["rows_skipped_no_terms"], ["气胸", "胸腔积液", "肺不张"])
        pna = next(r for r in merged if r["concept"] == "新生儿肺炎")
        self.assertIn("肺炎", pna["aliases"])                     # 已知缺口修复
        self.assertIn("Neonatal Pneumonia", pna["english"])       # 数据词不被覆盖
        self.assertEqual(pna["metadata"]["label_count"], 100)     # 数据计数保留
        self.assertEqual(pna["metadata"]["lexicon_additions"]["aliases"], ["肺炎"])
        self.assertEqual(pna["metadata"]["review_status"], "dataset_derived")
        jaundice = next(r for r in merged if r["concept"] == "新生儿高胆红素血症")
        self.assertEqual(jaundice["metadata"]["source"], "lexicon:" + name)
        self.assertEqual(jaundice["metadata"]["review_status"], "needs_review")
        self.assertIn("黄疸", jaundice["aliases"])
        # 全部词表来源行都不得冒充金标准
        for row in merged:
            if row["metadata"].get("supplemented_by"):
                self.assertIn("needs_review", (row["metadata"]["review_status"], row["metadata"]["mesh_review_status"]))

    def test_lexicon_duplicates_are_deduped(self):
        module = self._module()
        rows = [{"concept": "湿肺", "aliases": ["TTN"], "english": ["Neonatal Transient Tachypnea"], "mesh": [],
                 "metadata": {"source": "dataset_labels:t", "review_status": "dataset_derived",
                              "mesh_review_status": "absent", "label_count": 5}}]
        merged, stats = module.merge_lexicon(rows, "t", [{"concept": "湿肺", "aliases": ["TTN", "新生儿湿肺"]}])
        self.assertEqual(stats["rows_supplemented"], 1)
        ttn = merged[0]
        self.assertEqual(ttn["aliases"].count("TTN"), 1)
        self.assertIn("新生儿湿肺", ttn["aliases"])
        self.assertEqual(ttn["metadata"]["lexicon_additions"], {"aliases": ["新生儿湿肺"]})

    def test_builder_integration_with_lexicon(self):
        import importlib.util

        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            (data_dir / "x.json").write_text(json.dumps([
                {"disease_label": "湿肺", "disease_label_en": "Neonatal Transient Tachypnea (TTN)"},
            ], ensure_ascii=False), encoding="utf-8")
            result = module.build(data_dir, lexicon_path=Path(__file__).resolve().parents[1] / "contracts/examples/term_lexicon_neonatal_example.json")
            concepts = {row["concept"] for row in result["rows"]}
            self.assertIn("新生儿高胆红素血症", concepts)      # 词表收进表外概念
            self.assertIn("湿肺", concepts)
            self.assertEqual(result["lexicon"]["lexicon"], "neonatal_term_lexicon_v1")
            # 出厂自检已通过（build 内部）；再确认库可加载
            TermMappingLibrary.from_rows(result["rows"], source=result["library"])


class ConfigDefaultTests(unittest.TestCase):
    def test_library_path_default_empty(self):
        from rag.config import RAGConfig

        self.assertEqual(RAGConfig().term_mapping_library_path, "")


@unittest.skipUnless(BUILDER_AVAILABLE, "研发侧构建器不随发布分支分发")
class MeshEnrichmentTests(unittest.TestCase):
    """MeSH 富化：接真实词库锚定（测试注入假 fetch，不联网）。"""

    def _module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "build_term_mapping_library",
            Path(__file__).resolve().parents[2] / "tools" / "build_term_mapping_library.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    _ESEARCH_HITS = json.dumps({"esearchresult": {"idlist": ["6037", "6038"]}}).encode()
    _ESUMMARY = json.dumps({"result": {
        "uids": ["6037", "6038"],
        "6037": {"uid": "6037", "ds_recordtype": "descriptor", "ds_meshui": "D012189",
                 "ds_meshterms": ["Respiratory Distress Syndrome, Newborn", "NRDS",
                                  "Neonatal Respiratory Distress Syndrome"],
                 "ds_scopenote": "A condition of the newborn infant."},
        "6038": {"uid": "6038", "ds_recordtype": "descriptor", "ds_meshui": "D012132",
                 "ds_meshterms": ["Respiratory Distress Syndrome"],
                 "ds_scopenote": ""},
    }}).encode()

    def _fetch(self, url, params):
        """模拟真实语义："[标签]"[Mesh] 仅精确命中已知标签；普通检索返回候选。"""
        if "esearch" in url:
            term = str(params.get("term", ""))
            if "[Mesh]" in term:
                if "Neonatal Respiratory Distress Syndrome" in term:
                    return self._ESEARCH_HITS
                return b'{"esearchresult": {"idlist": []}}'
            return self._ESEARCH_HITS
        return self._ESUMMARY


    def _rows(self, mesh=None):
        return [{
            "concept": "新生儿呼吸窘迫综合征",
            "aliases": ["NRDS"],
            "english": ["Neonatal Respiratory Distress Syndrome"],
            "mesh": list(mesh or ["My Handmade Candidate"]),
            "metadata": {"source": "dataset_labels:t", "review_status": "dataset_derived",
                         "mesh_review_status": "needs_review", "label_count": 100},
        }]

    def test_parse_mesh_esummary_reads_real_fields(self):
        module = self._module()
        records = module.parse_mesh_esummary(self._ESUMMARY)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["ui"], "D012189")
        self.assertEqual(records[0]["name"], "Respiratory Distress Syndrome, Newborn")
        self.assertIn("NRDS", records[0]["terms"])
        self.assertTrue(records[0]["scope"])

    def test_enrich_grounds_exact_match_with_ui_and_entry_terms(self):
        module = self._module()
        calls = []

        def fetch(url, params):
            calls.append((url, params))
            return self._fetch(url, params)

        rows = self._rows()
        stats = module.enrich_rows_with_mesh(rows, fetch, email="dev@example.org")
        self.assertEqual(stats["statuses"], {"mesh_grounded": 1})
        row = rows[0]
        self.assertEqual(row["mesh"], ["Respiratory Distress Syndrome, Newborn"])  # 官方主题词
        self.assertEqual(row["metadata"]["mesh_ui"], "D012189")
        self.assertEqual(row["metadata"]["mesh_source"], "ncbi_mesh_esummary")
        self.assertEqual(row["metadata"]["mesh_review_status"], "mesh_grounded")
        self.assertEqual(row["metadata"]["mesh_before"], ["My Handmade Candidate"])
        self.assertIn("Respiratory Distress Syndrome, Newborn", row["aliases"])   # 真实入口词进别名
        self.assertIn("Respiratory Distress Syndrome, Newborn", row["metadata"]["mesh_entry_terms"])
        self.assertNotIn("NRDS", row["metadata"].get("mesh_entry_terms", ()))      # 已有别名不重复
        self.assertNotIn("Neonatal Respiratory Distress Syndrome", row["metadata"].get("mesh_entry_terms", ()))  # 已是英文名，去重
        self.assertTrue(all(p.get("email") == "dev@example.org" for _, p in calls))

    def test_enrich_top_match_and_not_found_and_no_english(self):
        module = self._module()

        def fetch_top(url, params):
            return self._fetch(url, params)

        rows = self._rows(mesh=[])
        rows[0]["english"] = ["Totally Made Up Syndrome"]     # 无精确命中 → 词重合最高者 top-match
        stats = module.enrich_rows_with_mesh(rows, fetch_top)
        self.assertEqual(stats["statuses"], {"mesh_top_match": 1})
        self.assertEqual(rows[0]["metadata"]["mesh_review_status"], "mesh_top_match")
        self.assertIn("Respiratory Distress Syndrome, Newborn", rows[0]["metadata"]["mesh_candidates"])

        rows2 = self._rows()
        stats2 = module.enrich_rows_with_mesh(rows2, lambda u, p: b'{"esearchresult": {"idlist": []}}')
        self.assertEqual(stats2["statuses"], {"not_found": 1})
        self.assertEqual(rows2[0]["metadata"]["mesh_review_status"], "not_found")

        rows3 = [{"concept": "x", "aliases": [], "english": [], "mesh": [],
                  "metadata": {"review_status": "dataset_derived"}}]
        stats3 = module.enrich_rows_with_mesh(rows3, fetch_top)
        self.assertEqual(stats3["statuses"], {"no_english": 1})

    def test_builder_integration_mesh_enrich(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            (data_dir / "x.json").write_text(json.dumps([
                {"disease_label": "新生儿呼吸窘迫综合征",
                 "disease_label_en": "Neonatal Respiratory Distress Syndrome (NRDS)"},
            ], ensure_ascii=False), encoding="utf-8")

            def fetch(url, params):
                return self._fetch(url, params)

            result = module.build(data_dir, mesh_enrich=True, mesh_fetch=fetch)
            row = result["rows"][0]
            self.assertEqual(row["metadata"]["mesh_review_status"], "mesh_grounded")
            self.assertEqual(row["metadata"]["mesh_ui"], "D012189")
            self.assertEqual(result["mesh_enrichment"]["source"], "ncbi_mesh_esummary")
            # 不开 mesh_enrich 时保持确定性（不联网、不改候选）
            result2 = module.build(data_dir, mesh_enrich=False, mesh_fetch=fetch)
            self.assertIsNone(result2["mesh_enrichment"])
            self.assertEqual(result2["rows"][0]["metadata"]["mesh_review_status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
