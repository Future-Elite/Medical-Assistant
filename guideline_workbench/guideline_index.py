"""Compatibility entry point for the guideline_processing package."""

from __future__ import annotations

import argparse
from pathlib import Path

from guideline_processing.pipeline import main as _pipeline_main, process_guidelines


def build(input_path: Path, output: Path, specialty: str, size: int, overlap: int,
          tier: str = "advanced", version: str = "", source_url: str = "") -> int:
    return process_guidelines(input_path, output, specialty=specialty,
                              chunk_size=size, overlap=overlap, tier=tier,
                              version=version, source_url=source_url)


def main() -> None:
    _pipeline_main()


if __name__ == "__main__":
    main()
