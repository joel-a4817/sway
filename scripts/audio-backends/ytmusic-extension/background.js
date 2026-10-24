const API='http://127.0.0.1:8766';
let lastCookie='',lastSent=0;
const refreshTabs=new Set();
const quickPickTabs=new Map();
let closingMusicTabs=false;

async function closeMusicTab(tabId){
  const result=await localPost('/api/ytmusic/force-close',{tabId});
  if(!result||result.ok!==true)throw new Error('Backend rejected YouTube Music force-close request');
}

async function keepOnlyMusicTab(tabId){
  if(closingMusicTabs)return;
  closingMusicTabs=true;
  try{
    const tabs=await browser.tabs.query({url:'https://music.youtube.com/*'});
    const obsolete=tabs.filter(tab=>tab.id!==tabId).map(tab=>tab.id);
    for(const obsoleteTabId of obsolete){
      try{await closeMusicTab(obsoleteTabId)}
      catch(error){console.error('Could not close obsolete YouTube Music tab',obsoleteTabId,error)}
    }
  }finally{closingMusicTabs=false}
}

function rawHeaders(headers){
  const rows=[];
  for(const h of headers||[]){
    const name=String(h.name||'').trim(),value=typeof h.value==='string'?h.value:'';
    if(name&&value&&!name.startsWith(':')&&!/^content-length$/i.test(name))rows.push(name+': '+value);
  }
  return rows.join('\n');
}
function localPost(path,data){return fetch(API+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(r=>r.json())}

browser.webRequest.onBeforeRequest.addListener(details=>{
  if(details.type!=='main_frame')return;
  const url=new URL(details.url),token=url.searchParams.get('mcquickpick');
  if(url.searchParams.get('media_control_refresh_cookie')==='1')refreshTabs.add(details.tabId);
  if(token)quickPickTabs.set(details.tabId,token);
  if(url.searchParams.get('mcserverlaunch')==='1')keepOnlyMusicTab(details.tabId).catch(()=>{});
},{urls:['https://music.youtube.com/*']});

browser.tabs.onRemoved.addListener(tabId=>{quickPickTabs.delete(tabId);refreshTabs.delete(tabId)});
browser.runtime.onMessage.addListener((message,sender)=>{
  if(!message||message.type!=='current-track'||!sender.tab)return;
  const token=quickPickTabs.get(sender.tab.id)||'';
  return localPost('/api/ytmusic/current',{videoId:message.videoId,title:message.title,url:message.url,quickPickToken:token});
});

browser.webRequest.onBeforeSendHeaders.addListener(details=>{
  if(details.method!=='POST'||!details.url.includes('/youtubei/v1/'))return;
  if(!refreshTabs.has(details.tabId))return;
  const raw=rawHeaders(details.requestHeaders);
  const cookie=(details.requestHeaders||[]).find(h=>/^cookie$/i.test(h.name||''))?.value||'';
  if(!cookie||cookie===lastCookie&&Date.now()-lastSent<10000)return;
  fetch(API+'/api/ytmusic/refresh-cookie-status',{cache:'no-store'}).then(r=>r.json()).then(state=>{
    if(!state.ok||!state.pending)return;
    return localPost('/api/ytmusic/refresh-cookie',{headers:raw}).then(async result=>{
      if(!result.ok)return;
      lastCookie=cookie;lastSent=Date.now();
      const tabId=details.tabId;
      refreshTabs.delete(tabId);
      try{await closeMusicTab(tabId)}catch(error){console.error('Could not close completed cookie-refresh tab',error)}
    });
  }).catch(()=>{});
},{urls:['https://music.youtube.com/youtubei/v1/*']},['requestHeaders']);
