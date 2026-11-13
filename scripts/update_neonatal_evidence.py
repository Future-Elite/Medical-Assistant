"""Reproducible public PubMed discovery; never reads or transmits patient data."""
from __future__ import annotations

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'
NEONATAL = '("Infant, Newborn"[Mesh] OR neonat*[tiab] OR newborn*[tiab] OR preterm[tiab])'
HIGH_LEVEL = '(Guideline[pt] OR Practice Guideline[pt] OR Meta-Analysis[pt] OR Systematic Review[pt] OR consensus[ti])'
QUERIES = {
    'pneumonia': f'{NEONATAL} AND (Pneumonia[Mesh] OR pneumonia[tiab]) AND ({HIGH_LEVEL} OR Randomized Controlled Trial[pt])',
    'rds_guidelines': f'{NEONATAL} AND ("Respiratory Distress Syndrome, Newborn"[Mesh] OR "respiratory distress syndrome"[tiab]) AND {HIGH_LEVEL}',
    'respiratory_interventions': f'{NEONATAL} AND (CPAP[tiab] OR NIPPV[tiab] OR surfactant[tiab] OR "volume targeted"[tiab]) AND Randomized Controlled Trial[pt]',
    'infection_antibiotics': f'{NEONATAL} AND (Sepsis[Mesh] OR "early onset sepsis"[tiab] OR "late onset sepsis"[tiab] OR "neonatal infection"[tiab]) AND ({HIGH_LEVEL} OR Randomized Controlled Trial[pt])',
    'oxygen_caffeine': f'{NEONATAL} AND ("oxygen saturation"[tiab] OR caffeine[tiab]) AND ({HIGH_LEVEL} OR Randomized Controlled Trial[pt])',
    'supportive_guidelines': f'{NEONATAL} AND (nutrition[tiab] OR transfusion[tiab] OR hypoglyc*[tiab] OR hyperbilirubin*[tiab] OR "fluid therapy"[tiab]) AND (Guideline[pt] OR Practice Guideline[pt] OR consensus[ti])',
    'diagnostic_laboratory': f'{NEONATAL} AND ("reference interval"[tiab] OR "reference intervals"[tiab] OR ((procalcitonin[tiab] OR "C-reactive protein"[tiab] OR "blood count"[tiab]) AND sepsis[tiab])) AND ({HIGH_LEVEL} OR Randomized Controlled Trial[pt])',
}
CORE_PMIDS = ['30455342', '30455344', '41802129', '40962511', '42130350', '36863329',
              '34902013', '37695601', '18272893', '20472939', '28711318', '16707748',
              '29872859', '35927462', '38874929']


def text(node, path):
    found = node.find(path)
    return ''.join(found.itertext()) if found is not None else ''


