"""Standalone n8n workflow port: python runner.py check|run|sync|schedule|demo."""
from __future__ import annotations
import argparse
import copy
import getpass
import html
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import xml.etree.ElementTree as ET

from core import (KST, HEADERS, KEYS, WorkflowError, clean, text, parse_time, period,
    normalize_articles, naver_articles, dart_articles, prepare_raw, restore_articles,
    select_articles, disclosure_output, validate_results, briefing_row, aggregate_stocks)

ROOT=Path(__file__).resolve().parent

def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def now_kst():
    return datetime.now(KST)

def atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    os.replace(tmp,path)

class RunLock:
    """OS lock survives neither crash nor process exit; no stale PID removal."""
    def __init__(self,path): self.path=path; self.file=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file=self.path.open('a+b'); self.file.seek(0)
        if self.path.stat().st_size==0: self.file.write(b'0'); self.file.flush()
        self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close(); raise WorkflowError('이 폴더에서 다른 실행이 진행 중입니다.') from None
        return self
    def __exit__(self,*args): self.file.close()

class Http:
    def __init__(self,cfg):
        import requests
        self.session=requests.Session(); self.cfg=cfg
        self.session.headers['User-Agent']='PortableNewsWorkflow/1.0'
    def request(self,method,url,*,label='HTTP',session=None,retry=True,**kwargs):
        import requests
        count=self.cfg['http_attempts'] if retry else 1
        kwargs.setdefault('timeout',self.cfg['http_timeout_seconds'])
        kwargs.setdefault('allow_redirects',False)
        for attempt in range(count):
            try:
                response=(session or self.session).request(method,url,**kwargs)
            except requests.RequestException:
                if attempt+1<count: time.sleep(min(2**attempt,10)); continue
                raise WorkflowError(label+': 연결 실패/시간 초과. 키나 응답 원문은 기록하지 않았습니다.') from None
            if response.status_code in (429,500,502,503,504) and attempt+1<count:
                delay=response.headers.get('Retry-After','')
                time.sleep(min(float(delay),60) if delay.isdigit() else min(2**attempt,10)); continue
            if not 200<=response.status_code<300:
                raise WorkflowError(f'{label}: HTTP {response.status_code}. 인증·권한·요청 한도를 확인하세요.')
            return response
        raise WorkflowError(label+': 요청 실패')
    def json(self,method,url,**kwargs):
        response=self.request(method,url,**kwargs)
        try: return response.json()
        except ValueError: raise WorkflowError('JSON 응답 형식 오류') from None

def credentials_needed(cfg,command):
    required=[]
    if command in ('run','schedule'):
        if cfg['sources']['naver']: required+=['NAVER_API_KEY_ID','NAVER_API_KEY']
        if cfg['sources']['dart']: required+=['DART_API_KEY']
        if any(cfg['sources'][s] for s in ['hankyung','mk','naver']): required+=['GEMINI_API_KEY']
    return required

