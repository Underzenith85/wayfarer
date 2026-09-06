const $ = id => document.getElementById(id);
let boot, draft, scenario, campaign, busy = false, pending = null;
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function notice(text) { $('notice').textContent = text; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Request failed');
  return result;
}
async function job(fn) {
  if (busy) return;
  busy = true; document.querySelectorAll('button').forEach(b => b.disabled = true); notice('Working…');
  try { await fn(); notice(''); } catch(e) { notice(e.message); }
  finally { busy = false; document.querySelectorAll('button').forEach(b => b.disabled = false); configureMic(); }
}
function view(name) {
  document.querySelectorAll('.view').forEach(s => s.hidden = s.id !== name);
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b.dataset.view === name));
}
document.querySelectorAll('[data-view]').forEach(b => b.onclick = () => view(b.dataset.view));
function renderDraft() {
  $('char-name').value = draft.name; $('char-concept').value = draft.concept;
  $('attributes').innerHTML = Object.entries(draft.attributes).map(([k,v]) => `<label>${esc(k)}<input data-attr="${esc(k)}" type="number" min="8" max="14" value="${esc(v)}"></label>`).join('');
  $('skills').innerHTML = ['Stealth','Observation','Diplomacy','Survival'].map(k => `<label class="skill-row">${k}<select data-skill="${k}">${[0,1,2,4,8,12,16].map(n => `<option value="${n}" ${n === (draft.skills[k] || 0)?'selected':''}>${n} points</option>`).join('')}</select></label>`).join('');
  $('traits').innerHTML = Object.entries(boot.rules.traits).map(([k,v]) => `<label><input type="checkbox" data-trait="${esc(k)}" ${draft.traits.includes(k)?'checked':''}> ${esc(k)} <span class="muted">${v>0?'+':''}${v} pts</span></label>`).join('');
}
function collectDraft() {
  const c = {name:$('char-name').value,concept:$('char-concept').value,attributes:{},skills:{},traits:[]};
  document.querySelectorAll('[data-attr]').forEach(i => c.attributes[i.dataset.attr] = Number(i.value));
  document.querySelectorAll('[data-skill]').forEach(i => {if(Number(i.value)) c.skills[i.dataset.skill] = Number(i.value);});
  document.querySelectorAll('[data-trait]:checked').forEach(i => c.traits.push(i.dataset.trait));
  return c;
}
function verdict(v) {
  $('validation').innerHTML = `<strong>${esc(v.remaining)}</strong><p>points remaining / 100</p><p class="${v.valid?'good':'bad'}">${v.valid?'✓ Legal for this campaign':'Draft needs changes'}</p>${v.errors.map(e=>`<p class="bad">${esc(e)}</p>`).join('')}<hr>${Object.entries(v.levels).map(([k,n])=>`<div class="statline"><span>${esc(k)}</span><b>${n}</b></div>`).join('')}`;
}
$('validate').onclick = () => job(async()=> {draft=collectDraft(); verdict(await api('/api/validate',{character:draft}));});
$('generate-character').onclick = () => job(async()=> {
  const r=await api('/api/generate/character',{prompt:$('concept-prompt').value,current:collectDraft()});
  draft=r.draft; renderDraft(); verdict(r.validation); $('generation-note').textContent=r.mode;
});
function renderScenario() {
  $('scenario-fields').innerHTML=Object.entries(scenario).map(([k,v])=>`<label for="scenario-${k}">${esc(k[0].toUpperCase()+k.slice(1))}${k==='secret'?' · author only':''}</label><textarea id="scenario-${k}" data-scenario="${k}" rows="${k==='premise'?3:2}" maxlength="2000">${esc(v)}</textarea>`).join('');
}
function collectScenario() {const s={}; document.querySelectorAll('[data-scenario]').forEach(i=>s[i.dataset.scenario]=i.value);return s;}
$('generate-scenario').onclick=()=>job(async()=>{const r=await api('/api/generate/scenario',{prompt:$('scenario-prompt').value});scenario=r.draft;renderScenario();});
async function campaigns() {
  boot=await api('/api/bootstrap');
  $('campaigns').innerHTML='<option value="">Choose an adventure</option>'+boot.campaigns.map(c=>`<option value="${esc(c.id)}">${esc(c.title)} · ${esc(c.name)}</option>`).join('');
  if(campaign) $('campaigns').value=campaign.id;
}
function renderPlay() {
  const c=campaign.character;
  $('title').textContent=campaign.scenario.title; $('location').textContent=campaign.location.toUpperCase();
  $('clock').textContent=`${campaign.minutes} minutes elapsed · Turn ${campaign.revision} · Saved`;
  $('party').innerHTML=`<h2>${esc(c.name)}</h2><p class="muted">${esc(c.concept)}</p>` + [['HP',campaign.hp,c.attributes.ST],['Fatigue',campaign.fp,c.attributes.HT]].map(([k,v,m])=>`<div class="statline"><span>${k}</span><span>${v} / ${m}</span></div><div class="meter"><span style="width:${100*v/m}%"></span></div>`).join('')+`<div class="attributes">${Object.entries(c.attributes).map(([k,v])=>`<div><small>${k}</small><br><b>${v}</b></div>`).join('')}</div>`;
  $('inventory').innerHTML=campaign.inventory.map(i=>`<div class="item">${esc(i)}</div>`).join('');
  $('progress').innerHTML=`<h3>${esc(campaign.scenario.objective)}</h3><p class="${campaign.complete?'good':'muted'}">${campaign.complete?'✓ Completed':campaign.discoveries.length?'Lead discovered · follow it to the customs house':'Investigate the docks'}</p>`;
  $('journal').innerHTML=[...campaign.discoveries,...campaign.flags].map(d=>`<div class="item">${esc(d)}</div>`).join('') || '<p class="muted">Your discoveries will appear here.</p>';
  $('messages').innerHTML=campaign.messages.map(m=>`<div class="message ${m.role}"><span class="eyebrow">${m.role==='gm'?'GAME MASTER':esc(c.name)}</span>${esc(m.flavor||m.text)}${m.flavor?`<details><summary>Committed outcome</summary>${esc(m.text)}</details>`:''}${m.roll?`<span class="roll">${esc(m.action)} · [${m.roll.dice.join(' + ')}] = ${m.roll.total} / target ${m.roll.target} · ${m.roll.critical?'CRITICAL ':''}${m.roll.success?'SUCCESS':'FAILURE'}</span>`:''}</div>`).join('');
  $('messages').scrollTop=$('messages').scrollHeight;
  localStorage.setItem('wayfarer-campaign',campaign.id);
}
async function start(c,s) {campaign=await api('/api/campaigns',{character:c,scenario:s});pending=null;renderPlay();await campaigns();view('play');}
$('start').onclick=()=>job(()=>start(collectDraft(),collectScenario()));
$('quickstart').onclick=()=>job(()=>start(boot.character,boot.scenario));
$('campaigns').onchange=()=>job(async()=>{if(!$('campaigns').value)return;campaign=await api('/api/campaigns/'+$('campaigns').value);pending=null;renderPlay();});
$('action-form').onsubmit=e=>{e.preventDefault();job(async()=>{
  if(!campaign)throw Error('Begin an adventure first.');
  const text=$('action').value.trim();if(!text)throw Error('Describe an action or ask a question.');
  if(!pending || pending.text!==text || pending.campaign!==campaign.id)pending={request_id:crypto.randomUUID(),revision:campaign.revision,text,campaign:campaign.id};
  campaign=await api('/api/campaigns/'+campaign.id+'/turn',pending);pending=null;renderPlay();$('action').value='';
  if($('speak').checked && 'speechSynthesis' in window){speechSynthesis.cancel();const m=campaign.messages.at(-1);speechSynthesis.speak(new SpeechSynthesisUtterance(m.flavor||m.text));}
});};
document.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>{$('action').value=b.dataset.action;$('action').focus();});
const SpeechRecognition=window.SpeechRecognition||window.webkitSpeechRecognition;
let recognition=null;
function configureMic(){ $('mic').disabled=!SpeechRecognition||busy; if(!SpeechRecognition)$('voice-help').textContent='Speech recognition is unavailable in this browser. Text input is fully supported.'; }
$('mic').onclick=()=>{
  if(recognition){recognition.stop();return;}
  if('speechSynthesis' in window)speechSynthesis.cancel();
  recognition=new SpeechRecognition();recognition.lang='en-US';recognition.interimResults=false;
  recognition.onresult=e=>{$('action').value=e.results[0][0].transcript;notice('Transcript ready. Review it, then send.');};
  recognition.onerror=e=>notice('Microphone: '+e.error+'. You can still type your action.');
  recognition.onend=()=>{recognition=null;$('mic').textContent='◉ Speak';};
  try{recognition.start();$('mic').textContent='■ Stop listening';}catch(e){recognition=null;notice(e.message);}
};
job(async()=>{await campaigns();$('mode').textContent=boot.mode;draft=boot.character;scenario=boot.scenario;renderDraft();renderScenario();verdict(await api('/api/validate',{character:draft}));const id=localStorage.getItem('wayfarer-campaign');if(id && boot.campaigns.some(c=>c.id===id)){campaign=await api('/api/campaigns/'+id);renderPlay();$('campaigns').value=id;}});
