const fs=require('fs');const assert=require('assert/strict');
const workflow=JSON.parse(fs.readFileSync(__dirname+'/workflow.json','utf8'));
const node=name=>workflow.nodes.find(n=>n.name===name);
const run=new Function('$input','$',node('전체 건수와 일봉 검증1').parameters.jsCode);
const config={beginDate:'20261001',endDate:'20261014',source:'FSC_STOCK_V2',requestedAt:'2026-10-15T13:00:00+09:00'};
const row={basDt:'20261014',srtnCd:'005930',itmsNm:'합성 테스트',mrktCtg:'KOSPI',mkp:'100',hipr:'120',lopr:'90',clpr:'110',trqu:'1000',trPrc:'9007199254740993',mrktTotAmt:'9007199254740994',lstgStCnt:'10000'};
function page(rows,total=rows.length,num=1,size=1000) {return {response:{header:{resultCode:'00'},body:{totalCount:total,numOfRows:size,pageNo:num,items:{item:rows}}}};}
function execute(pages) {return run({all:()=>pages.map(json=>({json}))},()=>({first:()=>({json:config})}))[0].json;}
let count=0; function test(name,fn) {fn();count++;console.log('PASS '+name);}
test('valid and large integer preservation',()=>{let r=execute([page([row])]);assert.equal(r.rows[0].ticker,'005930');assert.equal(r.rows[0].value,'9007199254740993');assert.equal(r.dbWritten,false);});
test('multiple pages complete',()=>assert.equal(execute([page([row],2,1,1),page([{...row,srtnCd:'000001'}],2,2,1)]).selectedRows,2));
test('single object response',()=>{let p=page([row]);p.response.body.items.item=row;assert.equal(execute([p]).selectedRows,1);});
test('missing last page rejected',()=>assert.throws(()=>execute([page([row],2,1,1)]),/미완료/));
test('short page rejected',()=>assert.throws(()=>execute([page([row],2)]),/누락/));
test('duplicate rejected',()=>assert.throws(()=>execute([page([row,row])]),/중복/));
test('business error rejected',()=>assert.throws(()=>execute([{response:{header:{resultCode:'30'}}}]),/API 업무/));
test('empty response rejected',()=>assert.throws(()=>execute([page([])]),/조회 결과 없음/));
test('OHLC rejected',()=>assert.throws(()=>execute([page([{...row,hipr:'50'}])]),/OHLC/));
test('halted candle accepted',()=>assert.equal(execute([page([{...row,mkp:'0',hipr:'0',lopr:'0',trqu:'0'}])]).rows[0].halted,true));
test('unsafe numeric JSON rejected',()=>assert.throws(()=>execute([page([{...row,trPrc:9007199254740993}])]),/안전하지/));
test('future date rejected',()=>assert.throws(()=>execute([page([{...row,basDt:'20261015'}])]),/기준일/));
test('KONEX excluded after total validation',()=>assert.equal(execute([page([row,{...row,srtnCd:'000001',mrktCtg:'KONEX'}])]).selectedRows,1));
test('alphanumeric stock code accepted',()=>assert.equal(execute([page([{...row,srtnCd:'0001A0'}])]).rows[0].ticker,'0001A0'));
test('malformed stock code rejected',()=>assert.throws(()=>execute([page([{...row,srtnCd:'000-10'}])]),/종목 코드/));
const w=JSON.parse(fs.readFileSync(__dirname+'/workflow.json','utf8'));const expr=w.nodes.find(n=>n.type.endsWith('httpRequest')).parameters.options.pagination.pagination.completeExpression;const done=new Function('$response','return '+expr.slice(3,-2));
test('pagination keeps page 8 of 9',()=>assert.equal(done({body:page([],8609,8,1000)}),false));
test('pagination ends at page 9',()=>assert.equal(done({body:page([],8609,9,1000)}),true));



