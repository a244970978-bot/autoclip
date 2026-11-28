const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = readFileSync(path.join(__dirname, '../../backend/assets/local_captions/app.js'), 'utf8').replace('start().catch(fail);', '');
function ui() {
  const elements = new Map();
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; this.hidden = false; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
  }
  const context = vm.createContext({URLSearchParams, localStorage:{getItem:()=>null}, window:{location:{pathname:'/',search:''}}, document:{createElement:t=>new Element(t), getElementById(id){if (!elements.has(id)) elements.set(id,new Element(id));return elements.get(id);}}});
  vm.runInContext(source, context);
  return {run:s=>vm.runInContext(s,context),elements};
}
const record = {
  primary_pick: {chapter_id:'B',basis:'whole_transcript_hook_v1',camera:'source_frame',compared:2,eligible_hooks:1},
  comparison:'rubric_only', analysis_version:'editorial_v2', recommended:'B', director_ready:['B'],
  reading:{coverage:{complete:true}}, previewable:['B','A'], chapters:[
    {id:'A',title:'Other synthetic topic',start:5,end:11,mode:'source_order',scenes:[],selection:{reason:'Only one example.'}},
    {id:'B',title:'Synthetic first choice',start:16,end:25,mode:'hook_first',scenes:[],selection:{reason:'Claim backed by two examples.'},second_pass:{passed:true}}
  ]
};
function put(view,report) {
  view.run('current={id:"project", jobs:[]}; const report='+JSON.stringify(report)+'; oneClickResult={choices:reportChoices(report),jobId:"job",report};drawCandidateChoices()');
}
test('one card link per clip; primary is explicit; links open the full preview',()=>{
  const v=ui(); put(v,record); const list=v.elements.get('candidateList').children;
  assert.equal(list[0].className,'primary-choice');
  assert.equal(list[0].children.length,1);
  assert.equal(list[0].children[0].href,'/projects/project/clips/job/B?play=1');
  assert.notEqual(list[1].className,'primary-choice');
});
test('no guessing a winner from position, empty or older reports',()=>{
  for(const primary_pick of [null,undefined,{...record.primary_pick,chapter_id:'MISSING'}]){
    const v=ui(); put(v,{...record,primary_pick});
    assert.ok(v.elements.get('candidateList').children.every(el=>el.className!=='primary-choice'));
  }
});
test('autoplay only follows the initiating job or its exact ASR handoff, once',()=>{
  const v=ui(); v.run('current={id:"project",jobs:[{id:"asr",one_click_next:"job"}]};');
  const pick=JSON.stringify(record.primary_pick);
  v.run('requestedPick={project:"project",job:"asr"};');
  assert.equal(v.run('nextPickHref("foreign",'+pick+')'),null);
  assert.equal(v.run('nextPickHref("job",null)'),null);
  assert.equal(v.run('nextPickHref("job",'+pick+')'),'/projects/project/clips/job/B?play=1');
  assert.equal(v.run('nextPickHref("job",'+pick+')'),null);
});
test('primary details give the concrete rationale; compare another candidate without inventing a difference',()=>{
  const v=ui(); put(v,record);
  v.run(`const cs=reportChoices(report);
    current={id:'project',name:'synthetic',jobs:[],cues:[]};
    detailChoice={choice:cs[0],rank:0,legacy:false,primary:true,other:cs[1]};
    cs[0].selection.evidence={claim:{start:4,end:5,quote:'An actual synthetic quote.'}};
    activePreview={draft:{scenes:[]},track:{source:[],duration:9},relisten:[]};
    drawDetail();`);
  const summary=v.elements.get('primaryBrief');
  assert.equal(summary.hidden,false);
  assert.equal(summary.children[1].textContent,'Claim backed by two examples.');
  assert.ok(summary.children.some(n=>n.textContent==='final_comparison_topic_order'));
  assert.ok(summary.children.some(n=>n.children.some(child=>child.textContent==='An actual synthetic quote.')));
  assert.ok(summary.children.at(-1).children.at(-1).textContent.includes('Other synthetic topic'));
  v.run("detailChoice.pick={final_comparison:'model'}; drawDetail()");
  assert.ok(summary.children.some(n=>n.textContent==='final_comparison_edits'));
  assert.ok(!summary.children.some(n=>n.textContent==='final_comparison_topic_order'));
  v.run('detailChoice.primary=false; drawDetail()');
  assert.equal(summary.hidden,true);
  assert.equal(summary.children.length,0);
});

test('hidden preview pauses and visibility restoration never resumes it',()=>{
  const v=ui();
  v.run("activePreview={};let paused=0; $('video').pause=()=>paused++; document.hidden=true; pauseHiddenPreview();");
  assert.equal(v.run('paused'),1);
  v.run('document.hidden=false; pauseHiddenPreview();'); assert.equal(v.run('paused'),1);
  v.run('pauseHiddenPreview(true)'); assert.equal(v.run('paused'),2);
});
