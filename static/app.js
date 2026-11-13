"use strict";
const token = document.querySelector('meta[name="byme-token"]').content;
const $ = id => document.getElementById(id);
let current = {status:'idle', counts:{}, groups:[], sources:[]};
let lastId = 0, history = [], busy=false, toastTimer, firstLoad=true, nextArchiveRefresh=0, polling=false;
let inventory={sources:{},groups:{},eeprom:{}}, selectedInventory=null, inventoryRefreshAt=0, settingsKey='';
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function notify(msg, isError=false) { const el=$('toast'); el.textContent=msg; el.classList.toggle('error',isError); el.classList.add('show'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.classList.remove('show'),4800); }
async function api(path,data={}) {
  const res=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Byme-Token':token},body:JSON.stringify(data)});
  const val=await res.json(); if(!res.ok) throw new Error(val.error || 'Errore inatteso'); return val;
}
async function action(path,data={}, msg='Operazione eseguita') {
  if(busy) return;busy=true;
  try{await api(path,data);notify(msg);await update();return true;}
  catch(e){notify(e.message,true);return false;}finally{busy=false;}
}
function updateZoneOptions(names){const sel=$('noteZone');const was=sel.value;sel.innerHTML=names.map((n,i)=>`<option value="${esc(n)}">${i+1} · ${esc(n)}</option>`).join('');if(names.includes(was))sel.value=was;}
function labelFor(kind,address){const found=inventory[kind]?.[address];return found?.name || address || '—';}
function friendly(kind,address){const name=inventory[kind]?.[address]?.name;return name ? `${name} · ${address}`:address||'—';}
function renderSettings(settings){$('gatewayIp').value=settings.ip;$('gatewayPort').value=settings.port;$('targetLabel').textContent=`${settings.ip}:${settings.port}`;renderZoneCards(settings.climate_zones);updateZoneOptions(settings.zones);}
function hhmm(iso){if(!iso)return '';try{return new Date(iso).toLocaleTimeString('it-IT',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})+'.'+iso.substring(20,23);}catch(e){return iso.substring(11,23)}}
function renderEvent(e){const t=esc(hhmm(e.time)); if(e.type==='note')return `<div class="logrow note"><span class="time">${t}</span><span class="addr">✎ ${esc(e.zone)} · ${esc(e.action)} <small>da ${esc(e.origin)}${e.target_address?' · '+esc(friendly(e.target_kind, e.target_address)):''}${e.note?' — '+esc(e.note):''}</small></span></div>`;
  if(e.type==='system')return `<div class="logrow system"><span class="time">${t}</span><span class="addr">◈ ${esc(e.message)}</span></div>`;
  if(e.type==='bus') {const raw=esc(e.raw);let val = e.value ?? '';let op=e.apci ?? 'By-me / non decodificato'; if(e.repeat)op+=' · ritrasm.';return `<div class="logrow ${e.warning?'warn':''}" title="${raw}"><span class="time">${t}</span><span class="addr">${esc(friendly('sources',e.src))} → <b>${esc(friendly('groups',e.dst))}</b></span><span class="oper">${esc(op)} <span class="value">${esc(val)}</span></span><span class="hex">${raw}</span></div>`;}
  return `<div class="logrow system"><span class="time">${t}</span><span class="addr">${e.type==='ack'?'✓ ACK ':''}${esc(e.raw||'')}</span></div>`;
}
function repaint(){if($('freezeView').checked)return;
 const search=$('filterText').value.trim().toLowerCase();const hideRepeat=$('filterRepeat').checked,hideAck=$('filterAck').checked;
 let selected=history.filter(e=> {if(hideRepeat&&e.type==='bus'&&e.repeat)return false;if(hideAck&&(e.type==='ack'||e.type==='info'))return false;
 return !search||[e.src,e.dst,inventory.sources?.[e.src]?.name,inventory.groups?.[e.dst]?.name,e.raw,e.apci,e.value,e.action,e.zone,e.origin,e.note,e.message,e.target_label].filter(Boolean).join(' ').toLowerCase().includes(search);});
 const shown=selected.slice(-350);$('rowsVisible').textContent=`${shown.length} recenti / ${selected.length} righe in memoria`;
 const node=$('rows');const nearBottom=node.scrollTop+node.clientHeight>=node.scrollHeight-90;
 node.innerHTML=shown.length?shown.map(renderEvent).join(''):`<div class="empty"><div class="empty-icon">⌁</div><strong>Nessuna riga con questi filtri</strong><p>Avvia l'acquisizione oppure modifica i filtri.</p></div>`;
 if(nearBottom)node.scrollTop=node.scrollHeight;
}
function renderStats(s){const c=s.counts||{};for(const [id,key] of [['metricFrames','frames'],['metricRepeats','repeats'],['metricNotes','notes'],['metricIgnored','ignored'],['metricBad','bad'],['metricAck','acks']])$(id).textContent=(c[key]??0).toLocaleString('it-IT');
 const items=s.groups||[];const max=Math.max(1,...items.map(x=>x[1]));$('groupStats').innerHTML=items.length?items.slice(0,7).map(([addr,count])=>`<button class="statrow" data-dst="${esc(addr)}" title="Mostra gruppo ${esc(addr)}"><code title="${esc(addr)}">${esc(friendly('groups',addr))}</code><span class="statbar"><i style="width:${Math.max(4,Math.round(count/max*100))}%"></i></span><small>${count}</small></button>`).join(''):'<div class="muted">I gruppi appariranno dopo la prima acquisizione.</div>';
 $('groupStats').querySelectorAll('button').forEach(btn=>btn.addEventListener('click',()=>{$('filterText').value=btn.dataset.dst;repaint();}));
 $('metricTcpBytes').textContent=(c.monitor_bytes??0).toLocaleString('it-IT');
 $('metricTcpChunks').textContent=(c.tcp_chunks??0).toLocaleString('it-IT');
 $('metricRawLines').textContent=(c.lines??0).toLocaleString('it-IT');
 $('lastReceived').textContent=s.last_rx?hhmm(s.last_rx):'—';
 $('diagHint').textContent=(s.status==='running'&&c.monitor_bytes>0&&c.frames===0)?'TCP riceve dati, ma nessun telegramma KNX riconosciuto: disattiva i filtri e controlla le righe grezze.':(s.status==='running'&&c.monitor_bytes===0?'In attesa di dati dal gateway. Verifica indirizzo, porta ed esclusività della connessione.':'I byte TCP vengono conservati anche quando la decodifica fallisce.');
 $('restoreStatus').textContent=s.gateway_restored===true?'#ok ricevuto':s.gateway_restored===false?'Non confermato':'—';
}
function setState(s){const status=s.status; $('statusDot').className='status-dot '+status;
 const labels={idle:'Monitor fermo',connecting:'Connessione…',running:'Acquisizione in corso',paused:'Acquisizione in pausa',stopping:'Arresto in corso',error:'Errore / sessione chiusa'};
 $('stateTitle').textContent=labels[status]||status;$('stateMessage').textContent=s.message||'';
 $('streamState').textContent=status==='running'?'LIVE':status==='paused'?'PAUSA':status==='connecting'?'CONNESSIONE':'FERMO';
 $('startBtn').disabled=!['idle','error'].includes(status);$('sessionName').disabled=!['idle','error'].includes(status);
 $('pauseBtn').disabled=!['running','paused'].includes(status);$('pauseBtn').textContent=status==='paused'?'▶ Riprendi':'Ⅱ Pausa';
 $('stopBtn').disabled=!['running','paused','connecting'].includes(status);
 $('addNoteBtn').disabled=!['running','paused'].includes(status);
 document.querySelectorAll('.action-buttons button').forEach(b=>b.disabled=!['running','paused'].includes(status));
 $('exportBtn').disabled=!s.session && !$('archiveSel').value;
 $('sessionPath').textContent=s.session||'Nessuna sessione avviata';
 $('saveConnection').disabled=!['idle','error'].includes(status);
 $('saveZones').disabled=!['idle','error'].includes(status);
}
async function update(){if(polling)return;polling=true;try {const result=await fetch('/api/state?since='+lastId,{cache:'no-store'});if(!result.ok)throw new Error('Errore HTTP '+result.status);const s=await result.json();
 const incomingSettings=JSON.stringify(s.settings);
 if(firstLoad || settingsKey!==incomingSettings){renderSettings(s.settings);settingsKey=incomingSettings;firstLoad=false;}
 if(s.oldest_id>lastId+1&&lastId!==0){history=[];lastId=s.oldest_id-1;notify('Troppi messaggi per la vista. Il file completo è integro.',true);}
 for(const ev of s.events){if(ev.id>lastId){history.push(ev);lastId=ev.id;}}
 if(history.length>2300)history=history.slice(-2300);
 current=s;setState(s);renderStats(s);repaint();
 if(Date.now()>nextArchiveRefresh){nextArchiveRefresh=Date.now()+6000;refreshArchives();}
 if(Date.now()>inventoryRefreshAt){inventoryRefreshAt=Date.now()+3500;refreshInventory();}
 }catch(e){$('stateMessage').textContent='Interfaccia disconnessa: '+e.message;}finally{polling=false;}}
