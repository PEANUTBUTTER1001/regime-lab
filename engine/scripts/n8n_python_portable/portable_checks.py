import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import *
from runner import ROOT, load_json, pipeline, LocalRows, Sheets, Http, RunLock, model_output, enrich, preflight
from store import Store

NOW=parse_time('2026-10-12T09:10:00+09:00')
COMPANIES=load_json(ROOT/'companies.json')

def article(**kwargs):
    a={'title':'삼성전자 영업이익 10% 증가 발표','contentSnippet':'삼성전자는 영업이익이 10% 증가했다고 발표했다.',
       'link':'https://example.com/a','isoDate':'2026-10-12T08:00:00+09:00','dataType':'news','sourceProvider':'테스트'}
    a.update(kwargs)
    a['rawRecord']=prepare_raw([a],[],[],['삼성전자'],NOW)[0]
    return a

def output(a,**kwargs):
    value={'date':'2026-10-12','sector':'반도체','title':a['title'],'summary':a['contentSnippet'],'stocks':'삼성전자',
        'sources':a['link'],'is_relevant':True,'relevance_reason':'영업이익 변화 확인','relevance_evidence':a['contentSnippet'],
        'stock_impacts':[{'stock':'삼성전자','impact':'상승 요인','reason':'영업이익 증가 발표','uncertainty':'이후 실적은 미확인','evidence':a['contentSnippet']}]}
    value.update(kwargs); return value

