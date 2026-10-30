"""Generate pilot reference answers from gold evidence using a Qwen-compatible API.

This is an exploratory bootstrap tool. Generated answers must be clinically reviewed
before being used as a formal benchmark.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def call_qwen(question: str, evidence: list[dict], *, model: str, base_url: str, api_key: str) -> dict:
    excerpts = []
    for index, item in enumerate(evidence, start=1):
        excerpts.append(
            f"证据{index}（{item['guide_title']}；{item.get('locator', '')}）：\n{item['text']}"
        )
    prompt = f"""你是医学指南评测集构建助手。请严格只根据给出的指南证据，为问题生成一个试验阶段参考答案。
不要补充证据中没有的剂量、阈值、适应证或临床结论。输出 JSON，不要 Markdown：
{{"expected_answer":"简洁、完整的中文参考答案","required_points":["答案必须覆盖的事实点1","事实点2"]}}

问题：{question}

指南证据：
{chr(10).join(excerpts)}"""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = json.loads(response.read().decode("utf-8"))
    content = raw["choices"][0]["message"]["content"]
    result = json.loads(content)
    if not isinstance(result.get("expected_answer"), str) or not result["expected_answer"].strip():
        raise ValueError("模型未返回 expected_answer")
    if not isinstance(result.get("required_points"), list):
        raise ValueError("模型未返回 required_points 数组")
    return result


def evidence_for(row: dict, documents: list[dict]) -> list[dict]:
    result = []
    for anchor in row.get("gold_evidence", []):
        title = anchor["guide_title"]
        section = anchor.get("section_contains", "")
        terms = anchor.get("anchor_terms", [])
        for doc in documents:
            metadata = doc.get("metadata", {})
            if metadata.get("guide_title") != title:
                continue
            if section and section not in " ".join(metadata.get("section_path", [])):
                continue
            if not all(term in doc.get("text", "") for term in terms):
                continue
            result.append({
                "guide_title": title,
                "locator": doc.get("locator", ""),
                "text": doc.get("text", ""),
            })
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--model", default="qwen-plus")
    parser.add_argument("--base-url", default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    args = parser.parse_args()
    load_env(args.env)
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("DASHSCOPE_API_KEY 未配置")
    rows = [json.loads(line) for line in args.benchmark.read_text(encoding="utf-8").splitlines() if line.strip()]
    raw = json.loads(args.library.read_text(encoding="utf-8"))
    documents = [doc for source in raw.get("sources", []) for doc in source.get("documents", [])]
    output = []
    for index, row in enumerate(rows, start=1):
        evidence = evidence_for(row, documents)
        if not evidence:
            raise SystemExit(f"{row.get('query_id')}: 无法解析 gold evidence")
        generated = call_qwen(row["question"], evidence, model=args.model, base_url=args.base_url, api_key=api_key)
        row["expected_answer"] = generated["expected_answer"].strip()
        row["required_points"] = [str(point).strip() for point in generated["required_points"] if str(point).strip()]
        row["answer_key_metadata"] = {"method": "qwen_from_gold_evidence", "model": args.model, "pilot": True}
        output.append(row)
        print(f"[{index}/{len(rows)}] {row.get('query_id')}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in output) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
