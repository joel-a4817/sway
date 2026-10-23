let last='';
const autoplay=new URL(location.href).searchParams.get('mcautoplay')==='1';
function report(){const id=new URL(location.href).searchParams.get('v');if(!id)return;const title=document.title.replace(/\s*-\s*YouTube Music\s*$/,'');const key=id;if(key===last)return;browser.runtime.sendMessage({type:'current-track',videoId:id,title,url:location.href}).then(result=>{if(result&&result.ok)last=key}).catch(()=>{})}
function startPlayback(){if(!autoplay)return;const player=document.querySelector('#movie_player');if(player&&typeof player.playVideo==='function'){player.playVideo();return}const button=document.querySelector('ytmusic-player-bar tp-yt-paper-icon-button.play-pause-button,ytmusic-player-bar button[aria-label*="play" i]');if(button&&button.getAttribute('aria-label')?.toLowerCase().includes('play'))button.click()}
setInterval(()=>{report();startPlayback()},500);report();startPlayback();
