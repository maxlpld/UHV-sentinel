'use strict';
const $ = id => document.getElementById(id);
const state={csrf:null,systems:[],current:null,deviceTypes:{},history:[],lastHistory:0};
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
const removeChildren=n=>{while(n.firstChild)n.removeChild(n.firstChild)};
async function api(path,method='GET',payload=null){
  const opts={method,credentials:'same-origin',headers:{}};
  if(payload!==null){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(payload)}
  if(method!=='GET')opts.headers['X-UHV-CSRF']=state.csrf||'';
  const response=await fetch('/api/'+path,opts);
  let data;try{data=await response.json()}catch{throw Error('Server returned an invalid response')}
  if(!response.ok){if(response.status===401&&path!=='login')showLogin();throw Error(data.error||('HTTP '+response.status))}
  return data;
}
function showLogin(){state.csrf=null;$('application').hidden=true;$('login').hidden=false;$('password').focus()}
function showApp(){ $('login').hidden=true;$('application').hidden=false; }
async function init(){try{const session=await api('session');if(!session.authenticated)return showLogin();state.csrf=session.csrf;showApp();state.deviceTypes=await api('types');await refreshSystems();const match=location.pathname.match(/^\/s\/([a-z0-9-]+)$/);if(match)await openSystem(match[1],false)}catch(err){showLogin();$('login-error').textContent=String(err.message)}}
$('login-form').addEventListener('submit',async event=>{
 event.preventDefault();$('login-error').textContent='';try{
  const data=await api('login','POST',{password:$('password').value});state.csrf=data.csrf;$('password').value='';showApp();state.deviceTypes=await api('types');await refreshSystems();
  const match=location.pathname.match(/^\/s\/([a-z0-9-]+)$/);if(match)await openSystem(match[1],false);
 }catch(err){$('login-error').textContent=err.message}
});
$('logout').addEventListener('click',async()=>{try{await api('logout','POST',{})}catch{}showLogin()});
async function refreshSystems(){state.systems=await api('systems');drawSystems()}
function drawSystems(){
 const nav=$('system-nav');removeChildren(nav);const overview=$('overview-systems');removeChildren(overview);
 for(const s of state.systems){
  const n=el('button','system-item'+(state.current===s.slug?' active':''),s.name);n.addEventListener('click',()=>openSystem(s.slug));nav.append(n);
  const a=el('a','system-link',s.name+' · '+s.devices+' instrument(s)');a.href='/s/'+s.slug;a.addEventListener('click',e=>{e.preventDefault();openSystem(s.slug)});overview.append(a)
 }
}
async function openSystem(slug,push=true){
 if(!state.systems.some(s=>s.slug===slug))return;
 state.current=slug;state.lastHistory=0;state.history=[];
 if(push)history.pushState({},'', '/s/'+slug);
 $('overview').hidden=true;$('system-view').hidden=false;drawSystems();await refreshSystem(true)
}
window.addEventListener('popstate',()=>{const m=location.pathname.match(/^\/s\/([a-z0-9-]+)$/);if(m)openSystem(m[1],false);else{state.current=null;$('system-view').hidden=true;$('overview').hidden=false;drawSystems()}});
async function refreshSystem(loadHistory=false){
 if(!state.current)return;
 try{
  const slug=state.current,info=await api('systems/'+slug);if(slug!==state.current)return;
  const names=state.systems.find(s=>s.slug===slug);
  $('system-title').textContent=info.name;
  $('system-url').textContent=location.origin+'/s/'+slug;
  $('columns').value=String(info.layout?.columns||2);
  const grid=$('device-grid');grid.style.gridTemplateColumns='repeat('+($('columns').value)+', minmax(0,1fr))';
  const ordered=sortDevices(info.devices,info.layout?.order||[]);
  drawDeviceCards(ordered,info.live||{});
  $('empty-devices').hidden=ordered.length>0;
  $('updated-time').textContent='Updated '+new Date().toLocaleTimeString();
  if(loadHistory||Date.now()-state.lastHistory>15000){
   state.history=await api('systems/'+slug+'/history?hours='+$('history-range').value+'&limit=3500');
   state.lastHistory=Date.now();
  }
  for(const dev of ordered)drawChart('chart-'+dev.id,state.history.filter(r=>r.device_id===dev.id));
 }catch(e){$('updated-time').textContent='Connection error: '+e.message}
}
function sortDevices(devices,order){const ranks=new Map(order.map((x,i)=>[x,i]));return [...devices].sort((a,b)=>(ranks.get(a.id)??1e4)-(ranks.get(b.id)??1e4))}
function drawDeviceCards(devices,live){
 const grid=$('device-grid');removeChildren(grid);
 for(let i=0;i<devices.length;i++){
  const dev=devices[i],data=live[dev.id]||{},card=el('article','device-card');
  const top=el('div','card-top'),left=el('div'),title=el('h2','',dev.name),type=el('p','type',state.deviceTypes[dev.type]||dev.type);
  left.append(title,type);const status=el('span','state '+(data.state||'pending'),data.state||'pending');top.append(left,status);card.append(top);
  const metrics=el('div','metrics');const active=data.metrics||{};
  for(const [key,m] of Object.entries(active)){
   const item=el('div','metric'),small=el('small','',key),large=el('b','',formatValue(m.value,m.unit)+' '+m.unit);
   item.append(small,large);metrics.append(item)
  }
  if(!Object.keys(active).length)metrics.append(el('span','muted','No fresh measurements'));
  card.append(metrics);
  if(data.error)card.append(el('p','error-message',data.error));
  const graph=el('canvas','chart');graph.id='chart-'+dev.id;graph.height=195;card.append(graph);
  const actions=el('div','card-actions'),when=el('span','muted-small',data.last_success?'Last good: '+new Date(data.last_success*1000).toLocaleTimeString(): 'No successful reading yet');
  const buttons=el('div');const up=el('button','secondary','↑'),down=el('button','secondary','↓'),del=el('button','secondary','Remove');
  up.disabled=i===0;down.disabled=i===devices.length-1;
  up.addEventListener('click',()=>moveDevice(devices,i,-1));down.addEventListener('click',()=>moveDevice(devices,i,+1));
  del.addEventListener('click',async()=>{if(!confirm('Remove '+dev.name+'? Historical recordings will be retained.'))return;
   try{await api('systems/'+state.current+'/devices/'+dev.id+'/delete','POST',{});await refreshSystems();await refreshSystem(true)}catch(e){alert(e.message)}
  });buttons.append(up,down,del);actions.append(when,buttons);card.append(actions);grid.append(card)
 }
}
async function moveDevice(devices,index,direction){const arr=devices.map(x=>x.id);const j=index+direction;[arr[index],arr[j]]=[arr[j],arr[index]];
 try{await api('systems/'+state.current+'/layout','POST',{columns:Number($('columns').value),order:arr});await refreshSystem(false)}catch(e){alert(e.message)}
}
function formatValue(v,unit){if(typeof v!=='number'||!Number.isFinite(v))return '—';return unit==='mbar'?v.toExponential(2):Number(v.toPrecision(5)).toString()}
function drawChart(id,rows){
 const canvas=$(id);if(!canvas)return;
 const rect=canvas.getBoundingClientRect();const w=Math.max(260,rect.width||400),h=195,dpr=window.devicePixelRatio||1;
 canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
 ctx.clearRect(0,0,w,h);ctx.fillStyle='#fafcfc';ctx.fillRect(0,0,w,h);
 const series=new Map();for(const row of rows){if(!Number.isFinite(row.value))continue;
  const key=row.metric+' '+row.unit;if(!series.has(key))series.set(key,[]);series.get(key).push(row)}
 ctx.fillStyle='#8b979e';ctx.font='11px system-ui';
 if(!series.size){ctx.fillText('Historical measurements will appear here',16,h/2);return}
 const colors=['#ce1138','#0e8585','#3e5978','#e39a2f','#946aa0','#267e53'];
 let earliest=Infinity,latest=-Infinity;
 for(const points of series.values())for(const r of points){earliest=Math.min(earliest,r.ts);latest=Math.max(latest,r.ts)}
 if(latest<=earliest)latest=earliest+1;
 ctx.strokeStyle='#e2e8eb';ctx.lineWidth=1;for(let n=0;n<4;n++){const y=25+n*(h-51)/3;ctx.beginPath();ctx.moveTo(46,y);ctx.lineTo(w-8,y);ctx.stroke()}
 const bounds=new Map();
 for(const [label,points] of series){
  const unit=label.split(' ').at(-1),log=unit==='mbar';
  const values=points.map(p=>log?Math.log10(Math.max(1e-20,p.value)):p.value);
  const lo=Math.min(...values),hi=Math.max(...values);
  const prev=bounds.get(unit);bounds.set(unit,{lo:Math.min(prev?.lo??Infinity,lo),hi:Math.max(prev?.hi??-Infinity,hi),log});
 }
 let n=0;
 for(const [label,points] of series){
  const unit=label.split(' ').at(-1),b=bounds.get(unit);
  const lo=b.lo===b.hi?b.lo-0.5:b.lo,hi=b.lo===b.hi?b.hi+0.5:b.hi;
  ctx.strokeStyle=colors[n%colors.length];ctx.lineWidth=1.7;ctx.beginPath();
  for(let k=0;k<points.length;k++){
   const x=46+(points[k].ts-earliest)/(latest-earliest)*(w-57);
   const value=b.log?Math.log10(Math.max(1e-20,points[k].value)):points[k].value;
   const y=25+(hi-value)/(hi-lo)*(h-51);
   if(k===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)
  }ctx.stroke();
  ctx.fillStyle=colors[n%colors.length];ctx.font='11px system-ui';ctx.fillText(label.substring(0,29),8+n*145,14);n++
 }
 ctx.fillStyle='#7f8c92';ctx.fillText(new Date(earliest*1000).toLocaleTimeString(),45,h-7);ctx.fillText(new Date(latest*1000).toLocaleTimeString(),Math.max(46,w-86),h-7)
}
function modal(title,help,fields,onSubmit){
 $('modal-title').textContent=title;$('modal-help').textContent=help;$('modal-error').textContent='';
 const target=$('modal-fields');removeChildren(target);const controls={};
 for(const field of fields){const l=el('label','',field.label);l.htmlFor='field-'+field.key;target.append(l);
  const input=document.createElement(field.options?'select':'input');input.id='field-'+field.key;
  if(field.options){for(const [value,label] of Object.entries(field.options)){const option=el('option','',label);option.value=value;input.append(option)}}
  else{input.type=field.type||'text';if(field.min!==undefined)input.min=field.min;if(field.max!==undefined)input.max=field.max}
  if(field.value!==undefined)input.value=field.value;if(field.placeholder)input.placeholder=field.placeholder;
  input.required=field.required!==false;controls[field.key]=input;target.append(input)
 }
 $('modal-form').onsubmit=async e=>{e.preventDefault();const payload={};for(const [k,v] of Object.entries(controls))payload[k]=v.value;
  $('modal-save').disabled=true;try{await onSubmit(payload);$('modal').close()}catch(e){$('modal-error').textContent=e.message}finally{$('modal-save').disabled=false}};
 $('modal').showModal()
}
$('modal-cancel').addEventListener('click',()=>$('modal').close());
$('create-system').addEventListener('click',()=>modal('Create laboratory system','Give each system a stable URL name. Systems share no device connections or histories.',[
 {key:'name',label:'Display name',value:'UHV System A'},{key:'slug',label:'URL name (e.g. lab-a)',value:'lab-a'}],async data=>{
 const res=await api('systems','POST',data);await refreshSystems();await openSystem(res.slug)
}));
$('add-device').addEventListener('click',()=>{
 if(!state.current)return;
 const fields=[{key:'name',label:'Instrument name',value:'DualGauge 1'},
 {key:'type',label:'Device type',options:state.deviceTypes},
 {key:'port',label:'Mapped serial port (ignored for TCP)',value:'/dev/ttyUSB0',required:false},
 {key:'baudrate',label:'Serial baud rate',type:'number',value:'9600',min:1200,max:115200,required:false},
 {key:'host',label:'TCP IP (only for verified Ethernet / gateway)',placeholder:'192.168.1.55',required:false},
 {key:'tcp_port',label:'TCP port',type:'number',value:'502',required:false},
 {key:'address',label:'Controller address (if applicable)',type:'number',value:'1',required:false},
 {key:'units',label:'EpiMax pressure units',options:{mBar:'mbar',Torr:'Torr',Pa:'Pa'}},
 {key:'interval',label:'Polling interval (seconds)',type:'number',value:'5',min:2,max:120}];
 modal('Add instrument','In Docker, map the USB device in compose.server.yml first. Connections are read-only.',fields,async data=>{
  data.baudrate=Number(data.baudrate||9600);data.tcp_port=Number(data.tcp_port||502);data.address=Number(data.address||1);
  data.interval=Number(data.interval);await api('systems/'+state.current+'/devices','POST',data);
  await refreshSystems();await refreshSystem(true)
 });
});
$('history-range').addEventListener('change',()=>{state.lastHistory=0;refreshSystem(true)});
$('columns').addEventListener('change',async()=>{try{
 const i=await api('systems/'+state.current);
 await api('systems/'+state.current+'/layout','POST',{columns:Number($('columns').value),order:i.layout?.order||[]});await refreshSystem(false)
}catch(e){alert(e.message)}});
$('copy-link').addEventListener('click',async()=>{
 const url=location.origin+'/s/'+state.current;
 try{await navigator.clipboard.writeText(url)}catch{prompt('Copy system URL:',url)}
});
$('export-profile').addEventListener('click',async()=>{
 try{const data=await api('systems/'+state.current+'/profile');const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
 const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='uhv-'+state.current+'-profile.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),5000)}catch(e){alert(e.message)}
});
$('import-profile').addEventListener('click',()=>$('profile-file').click());
$('profile-file').addEventListener('change',async e=>{
 const file=e.target.files[0];if(!file)return;
 try{const data=JSON.parse(await file.text());if(!confirm('Import this profile? Current instrument configuration will be replaced; historical data remains.'))return;
 await api('systems/'+state.current+'/profile','POST',data);await refreshSystems();await refreshSystem(true)}catch(err){alert('Profile import failed: '+err.message)}finally{e.target.value=''}
});
setInterval(()=>{if(state.current&&state.csrf)refreshSystem(false)},5000);
window.addEventListener('resize',()=>{if(state.current)refreshSystem(false)});
init();