async function refreshArchives(){
 try{const r=await fetch('/api/sessions',{headers:{'X-Byme-Token':token}});if(!r.ok)return;
 const data=await r.json(), sel=$('archiveSel'), old=sel.value;
 sel.innerHTML='<option value="">Sessione corrente</option>'+(data.sessions||[]).map(s=>`<option value="${esc(s.id)}">${esc(s.name)} · ${esc(s.start.slice(0,16).replace('T',' '))}</option>`).join('');
 if([...sel.options].some(o=>o.value===old))sel.value=old;
 $('exportBtn').disabled=!current.session&&!sel.value;
 }catch(e){}
}
document.querySelectorAll('.config-trigger').forEach(button=>button.onclick=()=>{
  const target=$(button.dataset.panel), open=!target.classList.contains('hidden');
  for(const id of ['connectionPanel','zonesPanel','inventoryPanel'])$(id).classList.add('hidden');
  if(!open){target.classList.remove('hidden');if(target.id==='inventoryPanel')refreshInventory();target.scrollIntoView({behavior:'smooth',block:'nearest'});}
});
$('saveConnection').onclick=()=>action('/api/connection',{ip:$('gatewayIp').value,port:Number($('gatewayPort').value)},'Connessione salvata');
$('saveZones').onclick=()=>action('/api/zones',{climate_zones:readZoneCards()},'Zone e associazioni salvate');
$('startBtn').onclick=async()=>{history=[];lastId=0;await action('/api/start',{name:$('sessionName').value||'Test termostati'},'Nuova sessione avviata');};
$('pauseBtn').onclick=()=>action(current.status==='paused'?'/api/resume':'/api/pause',{},current.status==='paused'?'Acquisizione ripresa':'Acquisizione in pausa: pacchetti non salvati');
$('stopBtn').onclick=()=>action('/api/stop',{},'Arresto richiesto; verifica il ripristino TCP');
async function mark(actionName){const target=$('noteTarget').value,parts=target?target.split('|'):[];const data={action:actionName,zone:$('noteZone').value,origin:$('noteOrigin').value,note:$('noteText').value,target_kind:parts[0]||'',target_address:parts[1]||''};const ok=await action('/api/note',data,'Evento marcato con data e ora');if(ok)$('noteText').value='';}
document.querySelectorAll('.action-buttons button').forEach(b=>b.onclick=()=>mark(b.dataset.action));
$('addNoteBtn').onclick=()=>mark('NOTA LIBERA');
$('archiveSel').addEventListener('change',()=>{$('exportBtn').disabled=!current.session&&!$('archiveSel').value;});
$('exportBtn').onclick=async()=>{
  notify('Preparazione del file ZIP…');
  try {
    const archive=$('archiveSel').value;
    const response=await fetch('/api/export'+(archive?'?session='+encodeURIComponent(archive):''),{headers:{'X-Byme-Token':token}});
    if(!response.ok){const err=await response.json();throw new Error(err.error||'Errore export');}
    const blob=await response.blob();
    const cd=response.headers.get('Content-Disposition')||'';
    const filename=(cd.match(/filename="([^"]+)"/)||[])[1]||'ByMe_Lab_sessione.zip';
    const url=URL.createObjectURL(blob);const a=document.createElement('a');
    a.href=url;a.download=filename;document.body.appendChild(a);a.click();a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),30000);
    notify('ZIP pronto: allegalo nella conversazione per l’analisi');
  }catch(e){notify(e.message,true);}
};
for(const id of ['filterText','filterRepeat','filterAck','freezeView'])$(id).addEventListener('input',repaint);
update();setInterval(update,900);

