"""Pure transformations ported from the two exported workflows. No I/O."""
from __future__ import annotations
import copy
import html
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

KST = timezone(timedelta(hours=9))
SECTORS = ['반도체','IT·소프트웨어','바이오·헬스케어','자동차·배터리','조선·방산','에너지·화학','금융','소비재·유통','시장 공통 이슈','기타']
IMPACTS = ['상승 요인','하락 요인','혼재','판단 어려움']
ALIASES = {'삼전':['삼성전자'],'닉스':['SK하이닉스'],'하이닉스':['SK하이닉스'],'삼전닉스':['삼성전자','SK하이닉스']}
HEADERS = {
 'raw':['원기사 제목','RSS 요약','출처','게시 시각','최초 수집 시각','수집 출처','처리 상태','처리 사유','마지막 처리 시각'],
 'briefing':['날짜','업종','핵심 이슈','요약','관련 종목','출처','예상 영향','판단 근거','변수·불확실성','종목별 판정'],
 'stocks':['날짜','종목','예상 영향','판단 근거','변수·불확실성','출처','집계키']}
KEYS = {'raw':'출처','briefing':'출처','stocks':'집계키'}

class WorkflowError(Exception):
    """Only curated, secret-free messages may be passed here."""

def text(value):
    return '' if value is None else str(value)

def norm(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text(value))).upper()

