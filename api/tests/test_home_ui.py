"""Real view/component logic with DOM/API doubles; actual browser QA is separate."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('scenario', ['picker', 'picker_failure', 'picker_retry', 'home', 'home_empty', 'home_partial', 'home_dispose', 'chart'])
def test_home_and_picker(scenario):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for UI logic checks')
    script = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const scenario=process.argv[1];
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.events={};this.value='';this.hidden=false;this.textContent='';}
  setAttribute(k,v){this.attrs[k]=String(v);if(k==='id')this.id=v;if(k==='value')this.value=v;if(k==='hidden')this.hidden=true;}
  removeAttribute(k){delete this.attrs[k];}
  append(...xs){this.children.push(...xs.flat(Infinity).filter(x=>x!=null&&x!==false));}
  replaceChildren(...xs){this.children=[];this.textContent='';this.append(...xs);}
  addEventListener(k,v){this.events[k]=v;}
  focus(){this.focused=true;}
  scrollIntoView(){}
  fire(k,extra={}){let e={target:this,preventDefault(){this.prevented=true;},...extra};this.events[k]?.(e);return e;}
}
const h=(tag,attrs={},...children)=>{const e=new Element(tag);for(const[k,v]of Object.entries(attrs)){if(v==null||v===false)continue;if(k.startsWith('on'))e.addEventListener(k.slice(2),v);else e.setAttribute(k,v);}e.append(...children);return e;};
function all(root,p){return [root,...(root.children||[]).flatMap(x=>typeof x==='object'?all(x,()=>true):[])].filter(p);}
function text(root){return typeof root==='string'?root:(root.textContent||'')+(root.children||[]).map(text).join(' ');}
const tick=()=>new Promise(r=>setImmediate(r));
let calls=0, colors='dark';
const api={securities:async()=>{calls++;if(scenario==='picker_failure'||(scenario==='picker_retry'&&calls===1))throw Error('unavailable');return {items:[{ticker:'005930',name:'검증전자'},{ticker:'0000A1',name:'검증산업'},{ticker:'000001',name:'<img>'}]};},
 runs:async()=>{if(scenario==='home_partial')throw Error('failed');return{runs:scenario==='home_empty'?[]:[{run_id:'done',strategies:['<script>'],status:'completed',created_at:'2026-10-19T12:00:00'},{run_id:'queued',status:'queued',created_at:'2026-10-20T12:00:00+09:00'},{run_id:'failed',status:'failed'}]};},
 searches:async()=>({searches:scenario==='home_empty'?[]:[{search_id:'search',name:'검색',status:'completed',created_at:'2026-10-19T04:00:00Z'}]}),
 meta:async()=>({status:'ready',markets:['KOSPI'],data_as_of:'2026-09-18'})};
const charts=[];
const context={h,api,t:(k,v={})=>k+JSON.stringify(v),has:()=>true,state:{meta:null},loading:()=>h('p',{},'loading'),
 linkButton:(label,href)=>h('a',{href},label),pill:(label)=>h('span',{},label),
 table:({rows,columns})=>h('table',{},...rows.map(r=>h('tr',{},...columns.map(c=>h('td',{},c.render(r)))))),
 Intl,Date,URLSearchParams,console,
 document:{documentElement:{}},getComputedStyle:()=>({getPropertyValue:k=>colors+k}),
 ResizeObserver:class{observe(){}disconnect(){}},
 window:{echarts:{init:()=>{let option={};const c={disposed:false,isDisposed(){return this.disposed;},dispose(){this.disposed=true;},resize(){},getOption(){return option;},setOption(o){option={...option,...o};}};charts.push(c);return c;}}}};
vm.createContext(context);
function load(path){vm.runInContext(fs.readFileSync(path,'utf8').replace(/^import .*;\r?$/gm,'').replace(/export /g,''),context);}
(async()=>{
 if(scenario.startsWith('picker')){
  load('web/js/components/security-picker.js');let value='';
  const p=context.securityPicker({id:'test',value:'999999',onChange:v=>value=v});await tick();
  const status=all(p.root,e=>e.id==='test-status')[0];
  if(scenario==='picker_retry'){assert.match(status.textContent,/security.failed/);all(p.root,e=>e.tag==='button'&&e.attrs.class==='link-btn'&&e.hidden===false).find(e=>text(e).includes('common.retry')).fire('click');await tick();assert.equal(calls,2);}
  p.input.value='검증';p.input.fire('input');
  if(scenario==='picker_failure'){
   assert.match(status.textContent,/security.failed/);p.input.value='005930,0000a1';assert.equal(p.commit(),true);assert.equal(value,'999999,005930,0000A1');return;
  }
  assert.equal(p.input.attrs['aria-expanded'],'true');assert.equal(all(p.root,e=>e.attrs.role==='option').length,2);
  p.input.fire('keydown',{key:'ArrowUp'});assert.match(p.input.attrs['aria-activedescendant'],/-1$/);
  p.input.fire('keydown',{key:'Escape'});assert.equal(p.input.attrs['aria-expanded'],'false');
  p.input.fire('input');p.input.fire('keydown',{key:'ArrowDown'});
  p.input.fire('compositionstart');p.input.fire('keydown',{key:'Enter',isComposing:true});assert.equal(value,'');
  p.input.fire('compositionend');p.input.fire('keydown',{key:'ArrowDown'});const enter=p.input.fire('keydown',{key:'Enter'});assert.equal(enter.prevented,true);assert.match(value,/999999,/);
  p.input.value='005930,005930,0000a1';assert.equal(p.commit(),true);assert.deepEqual(value.split(',').sort(),['0000A1','005930','999999']);
  p.input.value='존재하지 않는 회사';assert.equal(p.commit(),false);assert.equal(p.input.attrs['aria-invalid'],'true');
  p.input.value='<';p.input.fire('input');assert.equal(all(p.root,e=>e.tag==='img').length,0);assert.match(text(p.root),/<img>/);
  const remove=all(p.root,e=>e.tag==='button'&&e.attrs['aria-label']?.includes('999999'))[0];remove.fire('click');assert.deepEqual(value.split(',').sort(),['0000A1','005930']);
  all(p.root,e=>e.tag==='button'&&text(e).includes('security.clear'))[0].fire('click');assert.equal(value,'');assert.equal(p.input.value,'');assert.equal(calls,scenario==='picker_retry'?2:1);
 }else if(scenario==='chart'){
  load('web/js/charts.js');const root=new Element('chart');
  const chart=context.lines(root,[{name:'series',data:[[1,2]]}],{ariaLabel:'test'});
  assert.equal(chart.getOption().series[0].lineStyle.color,'dark--accent');
  chart.setOption({dataZoom:[{start:25,end:60}],legend:[{selected:{series:false}}]});colors='light';context.refreshTheme();
  assert.equal(chart.getOption().series[0].lineStyle.color,'light--accent');assert.equal(chart.getOption().dataZoom[0].start,25);assert.equal(chart.getOption().legend[0].selected.series,false);assert.equal(charts.length,1);
  context.disposeAll();assert.equal(chart.disposed,true);
 }else{
  load('web/js/views/home.js');const el=new Element('root');const dispose=context.renderHome(el);
  if(scenario==='home_dispose'){dispose();await tick();assert.equal(all(el,e=>e.tag==='table').length,0);return;}
  await tick();
  if(scenario==='home_empty'){assert.match(text(el),/home.emptyTitle/);assert.equal(all(el,e=>e.tag==='table').length,0);return;}
  if(scenario==='home_partial'){assert.match(text(el),/home.loadFailed/);assert.doesNotMatch(text(el),/home.emptyTitle/);assert.match(text(el),/검색/);return;}
  const trs=all(el,e=>e.tag==='tr');assert.equal(trs.length,4);assert.match(text(trs[0]),/queued/);assert.match(text(trs[1]),/검색/);
  assert.equal(all(el,e=>e.attrs.href==='#/runs/queued/progress').length,1);assert.equal(all(el,e=>e.attrs.href==='#/runs/failed/results').length,0);assert.match(text(el),/<script>/);assert.equal(all(el,e=>e.tag==='script').length,0);
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run([node, '-e', script, scenario], cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
