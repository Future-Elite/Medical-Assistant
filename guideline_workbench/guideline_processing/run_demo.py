"""Run the guideline PDF parsing, chunking, export, and Medical-Assistant RAG demo."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def process_pdf(filename: str, output: str, specialty: str, url: str, source_id: str, version: str = "2025") -> None:
    input_path = ROOT / "data" / "guidelines" / filename
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "guideline_processing.pipeline",
            "--input",
            str(input_path),
            "--output",
            str(ROOT / "data" / output),
            "--specialty",
            specialty,
            "--version",
            version,
            "--source-url",
            url,
            "--tier",
            "advanced",
            "--medical-assistant-output",
            str(ROOT / "data" / "medical_assistant_guidelines.json"),
            "--medical-assistant-source-id",
            source_id,
        ],
        cwd=ROOT,
        check=True,
    )


def main() -> None:
    process_pdf(
        "\u57fa\u5c42\u533b\u7597\u536b\u751f\u673a\u6784\u5e38\u89c1\u65b0\u751f\u513f\u75be\u75c5\u8bca\u7597\u6307\u5357\uff1a\u65b0\u751f\u513f\u611f\u67d3\u6027\u80ba\u708e\uff082025\u5e74\uff09.pdf",
        "newborn_pneumonia_chunks.jsonl",
        "\u65b0\u751f\u513f",
        "https://www.zgddek.com/CN/10.7499/j.issn.1008-8830.2412164",
        "medllm-newborn-pneumonia",
    )
    process_pdf(
        "\u300a\u6b27\u6d32\u65b0\u751f\u513f\u547c\u5438\u7a98\u8feb\u7efc\u5408\u5f81\u7ba1\u7406\u5171\u8bc6\u6307\u53572025\u7248\u300b\u89e3\u8bfb.pdf",
        "neonatal_rds_chunks.jsonl",
        "\u65b0\u751f\u513f\u547c\u5438\u7a98\u8feb\u7efc\u5408\u5f81",
        "https://www.zgddek.com/CN/10.7499/j.issn.1008-8830.2603060",
        "medllm-neonatal-rds-2025",
    )
    process_pdf(
        "\u4e2d\u56fd\u65b0\u751f\u513f\u590d\u82cf\u6307\u5357\uff082021\u5e74\u4fee\u8ba2\uff09.pdf",
        "resuscitation_chunks.jsonl",
        "\u65b0\u751f\u513f\u590d\u82cf",
        "https://doi.org/10.3760/cma.j.cn113903-20211122-00967",
        "medllm-neonatal-resuscitation-2021",
        "2021",
    )
    subprocess.run(
        [sys.executable, "-m", "guideline_processing.run_medical_assistant"],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()