// === Inventario osservato: nessuna scansione attiva del bus, nessuna scrittura ai dispositivi ===
function setTargetOptions(){
 const sel=$('noteTarget'), selected=sel.value;
 const rows=[];
 for(const kind of ['sources','groups','eeprom'])for(const [addr,rec] of Object.entries(inventory[kind]||{})){
  const typeLabel={sources:'Sorgente',groups:'Gruppo',eeprom:'EEPROM'}[kind];
  rows.push([`${kind}|${addr}`,`${typeLabel}: ${rec.name||addr}${rec.name?' · '+addr:''}`]);
 }
 rows.sort((a,b)=>a[1].localeCompare(b[1], 'it'));
 sel.innerHTML='<option value="">Non specificato</option>'+rows.map(([v,l])=>`<option value="${esc(v)}">${esc(l)}</option>`).join('');
 if(rows.some(x=>x[0]===selected))sel.value=selected;
}
async function refreshInventory(){
 try{const r=await fetch('/api/inventory',{headers:{'X-Byme-Token':token},cache:'no-store'});if(!r.ok)throw new Error(`HTTP ${r.status}`);
 inventory=await r.json();
 const n=Object.values(inventory.sources||{}).length;
 const g=Object.values(inventory.groups||{}).length;
 const e=Object.values(inventory.eeprom||{}).length;
 $('inventorySummary').textContent=`${n} sorgenti · ${g} gruppi · ${e} ID EEPROM`;
 $('navInventoryCount').textContent=`(${n+g+e})`;
 renderInventoryList();setTargetOptions();renderStats(current);repaint();
 }catch(err){notify(`Censimento: ${err.message}`,true);}
}
function renderInventoryList(){
 const kind=$('inventoryKind').value, text=$('inventorySearch').value.trim().toLowerCase(), unlabeled=$('inventoryUnlabeled').checked;
 const recs=Object.entries(inventory[kind]||{}).filter(([addr,rec])=>{
  if(unlabeled&&rec.name)return false;
  return !text||[addr,rec.name,rec.zone,rec.category,rec.notes].join(' ').toLowerCase().includes(text);
 }).sort((a,b)=>b[1].count-a[1].count||a[0].localeCompare(b[0], 'it'));
 $('inventoryList').innerHTML=recs.length?recs.map(([addr,rec])=>{
  const selected=selectedInventory?.kind===kind&&selectedInventory?.address===addr;
  const links=Object.entries(rec.links||{}).sort((a,b)=>b[1]-a[1]).slice(0,3).map(([linked])=>friendly(kind==='sources'?'groups':'sources',linked)).join(', ');
  return `<button class="inventory-item ${selected?'selected':''}" data-id="${esc(addr)}"><span class="inventory-main"><b>${esc(rec.name||addr)}</b><small>${rec.name?esc(addr)+' · ':''}${esc(rec.category||'sconosciuto')}${rec.zone?' · '+esc(rec.zone):''}</small>${links?`<small class="inventory-links">↳ ${esc(links)}</small>`:''}</span><span class="inventory-number">${rec.count||'—'}</span></button>`;
 }).join(''):'<div class="inventory-placeholder">Nessun indirizzo corrispondente. Puoi avviare una cattura, leggere /status o importare una sessione ZIP.</div>';
 $('inventoryList').querySelectorAll('button[data-id]').forEach(btn=>btn.onclick=()=>{
  selectedInventory={kind,address:btn.dataset.id};renderInventoryList();renderInventoryEditor();
 });
 if(selectedInventory?.kind!==kind){selectedInventory=null;$('inventoryEditor').innerHTML='<div class="inventory-placeholder">Seleziona una riga per assegnare un nome, una categoria e una zona.</div>';}
}
function renderInventoryEditor(){
 if(!selectedInventory)return;
 const {kind,address}=selectedInventory, rec=inventory[kind]?.[address];
 if(!rec){$('inventoryEditor').innerHTML='<div class="inventory-placeholder">Indirizzo non più disponibile.</div>';return;}
 const linked=Object.entries(rec.links||{}).sort((a,b)=>b[1]-a[1]).slice(0,15);
 const what={sources:'Sorgente individuale',groups:'Gruppo logico',eeprom:'ID EEPROM'}[kind];
 $('inventoryEditor').innerHTML=`<div class="section-top"><span class="kicker">SCHEDA ANAGRAFICA</span><span class="tiny">${esc(what)}</span></div>
  <h3>${esc(address)}</h3>
  <label class="field-label">Nome di comodo<input id="editName" maxlength="80" placeholder="Es. Luce cucina, tapparella studio, termostato camera" value="${esc(rec.name||'')}"></label>
  <div class="two"><label>Categoria<select id="editCategory">${['sconosciuto','clima','luci','tapparelle','attuatori','centrale','sensori','gateway','altro'].map(x=>`<option value="${esc(x)}" ${x===rec.category?'selected':''}>${esc(x)}</option>`).join('')}</select></label>
  <label>Zona associata<select id="editZone"><option value="">Nessuna / da verificare</option>${[...new Set([...(current.settings?.zones||[]),...(rec.zone?[rec.zone]:[])])].map(z=>`<option value="${esc(z)}" ${z===rec.zone?'selected':''}>${esc(z)}</option>`).join('')}</select></label></div>
  <label class="field-label">Note / ipotesi<textarea id="editNotes" rows="3" maxlength="500" placeholder="Es. Candidato identificato azionando la luce della cucina">${esc(rec.notes||'')}</textarea></label>
  <label class="check verify-check"><input type="checkbox" id="editVerified" ${rec.verified?'checked':''}> Identificazione verificata con prove ripetute</label>
  <button id="saveInventoryItem" class="btn btn-primary block">Salva identificazione</button>
  <div class="inventory-facts"><p><b>Osservazioni:</b> ${rec.count||0}; ritrasmissioni: ${rec.repetitions||0}</p><p><b>Prima:</b> ${esc(rec.first_seen||'—')}</p><p><b>Ultima:</b> ${esc(rec.last_seen||'—')}</p>
  <p><b>Collegamenti osservati:</b> ${linked.length?linked.map(([addr,n])=>`<span>${esc(friendly(kind==='sources'?'groups':'sources',addr))} (${n})</span>`).join(' '):'nessuno'}</p></div>
  <p class="helptext">L'associazione è una tua etichetta. Un gruppo può essere usato da più dispositivi; il programma non invia comandi per identificarli.</p>`;
 $('saveInventoryItem').onclick=async()=>{
  const payload={kind,address,name:$('editName').value,category:$('editCategory').value,zone:$('editZone').value,notes:$('editNotes').value,verified:$('editVerified').checked};
  const ok=await action('/api/inventory/update',payload,'Scheda salvata localmente');
  if(ok){await refreshInventory();renderInventoryEditor();}
 };
}
for(const id of ['inventoryKind','inventorySearch','inventoryUnlabeled'])$(id).addEventListener(id==='inventoryKind'?'change':'input',renderInventoryList);
async function readStatus(){
 const ok=await action('/api/gateway-status',{},'Elenco EEPROM importato dallo /status del gateway');
 if(ok){$('inventoryKind').value='eeprom';await refreshInventory();}
}
$('readGatewayStatus').onclick=readStatus;
$('readStatusFromConn').onclick=readStatus;
$('importCaptureFile').onchange=async function(){
 const file=this.files?.[0];this.value='';if(!file)return;
 if(file.size>12_000_000){notify('Il file supera 12 MB',true);return;}
 if(!confirm(`Importare soltanto gli indirizzi osservati nel file «${file.name}»? Le annotazioni originali non verranno alterate.`))return;
 try{
  const r=await fetch('/api/import-capture',{method:'POST',headers:{'X-Byme-Token':token,'Content-Type':'application/zip'},body:file});
  const result=await r.json();if(!r.ok)throw new Error(result.error||`Errore HTTP ${r.status}`);
  notify(`Importati ${result.frames} telegrammi; nuove sorgenti: ${result.new_sources}, gruppi: ${result.new_groups}`);
  await refreshInventory();
 }catch(err){notify(err.message,true);}
};
$('exportInventory').onclick=async()=>{
 try{
  const r=await fetch('/api/inventory/export',{headers:{'X-Byme-Token':token}});if(!r.ok)throw new Error(`HTTP ${r.status}`);
  const blob=await r.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download='ByMe_Lab_dispositivi.json';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
 }catch(err){notify(err.message,true);}
};
// Zone climatiche: identità stabile, nessuna corrispondenza implicita con OUT.
function verification(value){return `<label class="check"><input data-field="verified" type="checkbox" ${value?'checked':''}> Associazione verificata con prove</label>`;}
function refFields(ref, busOnly=false){return `<div class="two"><label>Tipo indirizzo<select data-field="kind">${(busOnly?['sources','groups']:['sources','eeprom']).map(k=>`<option value="${k}" ${ref?.kind===k?'selected':''}>${{sources:'Sorgente KNX',groups:'Gruppo KNX',eeprom:'ID EEPROM'}[k]}</option>`).join('')}</select></label><label>Indirizzo (facoltativo)<input data-field="address" maxlength="20" value="${esc(ref?.address||'')}" placeholder="Es. 1.1.42 / 0C89"></label></div>`;}
function deviceFields(key, device={}){return `<fieldset data-device="${key}"><legend>${key==='thermostat'?'Termostato Vimar 14514':'Attuatore eventuale'}</legend><label>Nome / riferimento fisico<input data-field="name" maxlength="80" value="${esc(device.name||'')}"></label>${key==='actuator'?`<label>Modello<input data-field="model" maxlength="80" value="${esc(device.model||'')}"></label>`:''}${refFields(device.ref)}${verification(device.verified)}<label>Prove / ipotesi<textarea data-field="notes" maxlength="500">${esc(device.notes||'')}</textarea></label></fieldset>`;}
function observedRow(item={}){return `<div class="observed-row">${refFields(item,true)}${verification(item.verified)}<label>Prove / ipotesi<input data-field="notes" maxlength="500" value="${esc(item.notes||'')}"></label><button type="button" class="btn btn-minimal remove-observed">Rimuovi indirizzo</button></div>`;}
function renderZoneCards(zones=[]){
 $('zoneCards').innerHTML=zones.map(z=>`<article class="zone-card" data-zone-id="${esc(z.id)}"><label>Nome zona<input data-zone-name maxlength="60" value="${esc(z.name)}"></label><div class="two">${deviceFields('thermostat',z.thermostat)}${deviceFields('actuator',z.actuator)}</div><h3>Indirizzi del bus associati alla zona</h3><p class="helptext">L'associazione alla zona è un'ipotesi finché non verificata. Consulta le occorrenze nel catalogo per distinguere gli indirizzi già osservati da quelli inseriti manualmente.</p><div class="observed-list">${(z.observed||[]).map(observedRow).join('')}</div><div class="button-group"><button class="btn btn-outline add-observed">＋ Associa indirizzo bus</button><button class="btn btn-minimal remove-zone">Rimuovi zona</button></div></article>`).join('');
}
function fields(node){const out={};node.querySelectorAll('[data-field]').forEach(el=>out[el.dataset.field]=el.type==='checkbox'?el.checked:el.value.trim());return out;}
function readZoneCards(){return [...$('zoneCards').querySelectorAll('.zone-card')].map(card=>{
 const z={id:card.dataset.zoneId,name:card.querySelector('[data-zone-name]').value.trim()};
 for(const key of ['thermostat','actuator']){const d=fields(card.querySelector(`[data-device="${key}"]`));z[key]={name:d.name,model:key==='thermostat'?'14514':d.model,ref:d.address?{kind:d.kind,address:d.address}:null,verified:d.verified,notes:d.notes};}
 z.observed=[...card.querySelectorAll('.observed-row')].map(fields);return z;
});}
$('addZone').onclick=()=>{const zones=readZoneCards();if(zones.length>=100)return notify('Massimo 100 zone',true);let name=`Zona ${zones.length+1}`;while(zones.some(z=>z.name===name))name+=' nuova';zones.push({id:'zona-'+crypto.randomUUID(),name});renderZoneCards(zones);};
$('zoneCards').onclick=e=>{const card=e.target.closest('.zone-card');if(!card)return;
 if(e.target.closest('.add-observed'))card.querySelector('.observed-list').insertAdjacentHTML('beforeend',observedRow());
 if(e.target.closest('.remove-observed'))e.target.closest('.observed-row').remove();
 if(e.target.closest('.remove-zone')){if($('zoneCards').children.length===1)return notify('Mantieni almeno una zona',true);if(confirm('Rimuovere questa zona e le sue associazioni al prossimo salvataggio? Le note storiche restano invariate.'))card.remove();}
};