def preflight(cfg,command,prompt=False):
    if cfg['storage'] not in ('google_sheets','local'): raise WorkflowError('storage는 google_sheets 또는 local이어야 합니다.')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',cfg['gemini_model']): raise WorkflowError('모델 이름 형식 오류')
    if cfg['naver_url']!='https://naverapihub.apigw.ntruss.com/search/v1/news': raise WorkflowError('네이버 인증정보는 원래 API HUB 주소로만 전송할 수 있습니다.')
    for key in ['batch_size','http_attempts','max_sync_rows','news_limit','disclosure_limit','rss_limit_per_source','naver_display','dart_page_count']:
        if type(cfg.get(key)) is not int or cfg[key]<1: raise WorkflowError('설정의 양수 정수 항목을 확인하세요: '+key)
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',cfg['schedule_kst']): raise WorkflowError('schedule_kst 형식은 HH:MM입니다.')
    if cfg['storage']=='google_sheets':
        if not cfg.get('spreadsheet_id') or cfg['spreadsheet_id']=='YOUR_SPREADSHEET_ID':
            raise WorkflowError('config.local.json의 spreadsheet_id와 sheet_ids를 대상 시트 값으로 설정하세요.')
        ids=cfg.get('sheet_ids',{})
        if set(ids)!=set(HEADERS) or any(type(v) is not int or v<0 for v in ids.values()) or len(set(ids.values()))!=3:
            raise WorkflowError('sheet_ids에는 raw, briefing, stocks의 서로 다른 정수 gid가 필요합니다.')
    missing=[]
    for name in credentials_needed(cfg,command):
        if not os.environ.get(name) and prompt and sys.stdin.isatty():
            os.environ[name]=getpass.getpass(name+' (저장하지 않음): ').strip()
        if not os.environ.get(name): missing.append(name)
    if cfg['storage']=='google_sheets' and not os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON') and not os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
        if prompt and sys.stdin.isatty():
            os.environ['GOOGLE_APPLICATION_CREDENTIALS']=input('Google 서비스 계정 JSON 파일 경로: ').strip().strip('"')
        if not os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'): missing.append('GOOGLE_APPLICATION_CREDENTIALS')
    if missing: raise WorkflowError('설정 필요: '+', '.join(missing)+' — README를 참고하세요.')

class Sheets:
    def __init__(self,cfg,http):
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        try:
            if os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON'):
                creds=service_account.Credentials.from_service_account_info(json.loads(os.environ['GOOGLE_SERVICE_ACCOUNT_JSON']),scopes=['https://www.googleapis.com/auth/spreadsheets'])
            else:
                creds=service_account.Credentials.from_service_account_file(os.environ['GOOGLE_APPLICATION_CREDENTIALS'],scopes=['https://www.googleapis.com/auth/spreadsheets'])
        except Exception: raise WorkflowError('Google 서비스 계정 설정을 읽지 못했습니다. 파일/환경변수를 확인하세요.') from None
        self.http=http; self.session=AuthorizedSession(creds); self.cfg=cfg
        self.base='https://sheets.googleapis.com/v4/spreadsheets/'+quote(cfg['spreadsheet_id'],safe='')
        self.titles=None
    def request(self,method,suffix,**kwargs):
        try: return self.http.json(method,self.base+suffix,session=self.session,label='Google Sheets',**kwargs)
        except WorkflowError: raise
        except Exception: raise WorkflowError('Google 인증 갱신 실패. 계정 권한을 확인하세요.') from None
    def title(self,kind):
        if self.titles is None:
            meta=self.request('GET','',params={'fields':'sheets.properties(sheetId,title)'})
            self.titles={s['properties']['sheetId']:s['properties']['title'] for s in meta['sheets']}
        title=self.titles.get(self.cfg['sheet_ids'][kind])
        if title is None: raise WorkflowError('Google 시트 탭 ID를 찾지 못했습니다: '+kind)
        return "'"+title.replace("'","''")+"'"
    def values(self,kind):
        response=self.request('GET','/values/'+quote(self.title(kind)+'!A:ZZ',safe=''),params={'valueRenderOption':'UNFORMATTED_VALUE','dateTimeRenderOption':'SERIAL_NUMBER'})
        values=response.get('values',[])
        if not values: raise WorkflowError('시트 첫 행에 열 이름을 준비하세요: '+kind)
        headers=[text(v) for v in values[0]]
        if len(set(headers))!=len(headers) or not set(HEADERS[kind])<=set(headers): raise WorkflowError('시트 열 이름 누락/중복: '+kind)
        if len(values)-1>self.cfg['max_sync_rows']: raise WorkflowError('시트 행 상한 초과')
        return headers,values[1:]
    def read(self,kind):
        headers,values=self.values(kind)
        return [{**{h:r[i] if i<len(r) else '' for i,h in enumerate(headers)},'row_number':index+2}
                for index,r in enumerate(values) if any(v not in ('',None) for v in r)]
    def write(self,kind,rows):
        if not rows: return
        headers,values=self.values(kind); key=KEYS[kind]; column=headers.index(key); index={}
        for number,row in enumerate(values,2):
            if column<len(row) and row[column] not in ('',None):
                value=text(row[column])
                if value in index: raise WorkflowError('시트에 중복 식별자가 있습니다. 중복 행을 확인하세요: '+kind)
                index[value]=number
        next_row=len(values)+2; updates={}
        for row in rows:
            ident=text(row.get(key)).strip()
            if not ident: raise WorkflowError('저장할 행의 식별자 누락: '+kind)
            number=index.get(ident)
            if number is None: number=next_row; next_row+=1; index[ident]=number
            old=values[number-2] if number-2<len(values) else []
            cells=[row.get(h,old[i] if i<len(old) else '') for i,h in enumerate(headers)]
            updates[number]={'range':self.title(kind)+f'!A{number}','values':[cells]}
        if next_row-2>self.cfg['max_sync_rows']: raise WorkflowError('시트 저장 행 상한 초과')
        data=list(updates.values())
        # Exact row addresses make retry safe; no non-idempotent append endpoint.
        for start in range(0,len(data),100):
            self.request('POST','/values:batchUpdate',json={'valueInputOption':'RAW','data':data[start:start+100]})

