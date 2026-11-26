'use strict';
let lastHub = null;
let lastHomeUpdateAt=0;
let cachedHomeUpdate=null;
const toBytes = v => v===null||v===undefined?'Unavailable':(v/1073741824).toFixed(1)+' GiB';
const duration = v => v===null||v===undefined?'Unavailable':Math.floor(v/86400)+'d '+Math.floor((v%86400)/3600)+'h';
const infoCard = (label,value,detail='') => {
 const card=el('div','hub-stat'),a=el('small','',label),b=el('strong','',value),c=el('span','',detail);
 card.append(a,b,c);return card;
};
async function refreshHub(){
 if(!state.csrf)return;
 try{
  const data=await api('hub'); lastHub=data;const host=data.host||{};
  $('hub-title').textContent=data.title;
  const metrics=$('hub-metrics');removeChildren(metrics);
  metrics.append(infoCard('Server',host.available?host.hostname:'Unavailable',host.available?'Ubuntu host status':'Host statistics agent offline'));
  const disk=host.disk_total_bytes,free=host.disk_free_bytes;
  metrics.append(infoCard('Disk available',host.available?toBytes(free):'Unavailable',
   disk!==null&&disk!==undefined?'of '+toBytes(disk)+' total':'Host disk'));
  const used=host.memory_total_bytes!==null&&host.memory_total_bytes!==undefined&&host.memory_available_bytes!==null&&host.memory_available_bytes!==undefined
   ?host.memory_total_bytes-host.memory_available_bytes:null;
  metrics.append(infoCard('Memory used',host.available?toBytes(used):'Unavailable', 'of '+toBytes(host.memory_total_bytes)));
  metrics.append(infoCard('CPU temperature',host.available&&host.cpu_temp_c!==null&&host.cpu_temp_c!==undefined?host.cpu_temp_c.toFixed(1)+' °C':'Unavailable',host.cpu_temp_source||'No supported host sensor'));
  metrics.append(infoCard('Host uptime',host.available?duration(host.uptime_seconds):'Unavailable',
   host.load_1m===null||host.load_1m===undefined?'':('1-min load: '+host.load_1m)));
  const drawUpdateCard=result=>{
    const old=$('home-update-card');if(old)old.remove();
    const card=infoCard('UHV Sentinel version',result.installed,
      result.available?'Update available: '+result.latest:result.error||'Up to date');
    card.id='home-update-card';card.title='Open Hub settings to manage updates';
    card.style.cursor='pointer';card.addEventListener('click',()=>$('hub-settings').click());
    $('hub-metrics').append(card);
  };
  if(cachedHomeUpdate)drawUpdateCard(cachedHomeUpdate);
  if(Date.now()-lastHomeUpdateAt>600000){
    lastHomeUpdateAt=Date.now();
    api('hub/update').then(result=>{cachedHomeUpdate=result;drawUpdateCard(result)}).catch(()=>{});
  }

  const systems=$('overview-systems');removeChildren(systems);
  for(const system of data.systems){
   const tile=el('a','hub-system-card');tile.href=system.url;
   tile.addEventListener('click',e=>{e.preventDefault();openSystem(system.slug)});
   const top=el('div','hub-card-top'),left=el('div'),title=el('h3','',system.name),slug=el('small','',system.url);
   left.append(title,slug);top.append(left,el('span','hub-chip',system.healthy+'/'+system.devices+' online'));tile.append(top);
   const pressure=el('div','hub-pressures');
   for(const key of ['p','a']){
    const c=system.chambers[key],element=el('div','hub-pressure');
    element.append(el('small','',c.label));
    element.append(el('strong',c.fresh?'':'pressure-unavailable',c.fresh?formatValue(c.value,c.unit)+' '+c.unit:c.configured?'No fresh value':'Not configured'));
    pressure.append(element);
   }
   tile.append(pressure,el('span','hub-enter','Open dashboard →'));systems.append(tile);
  }
  if(!data.systems.length)systems.append(el('div','hub-empty','No systems yet. Use “Add system” to create RT-A, RT-B, or another laboratory system.'));
  const shortcuts=$('hub-shortcuts');removeChildren(shortcuts);
  for(const item of data.shortcuts){
   const row=el('div','shortcut-row'),a=el('a','',item.title+' ↗'),del=el('button','tiny','Remove');
   a.href=item.url;a.rel='noopener noreferrer';if(!item.url.startsWith('/'))a.target='_blank';
   del.addEventListener('click',async()=>{if(!confirm('Remove shortcut '+item.title+'?'))return;try{await api('hub/shortcuts/'+item.id,'POST',{});await refreshHub()}catch(e){alert(e.message)}});
   row.append(a,del);shortcuts.append(row);
  }
  if(!data.shortcuts.length)shortcuts.append(el('div','hub-empty','Add an authorized local link to another laboratory tool or server.'));
 }catch(err){if($('hub-metrics'))$('hub-metrics').textContent='Hub offline: '+err.message}
}
function showHome(push=true){
 state.current=null;$('system-view').hidden=true;$('hub-settings-view').hidden=true;$('overview').hidden=false;
 if(push)history.pushState({},'','/');drawSystems();refreshHub();
}
$('go-home').addEventListener('click',()=>showHome());
$('hub-add-system').addEventListener('click',()=>$('create-system').click());
$('hub-add-shortcut').addEventListener('click',()=>modal('Add link shortcut',
 'Only enter URLs reachable from your authorized laboratory network. Links open locally in your browser.',[
 {key:'title',label:'Button label',value:'Other laboratory system'},
 {key:'url',label:'URL (https://…, http://… or /s/lab-name)',value:'https://192.168.1.10:8443/'}
 ],async x=>{await api('hub/shortcuts','POST',x);await refreshHub()}));