let importConfig=null, importPreview=null, importGeneration=0;
function invalidatePreview(){importGeneration++;importPreview=null;$('applyConfig').disabled=true;$('configChanges').replaceChildren();$('configSummary').textContent='Genera una nuova anteprima prima di confermare.';}
$('importConfigFile').onchange=async function(){const file=this.files?.[0];this.value='';if(!file)return;invalidatePreview();importConfig=null;
 try{if(file.size>3_900_000)throw new Error('JSON troppo grande (massimo 3,9 MB)');importConfig=JSON.parse(await file.text());$('configFileName').textContent=file.name;$('configImportPanel').classList.remove('hidden');await previewConfiguration();}catch(e){notify(e.message,true);}
};
$('configImportMode').onchange=invalidatePreview;
async function previewConfiguration(){if(!importConfig)return;invalidatePreview();const generation=importGeneration;const mode=$('configImportMode').value;
 try{const result=await api('/api/inventory/import-preview',{config:importConfig,mode});if(generation!==importGeneration)return;importPreview=result.preview_id;
 $('configSummary').textContent=`${result.summary.aggiunto} aggiunti · ${result.summary.modificato} modificati · ${result.summary.rimosso} rimossi. ${result.zones_preserved?'Il file non contiene zone: saranno conservate.':''}`;
 $('configChanges').innerHTML=result.changes.map(c=>`<details><summary>${esc(c.action)} · ${esc(c.kind)} ${esc(c.address)}</summary><div class="two"><div><b>Prima</b><pre>${esc(JSON.stringify(c.before,null,2))}</pre></div><div><b>Dopo</b><pre>${esc(JSON.stringify(c.after,null,2))}</pre></div></div></details>`).join('');
 $('applyConfig').disabled=false;
 }catch(e){notify(e.message,true);}
}
$('previewConfig').onclick=previewConfiguration;
$('cancelConfig').onclick=()=>{invalidatePreview();importConfig=null;$('configImportPanel').classList.add('hidden');};
$('applyConfig').onclick=async()=>{if(!importPreview)return;$('applyConfig').disabled=true;
 try{const result=await api('/api/inventory/import-apply',{preview_id:importPreview});invalidatePreview();importConfig=null;$('configImportPanel').classList.add('hidden');selectedInventory=null;$('inventoryEditor').replaceChildren();await refreshInventory();await update();notify('Configurazione importata. Backup: '+result.backup);}catch(e){invalidatePreview();notify(e.message,true);}
};