class LocalRows:
    def __init__(self,store): self.store=store
    def read(self,kind):
        return [json.loads(r[0]) for r in self.store.db.execute('SELECT payload_json FROM current_records WHERE kind=? ORDER BY entity_key',(kind,))]
    def write(self,kind,rows):
        result=self.store.sync(kind,rows,datetime.now(timezone.utc).isoformat())
        if result['rejected']: raise WorkflowError('DB 행 검증 실패: 격리(quarantine) 테이블을 확인하세요.')

def read_rss(http,url,limit):
    response=http.request('GET',url,label='RSS',allow_redirects=True)
    try: tree=ET.fromstring(response.content)
    except ET.ParseError: raise WorkflowError('RSS XML 형식 오류') from None
    # Both original URLs are RSS2 feeds. Reject non-feed error pages.
    if tree.tag.rsplit('}',1)[-1]!='rss': raise WorkflowError('예상한 RSS2 응답이 아닙니다.')
    result=[]
    for item in tree.findall('./channel/item')[:limit]:
        stamp=item.findtext('pubDate') or item.findtext('{http://purl.org/dc/elements/1.1/}date')
        try: date=parse_time(stamp).astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
        except (ValueError,TypeError,OverflowError): continue
        result.append({'title':clean(item.findtext('title')),'link':text(item.findtext('link')).strip(),
                       'contentSnippet':clean(item.findtext('description')),'isoDate':date})
    return result

def collect(cfg,http,now):
    articles=[]; window=period(now)
    for source in ('hankyung','mk'):
        if cfg['sources'][source]: articles+=read_rss(http,cfg['rss_urls'][source],cfg['rss_limit_per_source'])
    if cfg['sources']['naver']:
        responses=[]
        for query in cfg['naver_queries']:
            responses.append(http.json('GET',cfg['naver_url'],label='네이버',headers={'X-NCP-APIGW-API-KEY-ID':os.environ['NAVER_API_KEY_ID'],'X-NCP-APIGW-API-KEY':os.environ['NAVER_API_KEY']},params={'query':query,'display':cfg['naver_display'],'start':1,'sort':'date','format':'json'}))
            time.sleep(cfg['request_interval_seconds'])
        articles+=naver_articles(responses,window)
    if cfg['sources']['dart']:
        responses=[]
        for market in ('Y','K'):
            for kind in ('B','I'):
                responses.append(http.json('GET','https://opendart.fss.or.kr/api/list.json',label='OpenDART',params={'crtfc_key':os.environ['DART_API_KEY'],'bgn_de':window['bgn_de'],'end_de':window['end_de'],'corp_cls':market,'pblntf_ty':kind,'page_no':1,'page_count':cfg['dart_page_count'],'sort':'date','sort_mth':'desc','last_reprt_at':'N'}))
                time.sleep(cfg['request_interval_seconds'])
        articles+=dart_articles(responses,window)
    return normalize_articles(articles,now,cfg['lookback_days'])