$('hub-settings').addEventListener('click',()=>{
 state.current=null;history.pushState({},'','/settings');$('overview').hidden=true;$('system-view').hidden=true;$('hub-settings-view').hidden=false;
 $('hub-name-input').value=lastHub?.title||'UHV Sentinel Hub';refreshUpdate();refreshMaintenance();
});
$('hub-save-name').addEventListener('click',async()=>{try{
 await api('hub/settings','POST',{title:$('hub-name-input').value});
 $('hub-settings-message').textContent='Hub name saved.';await refreshHub();
}catch(e){$('hub-settings-message').textContent=e.message}});
async function refreshUpdate(){
 const label=$('hub-version'),status=$('hub-update-state');
 label.textContent='Checking GitHub releases…';$('hub-install-update').disabled=true;
 try{
  const result=await api('hub/update');
  label.textContent='Installed '+result.installed+' · GitHub latest '+(result.latest||'unavailable');
  if(result.pending){status.textContent='An update has been requested. The Ubuntu host updater will process it.';return}
  const s=result.status||{};
  status.textContent=[s.state?s.state+': '+(s.message||s.version||''):'',result.error||''].filter(Boolean).join(' · ')||
      (result.available?'A newer version is available.':'No newer version detected.');
  $('hub-install-update').disabled=!result.available;
 }catch(e){label.textContent='Update check unavailable';status.textContent=e.message}
}
$('hub-check-update').addEventListener('click',refreshUpdate);
$('hub-install-update').addEventListener('click',async()=>{
 if(!confirm('Install the latest GitHub release? Monitoring will pause briefly while Docker containers restart. Saved data and profiles are retained.'))return;
 try{await api('hub/update','POST',{});$('hub-update-state').textContent='Update queued. The host will install the verified GitHub release.';$('hub-install-update').disabled=true}
 catch(e){$('hub-update-state').textContent=e.message}
});
$('configure-chambers').addEventListener('click',async()=>{
 if(!state.current)return;
 const slug=state.current;const info=await api('systems/'+slug);const current=await api('systems/'+slug+'/chambers');
 const available=[];
 for(const d of info.devices){
  const reading=info.live[d.id]||{};
  for(const [metric,value] of Object.entries(reading.metrics||{}))if(value.unit==='mbar')
   available.push({key:d.id+'::'+metric,label:d.name+' — '+metric,device_id:d.id,metric});
 }
 const options={'':'Not configured'};
 for(const row of available)options[row.key]=row.label;
 const getCurrent=k=>current[k]?current[k].device_id+'::'+current[k].metric:'';
 for(const k of ['p','a'])if(getCurrent(k) && !options[getCurrent(k)])
  options[getCurrent(k)]='Saved pressure mapping (currently offline)';
 modal('Home hub pressure indicators','Select a currently reporting pressure channel for P-Chamber and A-Chamber. Missing values remain marked unavailable.',[
  {key:'p',label:'P-Chamber',options,value:getCurrent('p'),required:false},
  {key:'a',label:'A-Chamber',options,value:getCurrent('a'),required:false}],async data=>{
   const mapping={};for(const k of ['p','a'])if(data[k]){
    const choice=available.find(x=>x.key===data[k]);
    if(choice)mapping[k]={device_id:choice.device_id,metric:choice.metric};
    else if(data[k]===getCurrent(k))mapping[k]=current[k];
    else throw Error('Pressure channel unavailable');
   }
   await api('systems/'+slug+'/chambers','POST',mapping);await refreshHub();
  });
});
window.addEventListener('popstate',()=>{
 if(location.pathname==='/settings'){
  $('hub-settings').click();history.replaceState({},'','/settings');
 }else if(location.pathname==='/'){showHome(false)}
});
// Restore settings view once login succeeds, even if the user opened its URL directly.
const oldShowApp=showApp;showApp=function(){oldShowApp();if(location.pathname==='/settings')setTimeout(()=>{$('hub-settings').click();history.replaceState({},'','/settings')},0)};