def clean(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', '', text(value)))).strip()

def parse_time(value):
    if isinstance(value, datetime):
        result = value
    else:
        raw = text(value).strip()
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
            return datetime.fromisoformat(raw).replace(tzinfo=KST)
        try:
            result = datetime.fromisoformat(raw.replace('Z','+00:00'))
        except ValueError:
            result = parsedate_to_datetime(raw)
    if result.tzinfo is None:
        raise ValueError('Timezone is required')
    return result.astimezone(KST)

def period(now):
    end = parse_time(now)
    start = (end-timedelta(days=3 if end.weekday()==0 else 1)).replace(hour=0,minute=0,second=0,microsecond=0)
    return {'fromISO':start.isoformat(timespec='milliseconds'),'toISO':end.isoformat(timespec='milliseconds'),
            'bgn_de':start.strftime('%Y%m%d'),'end_de':end.strftime('%Y%m%d')}

def url_key(value):
    value=re.sub(r'#.*$', '', text(value).strip())
    value=re.sub(r'^https?://', '', value,flags=re.I)
    value=re.sub(r'^www\.', '', value,flags=re.I)
    value=re.sub(r'([?&])(utm_[^=]*|fbclid|gclid|ref|from|utm)=[^&]*',r'\1',value,flags=re.I)
    return re.sub(r'[?&]+$','',value)

def normalize_articles(articles, now, lookback):
    seen=set(); result=[]
    for a in articles:
        if a.get('_sourceEmpty') or not a.get('link'): continue
        link=re.sub(r'#.*$','',text(a['link']).strip())
        parts=link.split('?',1)
        if len(parts)==2:
            qs='&'.join(p for p in parts[1].split('&') if not re.match(r'(utm_[^=]*|fbclid|gclid|ref|from|utm)=',p,re.I))
            link=parts[0]+('?' + qs if qs else '')
        link=re.sub(r'^http://(?:www\.)?(hankyung\.com|mk\.co\.kr)/',r'https://www.\1/',link,flags=re.I)
        if not re.match(r'^https?://[^/\s]+',link,re.I) or not text(a.get('title')).strip(): continue
        key=url_key(link)
        if key in seen: continue
        seen.add(key)
        try: published=parse_time(a.get('isoDate') or a.get('pubDate'))
        except (ValueError,TypeError,OverflowError): continue
        # Explicitly reject future timestamps as well as expired entries.
        if not now-timedelta(days=lookback) <= published <= now: continue
        provider=a.get('sourceProvider') or ('한국경제 RSS' if 'hankyung.com/' in link else '매일경제 RSS' if 'mk.co.kr/' in link else 'RSS')
        result.append({**a,'link':link,'dataType':a.get('dataType') or 'news','sourceProvider':provider})
    return result

def naver_articles(responses, window):
    start,end=parse_time(window['fromISO']),parse_time(window['toISO']); result=[]
    for r in responses:
        if not isinstance(r.get('items'),list): raise WorkflowError('네이버 응답에 items 배열이 없습니다.')
        for a in r['items']:
            try: stamp=parse_time(a.get('pubDate'))
            except (ValueError,TypeError,OverflowError): continue
            link=text(a.get('originallink') or a.get('link')).strip()
            if not start<=stamp<=end or not re.match(r'^https?://',link,re.I): continue
            result.append({'title':clean(a.get('title')),'contentSnippet':clean(a.get('description')),'link':link,
                'isoDate':stamp.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'),
                'sourceProvider':'NAVER API HUB','dataType':'news'})
    return result

def dart_articles(responses, window):
    result=[]
    for r in responses:
        if r.get('status')=='013': continue
        if r.get('status')!='000' or not isinstance(r.get('list'),list):
            raise WorkflowError('OpenDART 조회 실패. 인증·호출 한도·응답 형식을 확인하세요.')
        for a in r['list']:
            if a.get('corp_cls') not in ['Y','K'] or not re.fullmatch(r'\d{6}',text(a.get('stock_code'))): continue
            if not re.fullmatch(r'\d{14}',text(a.get('rcept_no'))) or not re.fullmatch(r'\d{8}',text(a.get('rcept_dt'))): continue
            if not window['bgn_de']<=a['rcept_dt']<=window['end_de']: continue
            try: day=datetime.strptime(a['rcept_dt'],'%Y%m%d').date().isoformat()
            except ValueError: raise WorkflowError('OpenDART 접수일 형식 오류') from None
            if not a.get('corp_name') or not a.get('report_nm'): raise WorkflowError('OpenDART 회사명/보고서명 누락')
            result.append({'title':f"[공시] {a['corp_name']} · {a['report_nm']}",
                'contentSnippet':f"{a['corp_name']}의 {a['report_nm']} 공시가 {day} 접수됨. 종목코드: {a['stock_code']}. 공시 본문은 미확보로 세부 수치·내용·주가 방향 확인이 필요함.",
                'link':'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+a['rcept_no'],'isoDate':day,'receiptDate':day,
                'stockCode':a['stock_code'],'company':a['corp_name'],'reportName':a['report_nm'],
                'sourceProvider':'OpenDART','dataType':'disclosure','sourceTimeMeaning':'접수일(일 단위), 공개 시각 아님'})
    return result

def prepare_raw(articles, old_rows, saved_rows, names, now):
    old={url_key(r['출처']):r for r in old_rows if r.get('출처')}
    saved={url_key(u) for r in saved_rows for u in text(r.get('출처')).split()}
    normalized=[norm(n) for n in names]; stamp=now.isoformat(timespec='milliseconds'); result=[]
    for a in articles:
        link=a['link']; previous=old.get(url_key(link),{}); exists=url_key(link) in saved
        row={'원기사 제목':text(a.get('title')),'RSS 요약':text(a.get('contentSnippet')),'출처':previous.get('출처') or link,
             '게시 시각':text(a.get('isoDate') or a.get('pubDate')),'최초 수집 시각':previous.get('최초 수집 시각') or stamp,
             '수집 출처':(a.get('sourceProvider') or 'RSS')+' | '+urlsplit(link).netloc,
             '처리 상태':previous.get('처리 상태') or ('기존 결과' if exists else '대기'),
             '처리 사유':previous.get('처리 사유') or ('결과 시트에 이미 저장된 링크' if exists else ''),
             '마지막 처리 시각':previous.get('마지막 처리 시각') or ''}
        if exists and row['처리 상태']=='대기': row.update({'처리 상태':'기존 결과','처리 사유':'결과 시트 저장 확인'})
        title_summary=text(a.get('title'))+' '+text(a.get('contentSnippet'))
        mentions=bool(re.search('삼전|하이닉스',title_summary)) or any(len(n)>=2 and n in norm(title_summary) for n in normalized)
        if row['처리 상태']=='대기' and a.get('sourceProvider')=='NAVER API HUB' and not mentions:
            row.update({'처리 상태':'종목 없음','처리 사유':'[선별v2] 네이버 제공 제목·요약에 기존 국내 상장종목명 또는 등록 별칭이 없어 AI 호출 생략. 원문 본문은 확인하지 않음.','마지막 처리 시각':stamp})
        result.append(row)
    return result

def restore_articles(articles, rows):
    by_url={url_key(a['link']):a for a in articles}; result=[]
    for r in rows:
        fresh=r['처리 상태'] not in ['완료','기존 결과'] and (r['처리 상태'] not in ['종목 없음','관련성 낮음'] or '[선별v2]' not in r['처리 사유'])
        result.append({**by_url.get(url_key(r['출처']),{}),'title':r['원기사 제목'],'contentSnippet':r['RSS 요약'],
                       'link':r['출처'],'isoDate':r['게시 시각'],'first_seen_at':r['최초 수집 시각'],'rawRecord':r,'isNew':fresh})
    return result

def select_articles(articles, news_limit, disclosure_limit):
    fresh=[a for a in articles if a['isNew']]
    recent=lambda a: text(a.get('isoDate'))
    return sorted([a for a in fresh if a.get('dataType')!='disclosure'],key=recent,reverse=True)[:news_limit]+sorted([a for a in fresh if a.get('dataType')=='disclosure'],key=recent,reverse=True)[:disclosure_limit]

def disclosure_output(articles, now):
    return [{'date':now.date().isoformat(),'sector':'기타','title':a['title'],'summary':a['contentSnippet'],
             'stocks':a['company'],'sources':a['link'],'is_relevant':True,'relevance_reason':'공시 접수정보 확인',
             'relevance_evidence':a['title'],'stock_impacts':[]} for a in articles]

def merge_impacts(values):
    s=set(values)
    return '혼재' if '혼재' in s or {'상승 요인','하락 요인'}<=s else '상승 요인' if '상승 요인' in s else '하락 요인' if '하락 요인' in s else '판단 어려움'

def validate_results(articles, output, companies, now):
    """Keep numeric/source/identity/relevance/impact checks from original nodes."""
    if not isinstance(output,list) or len(output)!=len(articles): raise WorkflowError('입력·출력 기사 수 불일치: 결과 저장 중단')
    lookup={}
    normalize_name=lambda s: norm(re.sub(r'^(?:주식회사|\(주\)|주식회사\s*)','',unicodedata.normalize('NFKC',text(s))))
    for name,code,market in companies:
        bucket=lookup.setdefault(normalize_name(name),[])
        if not any(v['code']==code for v in bucket): bucket.append({'name':name,'code':code,'market':market})
    by_url={a['link']:a for a in articles}; seen=set(); result=[]
    numbers=lambda s: {n.replace(',','') for n in re.findall(r'\d+(?:[,\.]\d+)*',text(s))}
    for original in output:
        issue=copy.deepcopy(original)
        canonical,alias=issue.get('relevance_reason'),issue.get('reason')
        if canonical is not None and alias is not None and canonical!=alias: raise WorkflowError('관련성 이유 필드 충돌')
        reason=canonical if canonical is not None else alias
        if not isinstance(reason,str) or not reason.strip(): raise WorkflowError('관련성 이유 누락')
        a=by_url.get(issue.get('sources'))
        if not a or issue['sources'] in seen: raise WorkflowError('AI 출처 불일치 또는 중복')
        seen.add(issue['sources'])
        if issue.get('sector') not in SECTORS: raise WorkflowError('허용되지 않은 업종')
        raw='\n'.join(text(a.get(k)) for k in ['title','contentSnippet','articleText'])
        warnings=[a['contentStatus']] if a.get('contentStatus') else []
        if issue.get('title')!=a['title']: warnings.append('기사 제목을 원래 제목으로 복원')
        summary=text(issue.get('summary')).strip()
        if not summary or numbers(summary)-numbers(raw) or re.search(r'(매수|매도|투자)하세요|수익을?\s*보장|매수를?\s*추천|매도를?\s*추천',summary):
            summary=a.get('contentSnippet') or a['title']; warnings.append('요약 검증 실패: RSS 원문 발췌로 대체')
        candidates=[s.strip() for s in re.split(r'[,，;；\r\n]+',text(issue.get('stocks'))) if s.strip()]
        grounded=[s for s in candidates if norm(s) in norm(raw)]
        if len(grounded)!=len(candidates): warnings.append('입력에 없는 기업명 후보 제외')
        disclosure=a.get('dataType')=='disclosure'
        if disclosure:
            grounded=[a['company']]; summary=a['contentSnippet']; issue['stock_impacts']=[]
            material=bool(re.search('사업보고서|반기보고서|분기보고서|주요사항보고서|증권신고서|실적|손익|매출|영업|공급계약|수주|배당|자기주식|증자|감자|전환사채|신주인수권|합병|분할|양수|양도|취득|처분|소송|회생|파산|부도|상장폐지|거래정지|불성실공시|투자판단|횡령|배임',a.get('reportName','')))
            issue['is_relevant']=material; reason='주요 기업 공시 접수. 세부 내용은 원문 확인 필요' if material else '정기 지분 보고·행정 공시 등으로 브리핑 우선순위 낮음'; issue['relevance_evidence']=a['title']
            warnings.append('공시 제목·접수정보만 확보, 본문·공개 시각 미확보')
        if type(issue.get('is_relevant')) is not bool: raise WorkflowError('관련성 판정 형식 오류')
        include=issue['is_relevant']; evidence=text(issue.get('relevance_evidence')).strip()
        if include and (not evidence or len(evidence)>300 or norm(evidence) not in norm(raw)):
            include=False; reason='입력 기사에서 투자 관련성 근거를 확인할 수 없음'
        promotion=re.search(r'수상|평가.{0,20}만점|웨비나|세미나|전시회.{0,12}(참가|참여)|프로모션|할인행사|할인 행사|출시 기념|기부|사회공헌|업무협약|MOU',a['title'],re.I)
        substantive=re.search(r'영업이익|순이익|잠정실적|실적 발표|매출액|공급계약|계약금|수주|품목허가|임상.{0,16}(결과|성공|실패|중단)|유상증자|자사주|배당|리콜|생산.{0,12}(중단|재개|증설)|과징금|소송|매각|인수합병',raw)
        if promotion and not substantive: include=False; reason='수상·행사·홍보·업무협약 중심이며 구체적인 실적·계약·규제 사건 근거 없음'
        matched={}
        for candidate in grounded:
            for name in ALIASES.get(candidate,[candidate]):
                if re.search(r'["{}:]',name): continue
                found=lookup.get(normalize_name(name),[])
                if len(found)==1: matched[found[0]['code']]=found[0]
        verified=[v for v in matched.values() if not disclosure or v['code']==a.get('stockCode')]
        impacts=issue.get('stock_impacts') or []; signals=[]; common='주가 선반영 여부·시장 수급은 확인되지 않음'
        if not isinstance(impacts,list) or any(not isinstance(c,dict) for c in impacts): raise WorkflowError('종목별 판정 형식 오류')
        for v in verified:
            matches=[c for c in impacts if any(norm(s)==norm(v['name']) for s in ALIASES.get(c.get('stock'),[c.get('stock')]))]
            c=matches[0] if not disclosure and len(matches)==1 else {}; ev=text(c.get('evidence')).strip()
            unsafe=re.search(r'(?:상승|하락)\s*확률|예상\s*수익률|수익\s*보장|매수하세요|매도하세요',text(c.get('reason'))+' '+text(c.get('uncertainty')))
            valid=bool(c and c.get('impact') in IMPACTS and text(c.get('reason')).strip() and text(c.get('uncertainty')).strip() and not unsafe and (c['impact']=='판단 어려움' or 10<=len(ev)<=300 and norm(ev) in norm(raw)))
            signals.append({'stock':v['name'],'code':v['code'],'impact':c['impact'] if valid else '판단 어려움',
                'reason':c['reason'] if valid else '공시 접수 사실만 확인, 공시 본문 확인 전 방향 판단 보류' if disclosure else '해당 종목의 영향 근거를 입력 기사에서 충분히 확인하지 못함',
                'uncertainty':(c['uncertainty'] if valid else '추가 기사·공시 확인 필요')+'; '+common,'evidence':ev if valid else ''})
        final={'date':now.date().isoformat(),'sector':issue['sector'],'title':a['title'],'summary':summary,
            'stocks':', '.join(v['name'] for v in verified),'sources':a['link'],'impact':merge_impacts(s['impact'] for s in signals),
            'impactReason':'\n'.join(s['stock']+': '+s['reason'] for s in signals) or '확인된 국내 상장 종목 없음',
            'impactUncertainty':'\n'.join(dict.fromkeys(s['uncertainty'] for s in signals)) or common,
            'stockImpactsJson':json.dumps(signals,ensure_ascii=False,separators=(',',':'))}
        record={**a['rawRecord'],'처리 상태':'관련성 낮음' if not include else '완료' if verified else '종목 없음',
            '처리 사유':'; '.join(x for x in ['[선별v2]',reason,('국내 상장 종목 확인' if verified else '확인된 국내 상장 종목 없음') if include else '',*warnings] if x),
            '마지막 처리 시각':now.isoformat(timespec='milliseconds')}
        result.append({'issue':final,'record':record,'accepted':bool(include and verified)})
    return result

def briefing_row(issue):
    fields=['date','sector','title','summary','stocks','sources','impact','impactReason','impactUncertainty','stockImpactsJson']
    return dict(zip(HEADERS['briefing'],[issue.get(k,'') for k in fields]))

def aggregate_stocks(rows, now):
    today=now.date().isoformat(); groups={}; seen=set()
    for r in rows:
        rawdate=r.get('날짜','')
        if isinstance(rawdate,(float,int)):
            day=(datetime(1899,12,30)+timedelta(days=rawdate)).date().isoformat()
        else:
            m=re.match(r'^(\d{4})\D+(\d{1,2})\D+(\d{1,2})',text(rawdate))
            day=f'{int(m[1]):04}-{int(m[2]):02}-{int(m[3]):02}' if m else ''
        if day!=today: continue
        url=text(r.get('출처')).strip()
        if not url or url in seen: continue
        seen.add(url)
        try: signals=json.loads(r.get('종목별 판정') or '[]')
        except (ValueError,TypeError): raise WorkflowError('저장된 종목별 판정 JSON 손상') from None
        if not isinstance(signals,list): raise WorkflowError('종목별 판정 배열 형식 오류')
        for s in signals:
            if not isinstance(s,dict) or not s.get('code') or not s.get('stock') or s.get('impact') not in IMPACTS: continue
            key=today+'|'+s['code']; g=groups.setdefault(key,{'stock':s['stock'],'signals':[],'urls':[]})
            g['signals'].append(s); g['urls'].append(url)
    join=lambda values:'\n'.join(dict.fromkeys(text(v) for v in values))[:20000]
    return [{'날짜':today,'종목':g['stock'],'예상 영향':merge_impacts(s['impact'] for s in g['signals']),
             '판단 근거':join(f"[{s['impact']}] {s['reason']}" for s in g['signals']),
             '변수·불확실성':join(['분석된 뉴스 요인의 합산이며 실제 등락 예측이 아님',*[s['uncertainty'] for s in g['signals']]]),
             '출처':join(g['urls']),'집계키':key} for key,g in sorted(groups.items(),key=lambda kv:kv[1]['stock'])]