class CoreTests(unittest.TestCase):
    def verify(self,a,value): return validate_results([a],[value],COMPANIES['companies'],NOW)[0]
    def test_monday_window_and_timezone(self):
        self.assertEqual(period(NOW)['bgn_de'],'20261009')
        self.assertEqual(period(parse_time('2026-10-12T00:10:00Z')),period(NOW))
        self.assertEqual(period(parse_time('2026-10-13T09:10:00+09:00'))['bgn_de'],'20261012')
    def test_future_and_old_rejected(self):
        values=[article(),article(link='https://example.com/b',isoDate='2026-10-13'),article(link='https://example.com/c',isoDate='2026-10-01')]
        self.assertEqual(len(normalize_articles(values,NOW,7)),1)
    def test_normalize_tracking_duplicates(self):
        a=article(link='http://www.hankyung.com/article/1?utm_source=x')
        b=article(link='https://www.hankyung.com/article/1#heading')
        self.assertEqual(len(normalize_articles([a,b],NOW,7)),1)
    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError): parse_time('2026-10-12T09:10:00')
    def test_raw_first_seen_and_completed_preserved(self):
        a=article(); old=copy.deepcopy(a['rawRecord']); old.update({'최초 수집 시각':'2026-10-01T09:00:00+09:00','처리 상태':'완료'})
        raw=prepare_raw([a],[old],[],[],NOW)
        self.assertEqual(raw[0]['최초 수집 시각'],old['최초 수집 시각'])
        self.assertFalse(restore_articles([a],raw)[0]['isNew'])
    def test_naver_without_company_skips(self):
        a=article(title='대학 강의 안내',contentSnippet='교육 행사',sourceProvider='NAVER API HUB')
        raw=prepare_raw([a],[],[],['삼성전자'],NOW)
        self.assertEqual(raw[0]['처리 상태'],'종목 없음')
        self.assertFalse(restore_articles([a],raw)[0]['isNew'])
    def test_count_and_source_rejected(self):
        a=article()
        with self.assertRaises(WorkflowError): validate_results([a],[],COMPANIES['companies'],NOW)
        with self.assertRaises(WorkflowError): self.verify(a,output(a,sources='https://example.com/other'))
    def test_reason_conflict_rejected(self):
        a=article()
        with self.assertRaises(WorkflowError): self.verify(a,output(a,reason='다른 이유'))
    def test_unsupported_number_fallback(self):
        a=article(); value=self.verify(a,output(a,summary='삼성전자 영업이익 999% 증가'))
        self.assertEqual(value['issue']['summary'],a['contentSnippet'])
    def test_advice_fallback(self):
        a=article(); self.assertEqual(self.verify(a,output(a,summary='삼성전자 매수하세요'))['issue']['summary'],a['contentSnippet'])
    def test_ungrounded_company_removed(self):
        a=article(); self.assertFalse(self.verify(a,output(a,stocks='SK하이닉스'))['accepted'])
    def test_relevance_evidence_rejected(self):
        a=article(); self.assertFalse(self.verify(a,output(a,relevance_evidence='기사에 전혀 없는 내용'))['accepted'])
    def test_impact_unverified_downgraded(self):
        a=article(); value=output(a); value['stock_impacts'][0]['evidence']='거짓 근거 문자열을 넣었습니다'
        self.assertEqual(self.verify(a,value)['issue']['impact'],'판단 어려움')
    def test_valid_results(self):
        a=article(); result=self.verify(a,output(a)); self.assertTrue(result['accepted']); self.assertEqual(result['issue']['impact'],'상승 요인')
    def test_disclosure_no_direction_guess(self):
        a=article(dataType='disclosure',company='삼성전자',stockCode='005930',reportName='공급계약')
        result=self.verify(a,disclosure_output([a],NOW)[0]); self.assertEqual(result['issue']['impact'],'판단 어려움')
    def test_dart_empty_and_failure_distinguished(self):
        self.assertEqual(dart_articles([{'status':'013'}],period(NOW)),[])
        with self.assertRaises(WorkflowError): dart_articles([{'status':'020','message':'secret'}],period(NOW))
    def test_naver_bad_response_fails(self):
        with self.assertRaises(WorkflowError): naver_articles([{}],period(NOW))
    def test_caps(self):
        values=[{**article(link=f'https://example.com/{i}'),'isNew':True} for i in range(35)]
        self.assertEqual(len(select_articles(values,30,10)),30)
    def test_aggregate_mixed_and_duplicates(self):
        a=article(); r=briefing_row(self.verify(a,output(a))['issue'])
        other=copy.deepcopy(r); other['출처']='https://example.com/b'
        signals=json.loads(other['종목별 판정']); signals[0]['impact']='하락 요인'; other['종목별 판정']=json.dumps(signals)
        result=aggregate_stocks([r,r,other],NOW)
        self.assertEqual(len(result),1); self.assertEqual(result[0]['예상 영향'],'혼재'); self.assertEqual(len(result[0]['출처'].splitlines()),2)

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.folder=Path(self.temp.name)
        self.store=Store(self.folder/'db'); self.rows=LocalRows(self.store)
        self.cfg=load_json(ROOT/'config.json'); self.cfg.update(news_batch_wait_seconds=0,disclosure_batch_wait_seconds=0)
    def tearDown(self): self.store.close(); self.temp.cleanup()
    def run_pipeline(self,values,model):
        return pipeline(self.cfg,self.rows,self.store,None,NOW,COMPANIES,collector=lambda *args:values,
            model=model,enricher=lambda a,*args:{**a,'contentStatus':'합성 검증','articleText':''})
    def test_repeat_does_not_reinvoke_model(self):
        a=article(); model=Mock(return_value=[output(a)])
        first=self.run_pipeline([a],model); second=self.run_pipeline([a],model)
        self.assertEqual(first['saved'],1); self.assertEqual(second['saved'],0); self.assertEqual(model.call_count,1)
        self.assertEqual(len(self.rows.read('briefing')),1)
    def test_failure_keeps_pending(self):
        a=article(); model=Mock(side_effect=WorkflowError('synthetic failure'))
        with self.assertRaises(WorkflowError): self.run_pipeline([a],model)
        self.assertEqual(self.rows.read('raw')[0]['처리 상태'],'대기'); self.assertEqual(self.rows.read('briefing'),[])
    def test_disclosure_skips_model(self):
        a=article(dataType='disclosure',company='삼성전자',stockCode='005930',reportName='공급계약'); model=Mock(side_effect=AssertionError('must not call'))
        self.run_pipeline([a],model); model.assert_not_called()
    def test_write_failure_never_marks_complete(self):
        a=article(); original=self.rows.write
        def fail(kind,values):
            if kind=='briefing': raise WorkflowError('synthetic write failure')
            return original(kind,values)
        with patch.object(self.rows,'write',side_effect=fail):
            with self.assertRaises(WorkflowError): self.run_pipeline([a],Mock(return_value=[output(a)]))
        self.assertEqual(self.rows.read('raw')[0]['처리 상태'],'대기')
    def test_lock_conflict(self):
        with RunLock(self.folder/'test.lock'):
            with self.assertRaises(WorkflowError):
                with RunLock(self.folder/'test.lock'): pass
    def test_sheet_raw_idempotent_addresses(self):
        sheet=object.__new__(Sheets); sheet.cfg=self.cfg; sheet.title=lambda k:"'raw'"
        sheet.values=lambda k:(HEADERS['raw'],[['title','summary','https://example.com/a']])
        sheet.request=Mock(return_value={})
        sheet.write('raw',[{'출처':'https://example.com/a','원기사 제목':'=not-a-formula'},{'출처':'https://example.com/b','원기사 제목':'new'}])
        body=sheet.request.call_args.kwargs['json']
        self.assertEqual(body['valueInputOption'],'RAW'); self.assertEqual([x['range'] for x in body['data']],["'raw'!A2","'raw'!A3"])
    def test_model_schema_failure_no_repair_call(self):
        client=Mock(); client.json.return_value={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'[]'}]}}]}
        with patch.dict('os.environ',{'GEMINI_API_KEY':'synthetic'}):
            with self.assertRaises(WorkflowError): model_output([article()],self.cfg,client,NOW)
        self.assertEqual(client.json.call_count,1)
    def test_article_redirect_failure_falls_back(self):
        client=Mock(); client.request.side_effect=WorkflowError('HTTP 302')
        cfg={**self.cfg,'request_interval_seconds':0}
        result=enrich(article(link='https://www.hankyung.com/article/123'),cfg,client)
        self.assertEqual(result['articleText'],''); self.assertIn('RSS만',result['contentStatus'])
    def test_auth_redirect_not_followed_and_secret_not_logged(self):
        client=Http(self.cfg); response=Mock(status_code=302); client.session.request=Mock(return_value=response)
        with self.assertRaises(WorkflowError) as ctx: client.request('GET','https://example.com/?key=secret',label='API')
        self.assertNotIn('secret',str(ctx.exception)); self.assertFalse(client.session.request.call_args.kwargs['allow_redirects'])



class PublicConfigTests(unittest.TestCase):
    def test_placeholder_fails_before_credentials(self):
        cfg=load_json(ROOT/'config.json')
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaisesRegex(WorkflowError,'spreadsheet_id'):
                preflight(cfg,'sync')
    def test_duplicate_tabs_rejected(self):
        cfg=load_json(ROOT/'config.json')
        cfg.update(spreadsheet_id='synthetic-sheet',sheet_ids={'raw':0,'briefing':0,'stocks':2})
        with self.assertRaisesRegex(WorkflowError,'sheet_ids'):
            preflight(cfg,'sync')
    def test_local_sync_needs_no_google_credentials(self):
        cfg=load_json(ROOT/'config.json'); cfg['storage']='local'
        with patch.dict('os.environ',{},clear=True): preflight(cfg,'sync')

if __name__=='__main__': unittest.main()