const code=node('중복 대조 및 저장 준비').parameters.jsCode;
const plan=(rows,db=[])=>new Function('$','$input',code)(()=>({first:()=>({json:{rows,source:'FSC_STOCK_V2',beginDate:'20260918',endDate:'20260921',latestDate:'2026-09-21',apiRows:rows.length,selectedRows:rows.length}})}),{all:()=>db.map(json=>({json}))});
const r={date:'2026-09-21',ticker:'0001A0',name:"종목 ' 테스트",market:'KOSDAQ',open_raw:'100',high_raw:'110',low_raw:'90',close_raw:'105',volume_raw:'123',value:'9007199254740993',marketcap:'9223372036854775000',shares:'1000'};
const dept={kind:'dept',payload:{ticker:r.ticker,dept:'우량기업부'}};
const first=plan([r],[dept]);assert.equal(first.length,3);assert.equal(first[0].json.report.inserted.store_prices,1);
assert.equal(JSON.parse(first[1].json.params[0])[0].dept,'우량기업부');
assert.equal(JSON.parse(first[0].json.params[0])[0].value,r.value);
assert.ok(!first[1].json.query.includes(r.name));
const db=first.map(x=>({kind:x.json.query.match(/INSERT INTO `([^`]+)`/)[1],payload:JSON.parse(x.json.params[0])[0]}));
const repeated=plan([r],[dept,...db]);assert.equal(repeated.length,1);assert.equal(repeated[0].json.query,'SELECT 1 AS no_new_rows');assert.equal(repeated[0].json.report.unchanged.store_prices,1);
assert.throws(()=>plan([{...r,close_raw:'106'}],[dept,...db]),/기존 값 충돌/);
assert.equal(plan([r])[0].json.report.missingDepartment,1);
assert.equal(plan([{...r,date:'2026-09-18'}])[0].json.report.inserted.store_prices,0);
assert.throws(()=>plan([{...r,date:'2026-09-18'}],[{kind:'store_krx_daily',payload:{...r,date:'2026-09-18',close_raw:'99'}}]),/대조 불일치/);
console.log('DB plan checks passed: append, duplicate, conflict, bigint, parameter binding, department, historical protection, baseline mismatch');


test('schedule is daily 13:30 KST',()=>{
 assert.equal(workflow.settings.timezone,'Asia/Seoul');
 assert.deepEqual(node('매일 13시 30분 KST').parameters.rule.interval,[{field:'cronExpression',expression:'0 30 13 * * *'}]);
});
test('export has no credentials or execution data and is inactive',()=>{
 assert.equal(workflow.active,false); assert.deepEqual(workflow.pinData,{});
 assert.ok(workflow.nodes.every(n=>!n.credentials));
 assert.ok(!workflow.id && !workflow.meta && !workflow.versionId && !workflow.staticData);
});
test('writes are transactional and parameterized',()=>{
 const p=node('일봉 3개 테이블 저장').parameters;
 assert.equal(p.options.queryBatching,'transaction');
 assert.equal(p.options.queryReplacement,'={{ $json.params }}');
 assert.ok(!node('일봉 3개 테이블 저장').continueOnFail);
});
test('requested end date is not the observed latest date',()=>{
 const result=execute([page([{...row,basDt:'20261013'}])]);
 assert.equal(result.endDate,'20261014');
 assert.equal(result.latestDate,'2026-10-13');
 assert.equal(result.latestMatchesRequestedEnd,false);
});
test('rolling window uses KST yesterday and seven days ago',()=>{
 const days=[];
 const clock={setZone(zone){assert.equal(zone,'Asia/Seoul');return this;},
 minus({days:n}){days.push(n);return {toFormat:()=>n===1?'20261014':'20261008'};},
 toFormat:()=> '20261015', toISO:()=> '2026-10-15T13:30:00+09:00'};
 const out=new Function('$now',node('조회 기간 설정1').parameters.jsCode)(clock)[0].json;
 assert.deepEqual(days,[1,7]);assert.equal(out.beginDate,'20261008');assert.equal(out.endDate,'20261014');
});
for(const n of workflow.nodes){
 if(n.parameters.jsCode)new Function('$input','$','$now',n.parameters.jsCode);
 for(const outputs of Object.values(workflow.connections[n.name]||{})){
  for(const edges of outputs)for(const edge of edges)assert.ok(node(edge.node),'missing target '+edge.node);
 }
}
console.log(count+' API/config cases + DB boundary checks passed; no API or DB was contacted.');
