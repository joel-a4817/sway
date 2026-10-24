let last='',lastReportAt=0;
const autoplay=new URL(location.href).searchParams.get('mcautoplay')==='1';
let autoplayComplete=!autoplay;

function report(){
  const id=new URL(location.href).searchParams.get('v');
  if(!id)return;
  const title=document.title.replace(/\s*-\s*YouTube Music\s*$/,'');
  if(id===last&&Date.now()-lastReportAt<2000)return;
  browser.runtime.sendMessage({type:'current-track',videoId:id,title,url:location.href})
    .then(result=>{if(result&&result.ok){last=id;lastReportAt=Date.now()}})
    .catch(()=>{});
}

function startPlaybackOnce(){
  if(autoplayComplete)return;
  const player=document.querySelector('#movie_player');
  if(player&&typeof player.playVideo==='function'){
    player.playVideo();
    autoplayComplete=true;
    return;
  }
  const button=document.querySelector('ytmusic-player-bar tp-yt-paper-icon-button.play-pause-button,ytmusic-player-bar button[aria-label*="play" i]');
  if(button&&button.getAttribute('aria-label')?.toLowerCase().includes('play')){
    button.click();
    autoplayComplete=true;
  }
}

setInterval(report,500);
const autoplayTimer=setInterval(()=>{
  startPlaybackOnce();
  if(autoplayComplete)clearInterval(autoplayTimer);
},250);
report();
startPlaybackOnce();
