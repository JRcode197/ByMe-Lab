"use strict";
const token = document.querySelector('meta[name="byme-token"]').content;
const $ = id => document.getElementById(id);
let current = {status:'idle', counts:{}, groups:[], sources:[]};
let lastId = 0, history = [], busy=false, toastTimer, firstLoad=true, nextArchiveRefresh=0, polling=false;
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
function renderSettings(settings){$('gatewayIp').value=settings.ip;$('gatewayPort').value=settings.port;$('targetLabel').textContent=`${settings.ip}:${settings.port}`;settings.zones.forEach((n,i)=>{$(`zone${i+1}`).value=n});updateZoneOptions(settings.zones);}
function hhmm(iso){if(!iso)return '';try{return new Date(iso).toLocaleTimeString('it-IT',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})+'.'+iso.substring(20,23);}catch(e){return iso.substring(11,23)}}
function renderEvent(e){const t=esc(hhmm(e.time)); if(e.type==='note')return `<div class="logrow note"><span class="time">${t}</span><span class="addr">✎ ${esc(e.zone)} · ${esc(e.action)} <small>da ${esc(e.origin)}${e.note?' — '+esc(e.note):''}</small></span></div>`;
  if(e.type==='system')return `<div class="logrow system"><span class="time">${t}</span><span class="addr">◈ ${esc(e.message)}</span></div>`;
  if(e.type==='bus') {const raw=esc(e.raw);let val = e.value ?? '';let op=e.apci ?? 'By-me / non decodificato'; if(e.repeat)op+=' · ritrasm.';return `<div class="logrow ${e.warning?'warn':''}" title="${raw}"><span class="time">${t}</span><span class="addr">${esc(e.src||'—')} → <b>${esc(e.dst||'—')}</b></span><span class="oper">${esc(op)} <span class="value">${esc(val)}</span></span><span class="hex">${raw}</span></div>`;}
  return `<div class="logrow system"><span class="time">${t}</span><span class="addr">${e.type==='ack'?'✓ ACK ':''}${esc(e.raw||'')}</span></div>`;
}
function repaint(){if($('freezeView').checked)return;
 const search=$('filterText').value.trim().toLowerCase();const hideRepeat=$('filterRepeat').checked,hideAck=$('filterAck').checked;
 let selected=history.filter(e=> {if(hideRepeat&&e.type==='bus'&&e.repeat)return false;if(hideAck&&(e.type==='ack'||e.type==='info'))return false;
 return !search||[e.src,e.dst,e.raw,e.apci,e.value,e.action,e.zone,e.origin,e.note,e.message].filter(Boolean).join(' ').toLowerCase().includes(search);});
 const shown=selected.slice(-350);$('rowsVisible').textContent=`${shown.length} recenti / ${selected.length} righe in memoria`;
 const node=$('rows');const nearBottom=node.scrollTop+node.clientHeight>=node.scrollHeight-90;
 node.innerHTML=shown.length?shown.map(renderEvent).join(''):`<div class="empty"><div class="empty-icon">⌁</div><strong>Nessuna riga con questi filtri</strong><p>Avvia l'acquisizione oppure modifica i filtri.</p></div>`;
 if(nearBottom)node.scrollTop=node.scrollHeight;
}
function renderStats(s){const c=s.counts||{};for(const [id,key] of [['metricFrames','frames'],['metricRepeats','repeats'],['metricNotes','notes'],['metricIgnored','ignored'],['metricBad','bad'],['metricAck','acks']])$(id).textContent=(c[key]??0).toLocaleString('it-IT');
 const items=s.groups||[];const max=Math.max(1,...items.map(x=>x[1]));$('groupStats').innerHTML=items.length?items.slice(0,7).map(([addr,count])=>`<button class="statrow" data-dst="${esc(addr)}" title="Mostra gruppo ${esc(addr)}"><code>${esc(addr)}</code><span class="statbar"><i style="width:${Math.max(4,Math.round(count/max*100))}%"></i></span><small>${count}</small></button>`).join(''):'<div class="muted">I gruppi appariranno dopo la prima acquisizione.</div>';
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
 $('saveSettings').disabled=!['idle','error'].includes(status);
}
async function update(){if(polling)return;polling=true;try {const result=await fetch('/api/state?since='+lastId,{cache:'no-store'});if(!result.ok)throw new Error('Errore HTTP '+result.status);const s=await result.json();
 if(firstLoad){renderSettings(s.settings);firstLoad=false;}
 if(s.oldest_id>lastId+1&&lastId!==0){history=[];lastId=s.oldest_id-1;notify('Troppi messaggi per la vista. Il file completo è integro.',true);}
 for(const ev of s.events){if(ev.id>lastId){history.push(ev);lastId=ev.id;}}
 if(history.length>2300)history=history.slice(-2300);
 current=s;setState(s);renderStats(s);repaint();
 if(Date.now()>nextArchiveRefresh){nextArchiveRefresh=Date.now()+6000;refreshArchives();}
 }catch(e){$('stateMessage').textContent='Interfaccia disconnessa: '+e.message;}finally{polling=false;}}
async function refreshArchives(){
 try{const r=await fetch('/api/sessions',{headers:{'X-Byme-Token':token}});if(!r.ok)return;
 const data=await r.json(), sel=$('archiveSel'), old=sel.value;
 sel.innerHTML='<option value="">Sessione corrente</option>'+(data.sessions||[]).map(s=>`<option value="${esc(s.id)}">${esc(s.name)} · ${esc(s.start.slice(0,16).replace('T',' '))}</option>`).join('');
 if([...sel.options].some(o=>o.value===old))sel.value=old;
 $('exportBtn').disabled=!current.session&&!sel.value;
 }catch(e){}
}
$('settingsToggle').onclick=()=>{$('settingsPanel').classList.toggle('hidden')};
$('saveSettings').onclick=()=>action('/api/settings',{ip:$('gatewayIp').value,port:Number($('gatewayPort').value),zones:[1,2,3,4].map(i=>$(`zone${i}`).value)},'Impostazioni salvate').then(ok=>{if(ok){renderSettings(current.settings);$('settingsPanel').classList.add('hidden');}});
$('startBtn').onclick=async()=>{history=[];lastId=0;await action('/api/start',{name:$('sessionName').value||'Test termostati'},'Nuova sessione avviata');};
$('pauseBtn').onclick=()=>action(current.status==='paused'?'/api/resume':'/api/pause',{},current.status==='paused'?'Acquisizione ripresa':'Acquisizione in pausa: pacchetti non salvati');
$('stopBtn').onclick=()=>action('/api/stop',{},'Arresto richiesto; verifica il ripristino TCP');
async function mark(actionName){const data={action:actionName,zone:$('noteZone').value,origin:$('noteOrigin').value,note:$('noteText').value};await action('/api/note',data,'Evento marcato con data e ora');$('noteText').value='';}
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