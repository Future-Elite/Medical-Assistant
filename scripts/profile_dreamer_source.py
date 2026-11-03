"""Read-only discharge workbook profiling; outputs aggregate counts without patient text."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]


def text(value):
    return '' if value is None else str(value).strip()


def calendar_date(value):
    """Extract a defensible calendar date; do not repair malformed year/month/day."""
    if isinstance(value, (datetime, date)):
        return value.date() if isinstance(value, datetime) else value
    match = re.match(r'^(\d{4})[./-](\d{1,2})[./-](\d{1,2})(?=$|\D)', text(value))
    if not match:
        return None
    try:
        return date(*map(int, match.groups()))
    except ValueError:
        return None


def gestation_weeks(value):
    raw = text(value)
    if not raw or re.search(r'无提及|不详|未知|未记录', raw):
        return None
    match = re.fullmatch(r'(\d{2})(?:\.0+)?(?:周)?', raw)
    return int(match.group(1)) if match else None


def profile(source):
    workbook = openpyxl.load_workbook(source, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        iterator = sheet.iter_rows(values_only=True)
        headers = list(next(iterator))
        rows = [dict(zip(headers, row)) for row in iterator
                if any(value is not None and text(value) for value in row)]
        fields = {}
        for header in headers:
            values = [text(row.get(header)) for row in rows]
            present = [value for value in values if value]
            fields[header] = {
                'nonempty': len(present), 'missing': len(values) - len(present),
                'distinct': len(set(present)),
                'median_characters': statistics.median(map(len, present)) if present else 0,
                'maximum_characters': max(map(len, present), default=0),
            }
        patterns = {'肺炎': '肺炎', 'RDS': r'呼吸窘迫|NRDS|(?<![A-Za-z])RDS(?![A-Za-z])|肺透明膜',
                    '黄疸': '黄疸|高胆红素', '早产': '早产', 'BPD': '支气管肺发育不良|BPD',
                    '暂时性呼吸增快': '暂时性呼吸增快|湿肺|TTN', '窒息': '窒息', '败血症': '败血症'}
        diagnoses = [text(row.get('出院诊断')) for row in rows]
        matches = {key: [bool(re.search(pattern, value, re.I)) for value in diagnoses]
                   for key, pattern in patterns.items()}
        stays, reversed_dates, unparsed, years = [], 0, 0, Counter()
        age_equals_stay, newborn_positive_age = 0, 0
        for row in rows:
            admission, discharge = calendar_date(row.get('入院时间')), calendar_date(row.get('出院时间'))
            if admission and discharge:
                stay = (discharge - admission).days
                stays.append(stay)
                reversed_dates += stay < 0
                years[admission.year] += 1
                age = re.fullmatch(r'(\d+)天', text(row.get('年龄')))
                age_equals_stay += bool(age and int(age.group(1)) == stay)
            else:
                unparsed += 1
            newborn_positive_age += bool(
                re.search(r'患儿[^。\n]{0,12}初生', text(row.get('入院时主要症状及体征')))
                and re.fullmatch(r'[1-9]\d*天', text(row.get('年龄'))))
        courses = [text(row.get('诊疗过程')) for row in rows]
        gestations = [gestation_weeks(row.get('孕周')) for row in rows]
        known_gestation = [value for value in gestations if value is not None]
        encounter_keys = {(text(row.get('住院号')), text(row.get('入院时间'))) for row in rows}
        return {
            'source_file': source.name, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'sheet': sheet.title, 'source_range': f'A1:{get_column_letter(sheet.max_column)}{sheet.max_row}',
            'record_count': len(rows), 'columns': headers, 'field_profiles': fields,
            'unique_hospital_id_values': len({text(row.get('住院号')) for row in rows if text(row.get('住院号'))}),
            'raw_encounter_key_count': len(encounter_keys),
            'repeated_raw_encounter_key_rows': len(rows) - len(encounter_keys),
            'diagnosis_keyword_counts': {key: sum(values) for key, values in matches.items()},
            'diagnosis_counts_are_overlapping_keyword_counts_not_adjudicated_cases': True,
            'pneumonia_and_rds_overlap': sum(a and b for a, b in zip(matches['肺炎'], matches['RDS'])),
            'outcome_counts': dict(Counter(text(row.get('治疗结果')) or '[empty]' for row in rows)),
            'gestation': {'known_integer_weeks': len(known_gestation),
                          'unknown_or_unparsed': len(rows) - len(known_gestation),
                          'minimum_known_weeks': min(known_gestation, default=None),
                          'maximum_known_weeks': max(known_gestation, default=None)},
            'stay_calendar_dates': {'parseable_pairs': len(stays), 'missing_or_unparseable_pairs': unparsed,
                                    'discharge_before_admission': reversed_dates,
                                    'median_calendar_day_difference': statistics.median(stays) if stays else None,
                                    'over_180_calendar_days_needs_review': sum(value > 180 for value in stays),
                                    'admission_year_counts': dict(years),
                                    'note': 'Date-level profiling only; partial dates/times require interval representation in the importer.'},
            'age_review_signals': {'age_equals_calendar_stay_days': age_equals_stay,
                                   'newborn_admission_wording_with_positive_age_days': newborn_positive_age,
                                   'note': 'Review signals do not prove when the age field was measured.'},
            'course_time_signals': {
                'date_like_text': sum(bool(re.search(r'20\d{2}[.年/-]\d{1,2}|(?<!\d)\d{1,2}[./月]\d{1,2}', value)) for value in courses),
                'clock_time_like_text': sum(bool(re.search(r'\d{1,2}[:：]\d{2}', value)) for value in courses),
                'note': 'Pattern matches are not validated action timestamps or complete order records.'},
            'lab_cells_at_excel_text_limit': sum(len(text(row.get('实验室检查及主要会诊'))) == 32767 for row in rows),
        }
    finally:
        workbook.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='2024带孕周的出院小结汇总.xlsx')
    parser.add_argument('--output', default='outputs/dreamer_design/workbook_profile.json')
    args = parser.parse_args()
    source, output = (ROOT / args.source).resolve(), (ROOT / args.output).resolve()
    if not source.is_relative_to(ROOT) or not output.is_relative_to(ROOT):
        parser.error('Source and output must stay inside the project.')
    if source == output:
        parser.error('The source workbook cannot be overwritten.')
    result = profile(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('record_count', 'gestation', 'stay_calendar_dates',
                                                  'course_time_signals', 'lab_cells_at_excel_text_limit')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
