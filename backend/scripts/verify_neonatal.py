"""Read-only reconciliation of the source workbook, MySQL storage and running API.

Run from the repository root: python backend/scripts/verify_neonatal.py
Uses only Python standard library and the mysql CLI; credentials come from HIS_DB_*
environment variables, matching the server's local defaults. Prints aggregates only.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(os.environ.get("NEONATAL_SOURCE", str(ROOT / "2024带孕周的出院小结汇总.xlsx")))
BASE = os.environ.get("HIS_API_URL", "http://127.0.0.1:8080/api/neonatal")
NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def api(path, **params):
    with urllib.request.urlopen(BASE + path + "?" + urllib.parse.urlencode(params), timeout=30) as response:
        result = json.load(response)
    assert result["code"] == 0, "API failed"
    return result["data"]


def mysql(query):
    env = dict(os.environ, MYSQL_PWD=os.environ.get("HIS_DB_PASSWORD", "his@2026"))
    result = subprocess.run([
        "mysql", "-h", os.environ.get("HIS_DB_HOST", "127.0.0.1"), "-P", os.environ.get("HIS_DB_PORT", "3306"),
        "-u", os.environ.get("HIS_DB_USER", "his_user"), "--default-character-set=utf8mb4", "--batch", "--raw", "--skip-column-names",
        os.environ.get("HIS_DB_NAME", "his_demo"), "-e", query
    ], env=env, capture_output=True, encoding="utf-8", check=True)
    return result.stdout


def source_rows():
    with zipfile.ZipFile(SOURCE) as z:
        strings = ["".join(node.itertext()) for node in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("x:si", NS)]
        rows = {}
        header = None
        for row in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).findall("x:sheetData/x:row", NS):
            values = {}
            for cell in row.findall("x:c", NS):
                column = re.sub(r"\d", "", cell.attrib["r"])
                node = cell.find("x:v", NS)
                text = node.text if node is not None else ""
                if cell.attrib.get("t") == "s":
                    text = strings[int(text)]
                values[column] = text or ""
            if header is None:
                header = values
            elif any(values.values()):
                rows[int(row.attrib["r"])] = {name: values.get(column, "") for column, name in header.items()}
        return rows


def main():
    rows = source_rows()
    sha = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    batch_id = int(mysql("SELECT id FROM neonatal_import_batch WHERE file_sha256='" + sha + "'").strip())
    stored = mysql(f"SELECT source_row_number,raw_data FROM neonatal_source_row WHERE batch_id={batch_id} ORDER BY source_row_number")
    stored_rows = {int(line.split("\t", 1)[0]): json.loads(line.split("\t", 1)[1]) for line in stored.splitlines()}
    assert stored_rows == rows, "Source cell preservation mismatch"
    # Independently compare EVERY diagnosis and summary text to its corresponding raw cell.
    comparisons = [
        ("s.presenting_symptoms", "入院时主要症状及体征"), ("s.laboratory_findings", "实验室检查及主要会诊"),
        ("s.special_examinations", "特殊检验及重要会诊"), ("s.treatment_course", "诊疗过程"),
        ("s.complications", "合并症"), ("s.discharge_condition", "出院时情况"),
        ("s.discharge_orders", "出院医嘱"), ("s.discharge_guidance", "出院指导"),
        ("da.diagnosis_text", "入院诊断"), ("dd.diagnosis_text", "出院诊断")]
    condition = " OR ".join(f"NOT (BINARY {column} <=> BINARY JSON_UNQUOTE(JSON_EXTRACT(src.raw_data,'$.\"{key}\"')))" for column, key in comparisons)
    mismatches = int(mysql(f"""SELECT COUNT(*) FROM neonatal_record r
        JOIN neonatal_source_row src ON src.id=r.source_row_id JOIN neonatal_summary s ON s.record_id=r.id
        JOIN neonatal_diagnosis da ON da.record_id=r.id AND da.phase='ADMISSION'
        JOIN neonatal_diagnosis dd ON dd.record_id=r.id AND dd.phase='DISCHARGE'
        WHERE r.batch_id={batch_id} AND ({condition})""").strip())
    assert mismatches == 0, "Normalized clinical text differs from source"
    stats = api("/stats", batchId=batch_id)
    assert stats["overview"]["records"] == len(rows)
    known = sum(v["孕周"].isdigit() for v in rows.values())
    assert stats["overview"]["knownGestation"] == known
    assert stats["overview"]["preterm"] == sum(v["孕周"].isdigit() and int(v["孕周"]) < 37 for v in rows.values())
    assert sum(r["count"] for r in stats["gestation"]) == len(rows)
    assert sum(r["count"] for r in stats["outcomes"]) == len(rows)
    assert sum(r["count"] for r in stats["monthly"]) + stats["overview"]["unknownAdmissionDate"] == len(rows)
    ids = []
    for page in range(1, (len(rows) + 99) // 100 + 1):
        part = api("/records", batchId=batch_id, page=page, size=100)
        assert part["total"] == len(rows)
        ids.extend((r["id"], r["rowNumber"]) for r in part["records"])
    assert len(ids) == len(set(id for id, row in ids)) == len(rows), "Pagination duplicates or drops records"
    samples = [ids[0], ids[len(ids) // 2], ids[-1]]
    for record_id, row in samples:
        assert api(f"/records/{record_id}")["rawData"] == rows[row]
    unknown = api("/records", batchId=batch_id, gestation="unknown")
    assert unknown["total"] == len(rows) - known
    assert api("/records", batchId=batch_id, keyword="__NO_SUCH_DIAGNOSIS__")["total"] == 0
    print(json.dumps({"verifiedSourceRows": len(rows), "verifiedOriginalCells": sum(map(len, rows.values())),
                      "clinicalTextMismatches": mismatches, "paginationRows": len(ids), "apiDetailSamples": len(samples),
                      "knownGestation": known, "unknownGestation": len(rows) - known}, ensure_ascii=False))


if __name__ == "__main__":
    main()