def parse_articles(xml):
    rows = []
    root = ET.fromstring(xml)
    for article in root.findall('PubmedArticle'):
        citation = article.find('MedlineCitation')
        identifiers = {n.get('IdType'): n.text for n in article.findall('PubmedData/ArticleIdList/ArticleId')}
        pmid = text(citation, 'PMID')
        rows.append({'pmid': pmid, 'title': text(citation, 'Article/ArticleTitle'),
            'journal': text(citation, 'Article/Journal/Title'),
            'year': text(citation, 'Article/Journal/JournalIssue/PubDate/Year') or text(citation, 'Article/ArticleDate/Year'),
            'doi': identifiers.get('doi'), 'pmcid': identifiers.get('pmc'),
            'publication_types': [n.text for n in citation.findall('Article/PublicationTypeList/PublicationType')],
            'abstract': '\n'.join(''.join(n.itertext()) for n in citation.findall('Article/Abstract/AbstractText')),
            'url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/', 'screening_status': 'discovered_not_clinically_reviewed'})
    for article in root.findall('PubmedBookArticle'):
        book = article.find('BookDocument')
        pmid = text(book, 'PMID')
        rows.append({'pmid': pmid, 'title': text(book, 'ArticleTitle') or text(book, 'Book/BookTitle'),
            'journal': text(book, 'Book/BookTitle'), 'year': text(book, 'Book/PubDate/Year'),
            'doi': None, 'pmcid': None, 'publication_types': ['Book or guideline chapter'],
            'abstract': '\n'.join(''.join(n.itertext()) for n in book.findall('Abstract/AbstractText')),
            'url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/', 'screening_status': 'discovered_not_clinically_reviewed'})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--through', default='2026/11/12')
    parser.add_argument('--proxy', action='store_true', help='Use the already enabled Windows proxy without changing settings')
    parser.add_argument('--limit', type=int, default=9999)
    args = parser.parse_args()
    options = {'timeout': 45, 'follow_redirects': True}
    if args.proxy:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
            enabled = winreg.QueryValueEx(key, 'ProxyEnable')[0]
            server = winreg.QueryValueEx(key, 'ProxyServer')[0]
        if enabled and server and '=' not in server:
            options['proxy'] = 'http://' + server
    target = ROOT/'outputs/neonatal_evidence'
    target.mkdir(parents=True, exist_ok=True)
    manifest = {'searched_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'through': args.through,
        'patient_data_sent': False, 'scope': 'Scoped PubMed discovery, not a claim to cover all medical literature or unpublished/unindexed guidelines',
        'queries': {}, 'clinical_review': 'Only curated Graph-RAG recommendations are executable context; discoveries are not automatically promoted.'}
    records = {}; memberships = {}
    with httpx.Client(**options) as client:
        def get(endpoint, params):
            for attempt in range(4):
                response = client.get(BASE+endpoint, params={'db': 'pubmed', 'tool': 'EHRDreamerEvidenceSearch', **params})
                if response.status_code not in (429, 500, 502, 503, 504):
                    response.raise_for_status()
                    time.sleep(.4)
                    return response
                time.sleep(2 ** attempt)
            response.raise_for_status()
        for topic, query in QUERIES.items():
            term = query + f' AND ("1900/01/01"[Date - Publication] : "{args.through}"[Date - Publication])'
            cache = target/(topic+'.search.json')
            if cache.exists():
                data = json.loads(cache.read_text(encoding='utf-8'))
                if data.get('query') != term: data = None
            else: data = None
            if data is None:
                result = get('esearch.fcgi', {'term': term, 'retmode': 'json', 'retmax': min(args.limit, 9999), 'sort': 'pub_date'}).json()['esearchresult']
                data = {'query': term, 'count': int(result['count']), 'ids': result['idlist'], 'query_translation': result.get('querytranslation')}
                cache.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            ids = data['ids']
            manifest['queries'][topic] = {**data, 'retrieved_count': len(ids), 'complete_id_retrieval': len(ids) == data['count']}
            for identifier in ids: memberships.setdefault(identifier, []).append(topic)
            print(json.dumps({'topic': topic, 'matches': data['count'], 'retrieved_ids': len(ids)}), flush=True)
        for identifier in CORE_PMIDS: memberships.setdefault(identifier, []).append('curated_citation_seeds')
        manifest['curated_citation_seed_pmids'] = CORE_PMIDS
        all_ids = list(memberships)
        for start in range(0, len(all_ids), 200):
            ids = all_ids[start:start+200]
            cache = target/f'articles-{start:05}.json'
            if cache.exists():
                saved = json.loads(cache.read_text(encoding='utf-8'))
                rows = saved['records'] if saved.get('requested_ids') == ids else None
            else: rows = None
            if rows is None:
                rows = parse_articles(get('efetch.fcgi', {'id': ','.join(ids), 'retmode': 'xml'}).text)
                cache.write_text(json.dumps({'requested_ids': ids, 'records': rows}, ensure_ascii=False), encoding='utf-8')
            for row in rows: records[row['pmid']] = {**row, 'topics': memberships[row['pmid']]}
            print(json.dumps({'metadata_processed': min(start+200, len(all_ids)), 'total_unique': len(all_ids)}), flush=True)
        missing = sorted(set(all_ids)-set(records))
        # PubMed also returns book records; recover incomplete earlier caches.
        if missing:
            rows = parse_articles(get('efetch.fcgi', {'id': ','.join(missing), 'retmode': 'xml'}).text)
            for row in rows: records[row['pmid']] = {**row, 'topics': memberships[row['pmid']]}
    manifest.update(unique_pmids=len(all_ids), metadata_records=len(records),
                    complete_metadata_retrieval=set(records) == set(all_ids),
                    missing_pmids=sorted(set(all_ids)-set(records)))
    (target/'pubmed_catalog.json').write_text(json.dumps(list(records.values()), ensure_ascii=False, indent=2), encoding='utf-8')
    (target/'search_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in manifest.items() if k != 'queries'}, ensure_ascii=False), flush=True)


if __name__ == '__main__': main()