def enrich(article,cfg,http):
    from bs4 import BeautifulSoup
    a=copy.deepcopy(article)
    allowed=re.match(r'^https?://(?:www\.)?(?:hankyung\.com/article/|mk\.co\.kr/news/)',a['link'])
    if not allowed:
        a.update(articleText='',contentStatus='공시 제목·접수정보만 확보, 공시 본문 미확보' if a.get('dataType')=='disclosure' else '네이버 제공 요약 사용, 원문 본문 미확보'); return a
    try:
        response=http.request('GET',re.sub('^http:','https:',a['link']),label='기사 본문',timeout=cfg['article_timeout_seconds'],retry=False)
        soup=BeautifulSoup(response.content,'html.parser')
        restricted=bool(soup.select_one('.premium9-view'))
        desc=soup.select_one('meta[property="og:description"]'); description=clean(desc.get('content','')) if desc else ''
        bodies=soup.select('#articletxt, #article-body, .news_cnt_detail_wrap')
        for body in bodies:
            for element in body.select('script,style,iframe,figure,.ad,.advertisement,.related_news'): element.decompose()
        body='' if restricted else ' '.join(b.get_text(' ',strip=True) for b in bodies)
        content=(body if len(body)>=80 else description)[:cfg['article_text_limit']]
        a.update(articleText=content,contentStatus='원문 발췌 보강' if content else 'RSS만 사용 (정상)')
    except WorkflowError:
        a.update(articleText='',contentStatus='RSS만 사용 (연결 실패 또는 HTTP 오류)')
    time.sleep(cfg['request_interval_seconds']); return a

def model_output(articles,cfg,http,now):
    import jsonschema
    schema=load_json(ROOT/'schema.json'); schema['minItems']=schema['maxItems']=len(articles)
    fields=['title','contentSnippet','articleText','link','isoDate','dataType','company','stockCode']
    payload=[{k:a[k] for k in fields if k in a} for a in articles]
    prompt=(ROOT/'prompt.txt').read_text(encoding='utf-8').replace('{report_date}',now.date().isoformat()).replace('{articles_json}',json.dumps(payload,ensure_ascii=False)).replace('{article_count}',str(len(articles)))
    body={'contents':[{'role':'user','parts':[{'text':prompt}]}],
          'generationConfig':{'responseMimeType':'application/json','responseJsonSchema':schema}}
    response=http.json('POST','https://generativelanguage.googleapis.com/v1beta/models/'+cfg['gemini_model']+':generateContent',label='Gemini',headers={'x-goog-api-key':os.environ['GEMINI_API_KEY']},json=body,timeout=cfg['gemini_timeout_seconds'],retry=False)
    try:
        candidate=response['candidates'][0]
        if candidate.get('finishReason')!='STOP': raise ValueError()
        value=json.loads(''.join(part.get('text','') for part in candidate['content']['parts'] if not part.get('thought')))
        jsonschema.validate(value,schema)
    except (KeyError,IndexError,TypeError,ValueError,jsonschema.ValidationError):
        raise WorkflowError('Gemini 출력 형식/기사 수 검증 실패. 원자료는 보관했고 결과 저장은 중단했습니다.') from None
    return value

def sync_database(rows,store,cfg):
    results=[]
    for kind in ('raw','briefing','stocks'):
        values=rows.read(kind)
        if len(values)>cfg['max_sync_rows']: raise WorkflowError('DB 동기화 행 상한 초과')
        result=store.sync(kind,values,datetime.now(timezone.utc).isoformat()); results.append(result)
        if result['rejected']: raise WorkflowError('DB 동기화에서 행 검증 실패. quarantine 테이블을 확인하세요.')
    return results

