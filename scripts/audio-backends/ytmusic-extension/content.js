let last='',lastReportAt=0,closeArmed=false;
const autoplay=new URL(location.href).searchParams.get('mcautoplay')==='1';
let autoplayComplete=!autoplay;
addEventListener('beforeunload',event=>{if(!closeArmed)return;closeArmed=false;event.stopImmediatePropagation();delete event.returnValue},{capture:true});
browser.runtime.onMessage.addListener(message=>{if(message?.type==='close-without-prompt'){closeArmed=true;return Promise.resolve({armed:true})}});
function report(){const id=new URL(location.href).searchParams.get('v');if(!id)return;const title=document.title.replace(/\s*-\s*YouTube Music\s*$/,'');if(id===last&&Date.now()-lastReportAt<2000)return;browser.runtime.sendMessage({type:'current-track',videoId:id,title,url:location.href}).then(r=>{if(r?.ok){last=id;lastReportAt=Date.now()}}).catch(()=>{})}
function startPlaybackOnce(){if(autoplayComplete)return;const p=document.querySelector('#movie_player');if(p&&typeof p.playVideo==='function'){p.playVideo();autoplayComplete=true;return}const b=document.querySelector('ytmusic-player-bar tp-yt-paper-icon-button.play-pause-button,ytmusic-player-bar button[aria-label*="play" i]');if(b&&b.getAttribute('aria-label')?.toLowerCase().includes('play')){b.click();autoplayComplete=true}}
setInterval(report,500);const timer=setInterval(()=>{startPlaybackOnce();if(autoplayComplete)clearInterval(timer)},250);report();startPlaybackOnce();