// Ubuntu host maintenance is a separate, root-owned systemd worker.
// The browser never transmits a shell command, package name, or reboot delay.
let maintenanceRefreshing=false;
async function refreshMaintenance(){
 if(maintenanceRefreshing || !state.csrf || $('hub-settings-view').hidden)return;
 maintenanceRefreshing=true;
 try{
  const info=await api('hub/maintenance');
  const p=Math.max(0,Math.min(100,Number(info.progress)||0));
  $('maintenance-progress').value=p;$('maintenance-percent').textContent=p+'%';
  $('maintenance-stage').textContent=info.stage||'Idle';
  const active=!!info.pending||!!info.reboot_pending||['checking','installing','rebooting'].includes(info.state);
  $('maintenance-check').disabled=active;
  $('maintenance-install').disabled=active||!(['ready','up-to-date'].includes(info.state)&&info.can_install);
  $('maintenance-reboot').disabled=active||!(info.state==='completed'&&info.can_reboot);
  const summary=[];
  summary.push('Status: '+(info.state||'idle'));
  if(info.packages_count!==undefined)summary.push('Ubuntu packages: '+info.packages_count);
  if(info.docker_version)summary.push(info.docker_version);
  if(info.reboot_required)summary.push('Ubuntu reports reboot required');
  if(info.message)summary.push(info.message);
  if(info.error)summary.push('Error: '+info.error);
  if(info.pending)summary.push('Waiting for host maintenance service');
  if(info.reboot_pending)summary.push('Restart request queued');
  $('maintenance-summary').textContent=summary.join(' · ');
  const lines=Array.isArray(info.logs)?info.logs:[];
  const box=$('maintenance-logs');
  const scroll=box.scrollHeight-box.clientHeight-box.scrollTop<40;
  const output=lines.length?lines.join('\n'):'No logs yet.';
  if(box.textContent!==output)box.textContent=output;
  if(scroll)box.scrollTop=box.scrollHeight;
  const restarting=info.state==='rebooting';
  $('maintenance-reconnect').textContent=restarting?'Ubuntu Server is restarting. The page will reconnect automatically when the dashboard returns.':
    info.state==='restarted'?'Server reboot completed; dashboard is available again.':'';
 }catch(err){$('maintenance-summary').textContent='Dashboard temporarily unavailable: '+err.message+'. Retrying automatically…';}
 finally{maintenanceRefreshing=false}
}
async function requestHostMaintenance(action,question){
 if(!confirm(question))return;
 const buttons=['maintenance-check','maintenance-install','maintenance-reboot'];
 for(const name of buttons)$(name).disabled=true;
 $('maintenance-summary').textContent='Requesting '+action+'…';
 try{await api('hub/maintenance/'+action,'POST',{});await refreshMaintenance()}
 catch(err){$('maintenance-summary').textContent=err.message;await refreshMaintenance()}
}
$('maintenance-check').addEventListener('click',()=>requestHostMaintenance('check',
 'Refresh Ubuntu package lists and check Docker versions? No software will be installed. This requires outbound access to package repositories.'));
$('maintenance-install').addEventListener('click',()=>requestHostMaintenance('install',
 'Install Ubuntu full-upgrade and Docker updates now? This may restart Docker, interrupt ALL RT-A/RT-B monitoring, and temporarily disconnect this dashboard. Do not proceed during a critical experiment.'));
$('maintenance-reboot').addEventListener('click',()=>requestHostMaintenance('reboot',
 'REBOOT this entire Ubuntu Server now? ALL instruments will stop recording during the restart; the dashboard will disconnect until the server returns.'));
setInterval(refreshMaintenance,2000);