def pipeline(cfg,rows,store,http,now,companies,collector=collect,model=model_output,enricher=enrich):
    old=rows.read('raw'); saved=rows.read('briefing')
    articles=collector(cfg,http,now)
    records=prepare_raw(articles,old,saved,companies['prefilter_names'],now)
    rows.write('raw',records)
    selected=select_articles(restore_articles(articles,records),cfg['news_limit'],cfg['disclosure_limit'])
    total=0
    for start in range(0,len(selected),cfg['batch_size']):
        batch=[enricher(a,cfg,http) for a in selected[start:start+cfg['batch_size']]]
        only_disclosures=all(a.get('dataType')=='disclosure' for a in batch)
        output=disclosure_output(batch,now) if only_disclosures else model(batch,cfg,http,now)
        verified=validate_results(batch,output,companies['companies'],now)
        accepted=[briefing_row(v['issue']) for v in verified if v['accepted']]
        # Mark completion only after every accepted result has been saved.
        rows.write('briefing',accepted)
        rows.write('raw',[v['record'] for v in verified]); total+=len(accepted)
        print(f'처리 {min(start+len(batch),len(selected))}/{len(selected)}, 결과 저장 {total}건',flush=True)
        time.sleep(cfg['disclosure_batch_wait_seconds'] if only_disclosures else cfg['news_batch_wait_seconds'])
    rows.write('stocks',aggregate_stocks(rows.read('briefing'),now))
    results=sync_database(rows,store,cfg)
    return {'status':'ok','collected':len(articles),'selected':len(selected),'saved':total,'db':results,'disclaimer':cfg['disclaimer']}

def export_report(rows,folder,disclaimer):
    folder.mkdir(parents=True,exist_ok=True)
    all_rows={k:rows.read(k) for k in HEADERS}
    for kind,value in all_rows.items(): atomic_json(folder/(kind+'.json'),value)
    parts=['<!doctype html><html lang="ko"><meta charset="utf-8"><title>뉴스 워크플로 결과</title>',
      '<style>body{font:15px sans-serif;margin:32px;background:#f5f7fb;color:#172130}table{border-collapse:collapse;width:100%;background:white}td,th{padding:12px;border:1px solid #d5dce7;text-align:left;white-space:pre-wrap;vertical-align:top}h1{font-size:26px}</style>',
      '<h1>뉴스·공시 처리 결과</h1><p>'+html.escape(disclaimer)+'</p>']
    for kind in ('briefing','stocks'):
        parts.append('<h2>'+html.escape(kind)+'</h2><table><tr>'+''.join('<th>'+html.escape(h)+'</th>' for h in HEADERS[kind] if h!='종목별 판정')+'</tr>')
        for row in all_rows[kind]: parts.append('<tr>'+''.join('<td>'+html.escape(text(row.get(h)))+'</td>' for h in HEADERS[kind] if h!='종목별 판정')+'</tr>')
        parts.append('</table>')
    parts.append('</html>'); (folder/'report.html').write_text(''.join(parts),encoding='utf-8')

def demo(cfg):
    from store import Store
    demo_cfg=copy.deepcopy(cfg); demo_cfg.update(news_batch_wait_seconds=0,disclosure_batch_wait_seconds=0)
    now=parse_time('2026-10-12T09:10:00+09:00')
    folder=ROOT/'demo-output'; store=Store(folder/'db'); rows=LocalRows(store)
    articles=[{'title':'삼성전자 공급계약 체결 공시 테스트','contentSnippet':'삼성전자는 공급계약 체결 사실을 발표했다. 이 문장은 합성 테스트 자료다.',
        'link':'https://example.com/synthetic-news','isoDate':'2026-10-12T08:00:00+09:00','dataType':'news','sourceProvider':'합성 테스트'}]
    def fake_model(batch,*args):
        return [{'date':now.date().isoformat(),'sector':'반도체','title':a['title'],'summary':a['contentSnippet'],'stocks':'삼성전자','sources':a['link'],
                 'is_relevant':True,'relevance_reason':'합성 공급계약 근거 확인','relevance_evidence':a['contentSnippet'],'stock_impacts':[]} for a in batch]
    try:
        result=pipeline(demo_cfg,rows,store,None,now,load_json(ROOT/'companies.json'),collector=lambda *args:articles,
                        model=fake_model,enricher=lambda a,*args:{**a,'articleText':'','contentStatus':'합성 테스트'})
        export_report(rows,folder,'합성 테스트 데이터입니다. '+cfg['disclaimer']); atomic_json(folder/'summary.json',result)
    finally: store.close()
    print('데모 완료 (네트워크·API 키·실제 시트 사용 없음): '+str(folder/'report.html'))

