import unittest
from pathlib import Path

from guideline_processing.chunker import split_markdown


def split(text, **options):
    return split_markdown(
        text,
        source_file=Path("guide.pdf"),
        source_id="guide",
        source_title="Test guideline",
        specialty="neonatal",
        chunk_size=80,
        overlap=10,
        **options,
    )


class ChunkerVariantTests(unittest.TestCase):
    def test_fixed_recursive_and_structure_modes_emit_strategy_metadata(self):
        text = "# Section\n\n" + ("Recommendation text contains condition and treatment. " * 8)
        fixed = split(text, chunk_strategy="fixed")
        recursive = split(text, chunk_strategy="recursive")
        structure = split(text, chunk_strategy="structure")
        self.assertTrue(fixed)
        self.assertTrue(recursive)
        self.assertTrue(structure)
        self.assertTrue(all(item.chunk_strategy == "fixed" for item in fixed))
        self.assertTrue(all(item.chunk_strategy == "recursive" for item in recursive))
        self.assertTrue(all(item.section_path == ["Section"] for item in structure))

    def test_table_row_mode_preserves_column_labels(self):
        text = "# NRS\n\n| 条件 | 推荐 |\n| --- | --- |\n| 高风险 | 使用CPAP |\n"
        chunks = split(text, table_strategy="row")
        table = next(item for item in chunks if item.element_type == "table")
        self.assertIn("条件：高风险", table.text)
        self.assertIn("推荐：使用CPAP", table.text)

    def test_table_row_context_includes_section_path(self):
        text = "# NRS\n\n| 条件 | 推荐 |\n| --- | --- |\n| 高风险 | 使用CPAP |\n"
        chunks = split(text, table_strategy="row_context")
        table = next(item for item in chunks if item.element_type == "table")
        self.assertIn("章节：NRS", table.text)

    def test_figure_context_keeps_reference_and_associated_prose(self):
        text = "# Management\n\nFigure 1: respiratory support pathway.\n\n![path](images/path.png)\n"
        chunks = split(text, figure_strategy="caption_context")
        figure = next(item for item in chunks if item.element_type == "figure")
        self.assertIn("Figure 1", figure.text)
        self.assertEqual(figure.image_refs, ["images/path.png"])

    def test_embedded_figure_data_is_not_copied_into_chunks(self):
        text = "# Resuscitation\n\n![flow](data:image/png;base64,QUJDREVGRw==)\n"
        figure = next(item for item in split(text, figure_strategy="caption_context") if item.element_type == "figure")
        self.assertIn("embedded-image/png", figure.text)
        self.assertEqual(figure.image_refs, ["embedded-image/png"])
        self.assertNotIn("QUJDREVGRw==", figure.text)


if __name__ == "__main__":
    unittest.main()
