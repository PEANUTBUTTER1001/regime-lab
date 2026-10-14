"""Exercise the real login view with DOM/API test doubles; no OAuth credentials."""
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.mark.parametrize("scenario", ["kakao", "google", "failure", "unavailable", "cleanup"])
def test_login_back_navigation(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the browser-view unit test")
    root = Path(__file__).resolve().parents[2]
    script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const scenario = process.argv[1];
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.disabled=false; this.classList={add(){},remove(){},toggle(){}}; }
  setAttribute() {}
  append(...items) { this.children.push(...items); }
}
const buttons=[];
function h(tag,attrs,...children) {
  const e=new Element(tag);e.attrs=attrs;e.append(...children);
  if(tag==='button') buttons.push(e);
  return e;
}
const events=new Map();
const state={authConfig:{enabled:true,available:true,social_providers:{kakao:true,google:true},guest_allowed:true}};
if(scenario==='unavailable') {state.authConfig.social_providers.google=false;state.authConfig.guest_allowed=false;}
let calls=[];
let destinations=[];
const context={h,BRAND:'test',t:k=>k,state,URLSearchParams,
 document:{createElementNS:(_,tag)=>new Element(tag)},
 window:{addEventListener:(name,fn)=>events.set(name,fn),removeEventListener:(name,fn)=>{assert.equal(events.get(name),fn);events.delete(name);}},
 api:{startOAuth:async provider=>{calls.push(provider);if(scenario==='failure')throw {code:'auth_oauth_unavailable'};return {url:'https://example.test/'+provider};}},
 location:{assign:url=>destinations.push(url)}};
let source=fs.readFileSync('web/js/views/login.js','utf8').replace(/^import .*;\r?$/gm,'').replace('export function renderLogin','function renderLogin');
vm.createContext(context);vm.runInContext(source,context);
(async()=>{
 const cleanup=context.renderLogin(new Element('root'));
 const selected=scenario==='google'?1:0;
 assert.equal(buttons[selected].disabled,false);
 const click=buttons[selected].onclick();
 assert.ok(buttons.every(b=>b.disabled));
 await click;
 if(scenario==='failure'){assert.ok(buttons.every(b=>!b.disabled));return;}
 assert.ok(buttons.every(b=>b.disabled));
 events.get('pageshow')({persisted:false});
 assert.ok(buttons.every(b=>b.disabled));
 events.get('pageshow')({persisted:true});
 assert.equal(buttons[0].disabled,false);
 assert.equal(buttons[1].disabled,scenario==='unavailable');
 assert.equal(buttons[2].disabled,scenario==='unavailable');
 await buttons[selected].onclick();
 assert.equal(calls.length,2);
 assert.equal(destinations.length,2);
 cleanup();assert.equal(events.size,0);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", script, scenario], cwd=root, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
