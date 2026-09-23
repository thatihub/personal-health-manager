const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(process.argv[2] || path.join(__dirname, '../index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
const statsScript = script.slice(script.indexOf('    async function loadStats()'), script.indexOf('    loadLatestDoctorNote();'));
class Element {
  constructor() { this.children = []; this.textContent = ''; this.classList = {add(){}}; }
  replaceChildren() { this.children = []; }
  append(child) { this.children.push(child); }
}
async function render({weight = 149, scans, weightFailure = false, scanFailure = false, labFailure = false}) {
  const elements = Object.fromEntries(['stats','vault-status','updated-status'].map(id => [id,new Element()]));
  const requests = [];
  const context = vm.createContext({Date, Number, String, Array, Promise, document:{getElementById:id=>elements[id],createElement:()=>new Element()}, fetch:async (url, options) => {
    requests.push([url,options]);
    if(url.includes('last-updated')) return {ok:true,json:async()=>({})};
    if(url === '/api/weight-data') return {ok:!weightFailure,json:async()=>({health_context:{weight_lb:weight,date:'2026-09-21',source:'Daily weight',latest_scans:scans}})};
    if(url === '/api/dexa-data') return {ok:!scanFailure,json:async()=>({rows:scans})};
    if(labFailure) throw Error('Unavailable');
    return {text:async()=> 'window.LAB_DASH_DATA = [{"hba1c":6.7,"tsh":2.98}];'};
  }});
  vm.runInContext(statsScript,context);
  await context.loadStats();
  const cards = Object.fromEntries(elements.stats.children.map(card=>[card.children[0].textContent,card.children.slice(1).map(c=>c.textContent)]));
  assert.equal(requests.find(([url])=>url==='/api/weight-data')[1].cache,'no-store');
  return cards;
}
(async()=>{
 const scans=[{date:'2026-08-29',scan_type:'InBody',home_weight_lb:null,body_fat_pct:36.1,lean_body_mass_lb:103.8},{date:'2026-06-02',scan_type:'DEXA',home_weight_lb:161,body_fat_pct:31.6,lean_body_mass_lb:110.1}];
 let cards=await render({scans});
 assert.equal(cards['Current Weight'][0],'149 lb');
 assert.equal(cards['Lean Body Mass'][0],'103.8 lb');
 assert.equal(cards['Lean Body Mass'][1],'InBody · 2026-08-29');
 assert.equal(cards['Body Fat %'][0],'36.1%');
 assert.equal(cards['Latest HbA1c'][0],6.7);
 cards=await render({scans,weight:148}); assert.equal(cards['Current Weight'][0],'148 lb');
 cards=await render({scans,weightFailure:true}); assert.equal(cards['Current Weight'][0],'-');
 cards=await render({scans,weight:null}); assert.equal(cards['Current Weight'][0],'-');
 cards=await render({scans,scanFailure:true,labFailure:true});
 assert.equal(cards['Lean Body Mass'][0],'103.8 lb'); assert.equal(cards['Current Weight'][0],'149 lb');
 cards=await render({scans:[{date:'2099-01-01',lean_body_mass_lb:99,body_fat_pct:1},...scans,{date:'2026-09-01',lean_body_mass_lb:null,body_fat_pct:null}]});
 assert.equal(cards['Lean Body Mass'][0],'103.8 lb');
 cards=await render({scans:[]}); assert.equal(cards['Lean Body Mass'][0],'-');
 console.log('PASS: saved/edited weight, null weight, dated lean mass, scan sorting, future/missing scans, independent API failures, lab values, uncached fetch');
})().catch(error=>{console.error(error);process.exitCode=1;});