def run_once(cfg,command):
    from store import Store
    store_path=Path(cfg.get('store_root','output/db'))
    if not store_path.is_absolute(): store_path=ROOT/store_path
    # Lock before Store initialization/recovery; protect the original export contract.
    with RunLock(store_path/'portable.lock'):
        store=Store(store_path)
        try:
            store.recover_exports(); http=Http(cfg)
            rows=Sheets(cfg,http) if cfg['storage']=='google_sheets' else LocalRows(store)
            if command=='sync': result={'status':'ok','db':sync_database(rows,store,cfg)}
            else: result=pipeline(cfg,rows,store,http,now_kst(),load_json(ROOT/'companies.json'))
            export_report(rows,ROOT/'output',cfg['disclaimer'])
            atomic_json(ROOT/'output'/'last_run.json',result)
            print(json.dumps(result,ensure_ascii=False,indent=2))
        finally: store.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['check','run','sync','schedule','demo'])
    parser.add_argument('--config',type=Path,default=ROOT/('config.local.json' if (ROOT/'config.local.json').exists() else 'config.json'))
    parser.add_argument('--prompt-secrets',action='store_true')
    args=parser.parse_args(); cfg=load_json(args.config)
    if args.command=='demo':
        with RunLock(ROOT/'demo-output'/'demo.lock'): demo(cfg)
        return
    preflight(cfg,'run' if args.command=='check' else args.command,args.prompt_secrets)
    if args.command=='check':
        import requests, bs4, google.auth, pyarrow, jsonschema
        print('로컬 설정·라이브러리 확인 완료. 네트워크 인증/모델 이용 가능 여부는 실제 실행 시 확인됩니다.'); return
    if args.command!='schedule': run_once(cfg,args.command); return
    print('매일 '+cfg['schedule_kst']+' KST 실행 대기. 이 창을 닫으면 중단됩니다.',flush=True)
    marker=ROOT/'output'/'schedule.json'
    while True:
        current=now_kst(); h,m=map(int,cfg['schedule_kst'].split(':')); day=current.date().isoformat()
        due=current.replace(hour=h,minute=m,second=0,microsecond=0)
        with RunLock(ROOT/'output'/'schedule.lock'):
            previous=load_json(marker) if marker.exists() else {}
            # Record attempts too: failed paid calls must not automatically repeat each minute.
            if current>=due and previous.get('attempted_date')!=day:
                atomic_json(marker,{'attempted_date':day,'status':'running'})
                try: run_once(cfg,'run')
                except Exception:
                    atomic_json(marker,{'attempted_date':day,'status':'failed'})
                    raise
                atomic_json(marker,{'attempted_date':day,'status':'ok'})
        time.sleep(30)

if __name__=='__main__':
    try: main()
    except KeyboardInterrupt: print('\n중단했습니다.'); sys.exit(130)
    except WorkflowError as exc: print('오류: '+str(exc),file=sys.stderr); sys.exit(1)
    except Exception as exc:
        # Third-party exceptions may embed keys, URLs, input text or credentials.
        print('오류: '+type(exc).__name__+' — 원문 오류와 인증정보는 출력하지 않습니다. 설정/디스크/라이브러리를 확인하세요.',file=sys.stderr)
        sys.exit(1)
