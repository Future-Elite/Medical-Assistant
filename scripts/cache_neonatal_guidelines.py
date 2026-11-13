"""Cache accessible public guideline pages with checksums; no access-control bypass."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ehrdreamer.dual.evidence import SOURCES


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proxy',action='store_true')
    args=parser.parse_args()
    options={'timeout':25,'follow_redirects':True}
    if args.proxy:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
            if winreg.QueryValueEx(key,'ProxyEnable')[0]:
                proxy=winreg.QueryValueEx(key,'ProxyServer')[0]
                if '=' not in proxy:options['proxy']='http://'+proxy
    target=ROOT/'guidelines/evidence_2026'
    target.mkdir(parents=True,exist_ok=True)
    report={'checked_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'patient_data_sent':False,'sources':[]}
    with httpx.Client(**options) as client:
        for identifier,source in SOURCES.items():
            if source['kind'] not in ('guideline','consensus','framework','clinical_report','position_statement'):continue
            row={'id':identifier,**source}
            try:
                response=client.get(source['url']);row['http_status']=response.status_code
                if response.status_code==200:
                    is_pdf='application/pdf' in response.headers.get('content-type','')
                    extension='.pdf' if is_pdf else '.html'
                    file=target/(identifier+extension)
                    file.write_bytes(response.content)
                    row.update(local_file=str(file.relative_to(ROOT)),sha256=hashlib.sha256(response.content).hexdigest(),
                        access='cached_public_page',coverage='pdf_document' if is_pdf else 'bibliographic_record_only' if 'pubmed.ncbi.nlm.nih.gov' in str(response.url) else 'public_html_page_not_guaranteed_full_text')
                else:row['access']='not_accessible_no_bypass_attempted'
            except httpx.HTTPError as exc:row.update(access='request_failed',error_type=type(exc).__name__)
            if source.get('document_url'):
                try:
                    document=client.get(source['document_url'])
                    row['document_http_status']=document.status_code
                    if document.status_code==200 and document.content.startswith(b'%PDF'):
                        file=target/(identifier+'.pdf');file.write_bytes(document.content)
                        row.update(document_file=str(file.relative_to(ROOT)),document_sha256=hashlib.sha256(document.content).hexdigest(),
                                   coverage='pdf_document')
                except httpx.HTTPError as exc:row['document_error_type']=type(exc).__name__
            report['sources'].append(row)
            print(json.dumps({k:row.get(k) for k in ('id','http_status','access','coverage')},ensure_ascii=False),flush=True)
    (target/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
