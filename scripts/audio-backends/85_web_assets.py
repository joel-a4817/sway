# Embedded web application. Keep markup, styles, and JavaScript readable.
PAGE = r'''<!doctype html>
<html>
 <head>
  <meta charset="utf-8"/>
  <meta content="width=device-width,initial-scale=1,viewport-fit=cover" name="viewport"/>
  <title>
   CamillaDSP Studio
  </title>
  <style>
   :root {
  color-scheme:dark;
  font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display",sans-serif;
  --v:#865dff;
  --b:#3f8eff;
  --g:#35d6a0;
}
* {
  box-sizing:border-box;
}
body {
  margin:0;
  min-height:100svh;
  padding:22px 16px 36px;
  color:#fff;
  background:radial-gradient(900px 520px at 50% -150px,#6542a8,#211a38 44%,#070910);
  background-attachment:fixed;
}
main {
  max-width:540px;
  margin:auto;
}
.hidden {
  display:none!important;
}
.hero,.card {
  border:1px solid #ffffff22;
  background:linear-gradient(145deg,#ffffff1c,#ffffff0d);
  box-shadow:inset 0 1px #ffffff22,0 20px 50px #0005;
  backdrop-filter:blur(22px);
}
.hero {
  padding:25px 22px;
  border-radius:29px;
}
.card {
  padding:18px;
  border-radius:24px;
  margin:14px 0;
}
.card+.grid {
  margin-top:14px;
}
.label,.section-title {
  color:#bcb7cb;
  text-transform:uppercase;
  letter-spacing:.1em;
  font-size:12px;
  font-weight:800;
}
h1 {
  margin:10px 0 5px;
  font-size:32px;
  letter-spacing:-.04em;
}
.subtitle,.details,.meta {
  color:#b9b4c5;
  font-size:13px;
}
.section-title {
  margin:27px 3px 10px;
}
.title {
  font-size:20px;
  font-weight:800;
  margin-top:6px;
  overflow-wrap:anywhere;
}
.grid {
  display:grid;
  gap:10px;
}
.two {
  grid-template-columns:1fr 1fr;
}
.three {
  grid-template-columns:1fr 1.2fr 1fr;
}
.pc-transport {
  margin-bottom:18px;
}
.pc-library-actions {
  margin-top:18px;
}
.wide {
  grid-column:1/-1;
}
button,input {
  font:inherit;
}
button {
  border:0;
  color:#fff;
  cursor:pointer;
}
.action,.item,.transport,.back {
  width:100%;
  transition:transform .12s,filter .15s;
}
.action:active,.item:active,.transport:active,.back:active {
  transform:scale(.96);
  filter:brightness(1.15);
}
.action {
  min-height:54px;
  border-radius:18px;
  background:#ffffff1b;
  font-weight:780;
}
.primary {
  background:linear-gradient(135deg,var(--b),var(--v));
}
.green {
  background:linear-gradient(135deg,#24ae7c,#287d95);
}
.danger {
  background:linear-gradient(135deg,#e64e74,#8e2b4b);
}
.active {
  outline:2px solid #a783ff;
  background:linear-gradient(135deg,#4e82ff66,#8552ff77)!important;
}
.search {
  width:100%;
  min-height:49px;
  border:1px solid #ffffff22;
  border-radius:17px;
  padding:12px 15px;
  color:#fff;
  background:#ffffff12;
  outline:0;
}
.search:focus {
  border-color:#9b7aff;
  box-shadow:0 0 0 4px #825dff2e;
}
.list {
  display:grid;
  gap:10px;
  margin-top:10px;
}
.cover-art {
  width:48px;
  height:48px;
  flex:none;
  object-fit:cover;
  border-radius:11px;
  background:#ffffff18;
}
.cover-art.large {
  width:100%;
  max-width:220px;
  height:auto;
  aspect-ratio:1;
  display:block;
  margin:0 auto 16px;
  border-radius:20px;
}
.cover-fallback {
  display:grid;
  place-items:center;
  color:#c3b6f3;
  font-size:24px;
}
.cover-fallback.large {
  display:grid;
  font-size:68px;
}
.item-cover {
  display:flex;
  align-items:center;
  gap:12px;
  min-width:0;
}
.item-cover .copy {
  min-width:0;
  flex:1;
  overflow-wrap:anywhere;
  font-size:16px;
  line-height:1.3;
}
.item-cover .name {
  font-size:16px;
  line-height:1.3;
  font-weight:780;
}
.item-cover .meta {
  font-size:13px;
  line-height:1.35;
  font-weight:400;
}
.item {
  text-align:left;
  min-height:64px;
  padding:13px 15px;
  border-radius:19px;
  background:#ffffff16;
}
.name {
  display:block;
  font-weight:780;
}
.meta {
  display:block;
  margin-top:4px;
}
.playlist-row {
  position:relative;
}
.playlist-open {
  display:block;
  padding-right:120px;
  min-height:76px;
}
.liked-toolbar {
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  margin-top:12px;
  margin-bottom:2px;
}
.liked-toolbar .action {
  width:auto;
  min-height:40px;
  padding:9px 14px;
  border-radius:14px;
}
.liked-toolbar .details {margin:0 2px 0 auto;white-space:nowrap}
.playlist-select-wrap {
  position:absolute;
  right:12px;
  top:50%;
  transform:translateY(-50%);
  display:grid;
  place-items:center;
  width:44px;
  height:44px;
  z-index:1;
}
.playlist-select {
  width:20px;
  height:20px;
  margin:0;
  accent-color:var(--g);
  cursor:pointer;
}
.playlist-controls {
  position:absolute;
  right:12px;
  top:50%;
  transform:translateY(-50%);
  display:flex;
  gap:8px;
  z-index:1;
}
.playlist-icon {
  display:grid;
  place-items:center;
  width:44px;
  height:44px;
  border:1px solid #ffffff38;
  border-radius:14px;
  background:#242036e8;
  box-shadow:0 4px 14px #0005;
}
.playlist-icon:hover {
  background:#564282;
}
.playlist-icon:active {
  transform:scale(.94);
}
.playlist-icon .media-icon {
  width:20px;
  height:20px;
}
.playlist-icon.busy {
  font-size:20px;
}
.header {
  display:grid;
  grid-template-columns:72px 1fr 72px;
  align-items:center;
}
.header h1 {
  text-align:center;
  font-size:23px;
}
.back {
  min-height:43px;
  border-radius:15px;
  background:#ffffff18;
}
.range {
  --fill:0%;
  width:100%;
  height:36px;
  background:transparent;
  appearance:none;
}
.range::-webkit-slider-runnable-track {
  height:6px;
  border-radius:99px;
  background:linear-gradient(to right,#fff var(--fill),#ffffff2d var(--fill));
}
.range::-webkit-slider-thumb {
  appearance:none;
  width:21px;
  height:21px;
  margin-top:-7.5px;
  border-radius:50%;
  background:#fff;
  box-shadow:0 3px 10px #0008;
}
.times {
  display:flex;
  justify-content:space-between;
  color:#aaa5b5;
  font-size:12px;
}
.range-row {
  display:grid;
  grid-template-columns:24px 1fr 24px;
  align-items:center;
  gap:7px;
}
.transport {
  display:grid;
  place-items:center;
  min-height:78px;
  border-radius:999px;
  background:#ffffff19;
}
.transport.main {
  min-height:98px;
  background:linear-gradient(145deg,#9e68ff,#583ad2);
}
.media-icon {
  display:block;
  width:34px;
  height:34px;
  fill:none;
  stroke:#fff;
  stroke-width:2.15;
  stroke-linecap:round;
  stroke-linejoin:round;
  pointer-events:none;
}
.transport.main .media-icon {
  width:42px;
  height:42px;
}
.icon-fill {
  fill:#fff;
  stroke:#fff;
}
.range {
  touch-action:none;
}
.range.dragging::-webkit-slider-thumb {
  transform:scale(1.08);
}
.source-grid {
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:10px;
  margin-top:12px;
}
/* Final interaction and consistency pass */
button {
  position:relative;
  overflow:hidden;
  -webkit-user-select:none;
  user-select:none;
  touch-action:manipulation;
}
button:focus-visible,.search:focus-visible,.range:focus-visible {
  outline:2px solid #b79cff;
  outline-offset:3px;
}
button:disabled {
  opacity:.58;
  cursor:default;
}
.action,.item,.transport,.back {
  will-change:transform;
}
button.busy::after {
  content:"";
  position:absolute;
  inset:0;
  background:linear-gradient(105deg,transparent 22%,var(--busy-sheen,#ffffff47) 50%,transparent 78%);
  pointer-events:none;
  z-index:3;
  animation:ui-shine .8s linear infinite;
}
@keyframes ui-shine{
from {
  transform:translateX(-100%);
}
to {
  transform:translateX(100%);
}
}
.transport {
  transition:transform .12s ease,filter .15s ease,box-shadow .2s ease;
}
.transport.main.playing {
  box-shadow:0 18px 42px #6b45dd77,inset 0 1px #ffffff35;
}
.media-icon {
  transition:transform .16s ease;
}
.transport:active .media-icon {
  transform:scale(.9);
}
.range {
  cursor:pointer;
}
.range.dragging::-webkit-slider-thumb {
  transform:scale(1.1);
}
.range::-webkit-slider-runnable-track {
  transition:background .08s linear;
}
.mode-active {
  outline:2px solid #72e5bc;
  background:linear-gradient(135deg,#24ae7c,#287d95)!important;
}
#repeat.active,#shuffle.active {
  outline:2px solid #a783ff;
  background:linear-gradient(135deg,#4e82ff66,#8552ff77)!important;
}
.output-modal {
  position:fixed;
  inset:0;
  z-index:50;
  display:grid;
  place-items:end center;
  padding:18px;
  background:#05060bbb;
  backdrop-filter:blur(12px);
}
.output-sheet {
  width:min(100%,540px);
  max-height:82svh;
  overflow:auto;
  padding:20px;
  border:1px solid #ffffff2b;
  border-radius:26px;
  background:linear-gradient(145deg,#272139,#11131c);
  box-shadow:0 28px 80px #000b;
}
.output-options {
  display:grid;
  gap:10px;
  margin:16px 0;
}
.output-choice {
  display:grid;
  grid-template-columns:24px 1fr;
  gap:10px;
  align-items:center;
  padding:14px;
  border-radius:18px;
  background:#ffffff12;
}
.output-options option:disabled {
  color:#999;
  opacity:.5;
}
.output-choice input {
  width:20px;
  height:20px;
  accent-color:#865dff;
}
.output-choice strong,.output-choice span {
  display:block;
}
.output-choice span {
  margin-top:3px;
  color:#aaa5b5;
  font-size:11px;
  overflow-wrap:anywhere;
}
.modal-actions {
  display:grid;
  grid-template-columns:1fr;
  gap:10px;
}
@media (prefers-reduced-motion:reduce){
* {
  animation-duration:.001ms!important;
  transition-duration:.001ms!important;
  scroll-behavior:auto!important;
}
}
/* Page rhythm and accessible navigation. */
#profiles>.hero {
  margin-bottom:20px;
}
#profiles>.action {
  display:block;
  margin:14px 0 20px;
}
#profiles>.grid {
  margin-top:12px;
}
#profiles>.section-title {
  margin-top:26px;
  margin-bottom:12px;
}
#groups-page .header,#listening-page .header,#songs .header,#playlist .header,#pc .header {
  margin-bottom:18px;
}
#groups-page .card {
  display:grid;
  gap:12px;
}
#groups-page .card .search,#groups-page .card .action {
  margin:0;
}
#groups-page .card label {
  display:flex;
  align-items:center;
  gap:9px;
}
.card>.details {
  margin-top:6px;
}
#pc .card>.label {
  margin-bottom:8px;
}
#listening-page .search {
  margin-bottom:16px;
}
#song-search,#playlist-search {
  display:block;
  margin:0 0 16px;
}
#song-list,#playlist-songs {
  margin-top:0;
}
.system-media-heading {
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
}
.card .range {
  display:block;
  margin-top:12px;
}
.card .times {
  margin-top:2px;
}
.card .range-row {
  margin:14px 0 16px;
}
.card .grid {
  margin-top:14px;
}
.range-row {
  grid-template-columns:24px minmax(0,1fr) 24px;
  gap:10px;
}
.volume-icon {
  display:grid;
  place-items:center;
  color:#d6d3de;
  pointer-events:none;
}
.volume-icon svg {
  display:block;
  width:20px;
  height:20px;
}
.local-now-header {
  display:flex;
  align-items:center;
  gap:14px;
  min-width:0;
  margin:10px 0 16px;
}
.local-now-copy {
  flex:1;
  min-width:0;
}
.local-now-copy .details {
  margin-top:6px;
  overflow-wrap:anywhere;
}
.local-now-artwork {
  flex:0 0 72px;
  width:72px;
  height:72px;
  position:relative;
  display:grid;
  place-items:center;
  overflow:hidden;
  border-radius:15px;
  background:linear-gradient(145deg,#433b68,#222d42);
  color:#c8c1e9;
  font-size:32px;
  line-height:1;
}
.local-now-artwork::before {
  content:'♫';
}
.local-now-artwork img {
  position:absolute;
  inset:0;
  display:block;
  width:100%;
  height:100%;
  object-fit:cover;
}
.back-to-top {
  position:fixed;
  z-index:40;
  top:max(14px,env(safe-area-inset-top));
  left:50%;
  transform:translateX(-50%);
  width:auto;
  min-height:44px;
  padding:9px 17px;
  border:1px solid #ffffff38;
  border-radius:999px;
  background:#262039f2;
  box-shadow:0 8px 28px #0009;
  white-space:nowrap;
  font-size:14px;
  font-weight:750;
  backdrop-filter:blur(14px);
}
.back-to-top:active {
  transform:translateX(-50%) scale(.96);
}
/* Playlist actions: clear affordance without changing the page layout. */
.playlist-row {
  border-radius:19px;
  transition:background .16s ease;
}
.playlist-row:hover {
  background:#ffffff08;
}
.playlist-icon {
  transition:background .16s ease,border-color .16s ease,transform .12s ease;
}
.playlist-icon:hover {
  border-color:#aa90ff;
  background:#493572;
}
#shuffle:disabled {
  opacity:.42;
  cursor:not-allowed;
}
#shuffle:not(:disabled) {
  background:linear-gradient(135deg,#6252a7,#373b77);
}
/* Unified indigo, blue and mint palette. */
:root {
  --v:#8574f5;
  --b:#6c9cff;
  --g:#55d9ae;
}
body {
  background:radial-gradient(900px 520px at 50% -150px,#514784,#1d2039 48%,#0b101b);
}
.hero,.card {
  border-color:#a8b7ef25;
  background:linear-gradient(145deg,#b8c8ff18,#a8b8ee0a);
}
.action:not(.primary):not(.green):not(.danger):not(.active) {
  background:#a8b7ef1b;
}
#shuffle:not(:disabled) {
  background:linear-gradient(135deg,#6257ae,#465b99);
}
.playlist-icon {
  background:#242941e8;
  border-color:#b7c7ff38;
}
.playlist-icon:hover {
  background:#414a77;
  border-color:#9caeff;
}
.output-sheet {
  background:linear-gradient(145deg,#252b47,#111827);
}
/* Shared home palette and compact destination layout. */
:root {
  --v:#8574f5;
  --b:#6c9cff;
  --g:#55d9ae;
}
#profiles {
  max-width:480px;
  margin-inline:auto;
}
#profiles>.hero {
  padding:17px 18px;
  border-radius:23px;
  margin-bottom:12px;
}
#profiles>.hero h1 {
  font-size:27px;
  margin:6px 0 3px;
}
#profiles>.card {
  padding:16px;
  margin:10px 0;
  border-radius:21px;
}
#profiles>.section-title {
  margin:18px 3px 8px;
}
#profiles>.action {
  min-height:48px;
  margin:10px 0 12px;
  border-radius:16px;
}
#audio-options-page .source-grid {
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:9px;
  margin-top:8px;
}
#audio-options-page .source-grid .source-heading {
  grid-column:1/-1;
  color:#bcb7cb;
  font-size:11px;
  font-weight:800;
  letter-spacing:.1em;
  text-transform:uppercase;
  margin:9px 2px 0;
}
#audio-options-page .source-grid .action {
  min-height:50px;
  padding:8px 5px;
  border-radius:15px;
  font-size:14px;
  line-height:1.2;
}
#profiles .card .grid {
  margin-top:10px;
}
#profiles .transport {
  min-height:64px;
}
#profiles .transport.main {
  min-height:80px;
}
#profiles .pc-transport {
  margin-bottom:12px;
}
.output-options .hidden {
  display:none!important;
}

/* Aurora Dusk theme: plum night, teal light, coral warmth and amber detail. */
:root {
  --ink:#fff8fb;
  --muted:#d0bfd0;
  --night:#171326;
  --plum:#3e2856;
  --berry:#643453;
  --teal:#236b70;
  --aqua:#4aa3a0;
  --coral:#b95f68;
  --amber:#c3914f;
  --periwinkle:#6678b8;
  --line:#f1c6df29;
}
body {
  color:var(--ink);
  background:
    radial-gradient(680px 520px at 5% -5%,#2b858470,transparent 66%),
    radial-gradient(760px 560px at 100% 0%,#8d416b6b,transparent 68%),
    radial-gradient(620px 500px at 50% 105%,#c18b4850,transparent 72%),
    linear-gradient(155deg,#171326 0%,#29203a 48%,#171a2c 100%);
}
.hero {
  border-color:#79cfca45;
  background:
    linear-gradient(125deg,#22666e9e 0%,#49315fbc 48%,#77405e9e 100%);
  box-shadow:inset 0 1px #ffffff35,0 20px 52px #0c09166b;
}
.hero h1 { color:#fff7fc; text-shadow:0 2px 18px #160d2770; }
.card {
  border-color:var(--line);
  background:linear-gradient(145deg,#493252d9,#29253bd9);
  box-shadow:inset 0 1px #ffffff20,0 15px 38px #0c091647;
}
#profiles > .card {
  background:linear-gradient(145deg,#313f54e8,#30243fe8 58%,#432940e8);
}
#audio-options-page .card:nth-of-type(odd) {
  background:linear-gradient(145deg,#293f4be0,#2d2840e0);
}
#audio-options-page .card:nth-of-type(even) {
  background:linear-gradient(145deg,#493044e0,#322942e0);
}
.section-title {
  color:#efbbcf;
  text-shadow:0 1px 12px #9d49613d;
}
.label { color:#b9ded8; }
.details,.meta,.times { color:var(--muted); }
.action:not(.active),.item,.back,.transport {
  border:1px solid #ffffff20;
  box-shadow:inset 0 1px #ffffff19,0 8px 20px #0b08143d;
}
.action:not(.primary):not(.green):not(.danger):not(.active) {
  background:linear-gradient(145deg,#49354f,#302d45);
}
.action:hover,.item:hover,.back:hover {
  filter:brightness(1.1) saturate(1.08);
}
.accent-mint {
  background:linear-gradient(135deg,#27777a,#315f78)!important;
}
.accent-amber {
  background:linear-gradient(135deg,#b17c43,#8a4e4c)!important;
}
.accent-violet {
  background:linear-gradient(135deg,#76569b,#5d477f)!important;
}
.accent-cyan {
  background:linear-gradient(135deg,#277c83,#3f608e)!important;
}
.accent-orange {
  background:linear-gradient(135deg,#b96559,#8b4565)!important;
}
.accent-blue {
  background:linear-gradient(135deg,#5268a4,#534f8b)!important;
}
.accent-red {
  background:linear-gradient(135deg,#ad5167,#793d62)!important;
}
#profiles > #restart-heading { margin-top:22px; }
#profiles > [aria-labelledby="restart-heading"] {
  margin-top:10px;
  margin-bottom:12px;
  grid-auto-rows:1fr;
  align-items:stretch;
}
#profiles > [aria-labelledby="restart-heading"] > .action {
  min-height:58px;
  height:100%;
  margin:0;
  padding:10px 12px;
}
#audio-options-page .source-grid {
  grid-template-columns:repeat(3,minmax(0,1fr));
}
#audio-options-page .source-grid .source-heading { color:#edbfd2; }
#audio-options-page .source-grid .action:nth-of-type(3n+1) {
  background:linear-gradient(145deg,#257078,#315a77);
}
#audio-options-page .source-grid .action:nth-of-type(3n+2) {
  background:linear-gradient(145deg,#69518e,#4c4776);
}
#audio-options-page .source-grid .action:nth-of-type(3n) {
  background:linear-gradient(145deg,#a16f43,#76515b);
}
.mode-active {
  outline:2px solid #75d7c8;
  outline-offset:1px;
  box-shadow:0 0 0 4px #75d7c82e,0 10px 24px #100b1959;
  filter:brightness(1.12) saturate(1.12);
}
.search,.output-choice {
  border-color:#f3c9df2e;
  background:linear-gradient(145deg,#443349d9,#292c42d9);
}
.search:focus {
  border-color:#70c9c4;
  box-shadow:0 0 0 4px #4aa3a02b;
}
.output-sheet {
  border-color:#e9b9d739;
  background:linear-gradient(145deg,#4b304e,#27263c 56%,#243d48);
}
.range::-webkit-slider-runnable-track {
  background:linear-gradient(to right,#65c6bc var(--fill),#f0c2db2b var(--fill));
}
.range::-webkit-slider-thumb { background:#fff6fb; }
.transport { background:linear-gradient(145deg,#3c3855,#293248); }
.transport.main {
  background:linear-gradient(145deg,#7652a0,#8a466f);
  box-shadow:inset 0 1px #ffffff36,0 12px 30px #5c2e6b54;
}
.item { background:linear-gradient(145deg,#41344c,#292d42); }
.playlist-row:hover { background:#6aa9a918; }
.playlist-icon {
  background:linear-gradient(145deg,#493550,#2c3349);
  border-color:#efbfd638;
}
.playlist-icon:hover {
  background:linear-gradient(145deg,#5d4666,#355064);
  border-color:#77c9c3;
}
.back { background:linear-gradient(145deg,#493650,#303148); }
.back-to-top {
  background:linear-gradient(135deg,#3d5967f2,#61405df2);
  border-color:#b9e2dc45;
}
#repeat.active,#shuffle.active {
  outline:2px solid #d799bb;
  background:linear-gradient(135deg,#6e4c91,#824467)!important;
}
.task-feedback {
  position:fixed;
  left:12px;
  right:12px;
  top:max(12px,env(safe-area-inset-top));
  z-index:5000;
  box-sizing:border-box;
  padding:15px 62px 15px 18px;
  border:1px solid #b8deda42;
  border-radius:17px;
  color:#fff8fb;
  background:linear-gradient(135deg,#284e5cf2,#443050f2 58%,#5b344df2);
  box-shadow:inset 0 1px #ffffff2b,0 16px 42px #0c091678;
  font-weight:750;
  text-align:center;
  backdrop-filter:blur(18px) saturate(1.2);
}
.task-feedback.working {
  border-color:#75d7c85c;
  background:linear-gradient(135deg,#236b70f2,#39415ff2 58%,#553456f2);
}
.task-feedback.done {
  border-color:#84dec568;
  background:linear-gradient(135deg,#236b5af2,#2f625df2 56%,#4f4968f2);
}
.task-feedback.error {
  border-color:#ef9aa65e;
  background:linear-gradient(135deg,#71384ef2,#7d3f55f2 56%,#4b315cf2);
}
.task-feedback-close {
  position:absolute;
  right:8px;
  top:50%;
  transform:translateY(-50%);
  width:44px;
  height:44px;
  border:1px solid #ffffff20;
  border-radius:13px;
  color:inherit;
  background:#ffffff12;
  box-shadow:inset 0 1px #ffffff18;
  font-size:32px;
  font-weight:400;
  line-height:38px;
  cursor:pointer;
}
.task-feedback-close:hover {
  background:#ffffff22;
}
/* Local Music Library: a focused Aurora Dusk music surface. */
#pc,#songs,#playlist {
  position:relative;
}
#pc::before,#songs::before,#playlist::before {
  content:"";
  position:fixed;
  z-index:-1;
  inset:0;
  pointer-events:none;
  background:
    radial-gradient(520px 420px at 12% 12%,#27848a42,transparent 68%),
    radial-gradient(600px 480px at 92% 24%,#91456b3d,transparent 70%),
    radial-gradient(520px 430px at 54% 100%,#b9844140,transparent 72%);
}
#pc .header,#songs .header,#playlist .header {
  padding:10px 11px;
  border:1px solid #e9b9d72b;
  border-radius:20px;
  background:linear-gradient(125deg,#274f5bbd,#4a315abe 54%,#65384fbd);
  box-shadow:inset 0 1px #ffffff25,0 13px 34px #0c091650;
  backdrop-filter:blur(16px);
}
#pc .header h1,#songs .header h1,#playlist .header h1 {
  color:#fff6fb;
  text-shadow:0 2px 14px #190d245c;
}
#pc .header .back,#songs .header .back,#playlist .header .back {
  border:1px solid #d9c5df2b;
  background:
    radial-gradient(70px 48px at 12% 18%,#68c9be32,transparent 72%),
    radial-gradient(76px 52px at 94% 86%,#b65d7429,transparent 74%),
    linear-gradient(145deg,#405162a8,#49374fa8);
  box-shadow:inset 0 1px #ffffff1d,0 6px 16px #0c091638;
  color:#fff7fb;
  backdrop-filter:blur(10px);
  transition:filter .16s ease,border-color .16s ease,box-shadow .16s ease,transform .12s ease;
}
#pc .header .back:hover,#songs .header .back:hover,#playlist .header .back:hover {
  border-color:#83d5ca4f;
  filter:brightness(1.09) saturate(1.06);
  box-shadow:inset 0 1px #ffffff28,0 7px 19px #17344742;
}
#pc > .card {
  border-color:#8edbd047;
  background:
    linear-gradient(145deg,#254d59e8 0%,#39304be8 52%,#633a50e8 100%);
  box-shadow:inset 0 1px #ffffff27,0 18px 46px #0c09165c;
}
#pc > .card > .label {
  color:#bfe9df;
}
#pc #now-title {
  color:#fff7fc;
  text-shadow:0 1px 12px #150b205c;
}
#pc #now-details {
  color:#dbcbda;
}
#pc .local-now-artwork {
  border:1px solid #f0bdd544;
  background:
    radial-gradient(circle at 30% 25%,#6fcac07a,transparent 34%),
    linear-gradient(145deg,#65406a,#2e5261 58%,#24253b);
  color:#fff0f7;
  box-shadow:inset 0 1px #ffffff35,0 10px 28px #170e235e;
}
#pc .mpv-gain-control { margin-top: .7rem; }
#pc .mpv-gain-labels { display:flex; justify-content:space-between; font-size:.74rem; color:var(--muted); margin:.1rem 2.3rem 0; }
#pc .mpv-gain-wrap { position:relative; flex:1; display:flex; align-items:center; }
#pc .mpv-gain-wrap .range { width:100%; }
#pc .range-row .volume-icon {
  color:#bfe9df;
}
#pc .transport {
  border-color:#cdb9e42b;
  background:linear-gradient(145deg,#3c5167,#34304e);
}
#pc .transport:first-child {
  background:linear-gradient(145deg,#2b6670,#354b68);
}
#pc .transport:last-child {
  background:linear-gradient(145deg,#7a465f,#514367);
}
#pc .transport.main {
  background:linear-gradient(145deg,#7a57a5,#a14e73 56%,#b26f50);
  box-shadow:inset 0 1px #ffffff3d,0 14px 34px #71365c66;
}
#pc #repeat {
  background:linear-gradient(135deg,#315f70,#504475)!important;
}
#pc #shuffle:not(:disabled) {
  background:linear-gradient(135deg,#76519a,#9a4f70)!important;
}
#pc #repeat.active,#pc #shuffle.active {
  outline-color:#78d8ca;
  background:linear-gradient(135deg,#28757a,#72558f)!important;
}
#pc #all-songs {
  border-color:#f1c3da3d;
  background:linear-gradient(135deg,#7a579e,#8c4f80 56%,#a16062)!important;
  box-shadow:inset 0 1px #ffffff2d,0 11px 28px #32172d54;
}
#pc > .section-title {
  color:#f1bfd3;
}
#playlists .playlist-row {
  border:1px solid #eac4d725;
  background:
    radial-gradient(150px 90px at 3% 18%,#3c8b8730,transparent 72%),
    radial-gradient(180px 110px at 98% 86%,#a5536a24,transparent 74%),
    linear-gradient(145deg,#423448e8 0%,#30364ae8 52%,#3d3048e8 100%);
  box-shadow:inset 0 1px #ffffff16,0 9px 24px #0b09143b;
  transition:filter .16s ease,border-color .16s ease,transform .16s ease;
}
#playlists .playlist-row:hover {
  border-color:#88d4ca3d;
  filter:brightness(1.07) saturate(1.05);
}
#playlists .playlist-open {
  background:transparent;
  border:0;
  box-shadow:none;
}
#playlists .playlist-icon {
  background:linear-gradient(145deg,#5d4164,#34485c);
  border-color:#d9b9dd36;
}
#playlists .playlist-icon:hover {
  background:linear-gradient(145deg,#71527a,#3d6570);
  border-color:#79d2c7;
}
#playlists .cover-art,#song-list .cover-art,#playlist-songs .cover-art {
  border:1px solid #f3c7dd2e;
  box-shadow:0 5px 16px #0d09174d;
}
#playlists .cover-fallback,#song-list .cover-fallback,#playlist-songs .cover-fallback {
  background:linear-gradient(145deg,#694463,#315866);
  color:#ffeaf5;
}
#songs .search,#playlist .search {
  border-color:#d8b8dd35;
  background:linear-gradient(145deg,#49344fdd,#29364add);
  box-shadow:inset 0 1px #ffffff14;
}
#songs .search:focus,#playlist .search:focus {
  border-color:#75d7c8;
  box-shadow:0 0 0 4px #75d7c827;
}
#song-list .item,#playlist-songs .item {
  border-color:#edbed628;
  background:
    radial-gradient(130px 80px at 4% 20%,#39857f29,transparent 74%),
    radial-gradient(150px 95px at 97% 82%,#9d506524,transparent 76%),
    linear-gradient(145deg,#433348e5 0%,#2d384be5 54%,#3b3046e5 100%);
  box-shadow:inset 0 1px #ffffff12,0 7px 20px #0b091435;
  transition:filter .16s ease,border-color .16s ease,transform .12s ease;
}
#song-list .item:hover,#playlist-songs .item:hover {
  border-color:#82d2c83d;
  filter:brightness(1.08) saturate(1.05);
}
#song-list .card.details,#playlist-songs .card.details {
  color:#d7c6d5;
  border-color:#e9bdd62b;
  background:linear-gradient(145deg,#443146d9,#29384ad9);
}

.safe-volume-button{width:100%;margin:10px 0 18px}.safe-mark{background-image:linear-gradient(#ffca57,#ffca57);background-repeat:no-repeat;background-size:2px 25px;background-position:var(--safe-x,50%) center}.safe-hidden{display:none!important}.custom-hrir-row{padding:15px;margin:10px 0;border:1px solid #ffffff22;border-radius:18px;background:#ffffff0a}.custom-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.custom-fields label{color:#bcb7cb;font-size:12px;font-weight:700}.custom-fields input,.custom-fields select,#custom-device,#custom-name{width:100%;margin-top:5px;padding:12px;border:1px solid #ffffff25;border-radius:12px;background:#090b14;color:#fff}.custom-actions{margin-top:14px}.custom-create-launch{margin-top:28px!important}.spatial-visual{margin-top:18px;padding:14px;border:1px solid #ffffff22;border-radius:22px;background:radial-gradient(circle at 50% 42%,#32245f88,#090b14 68%);box-shadow:inset 0 1px #ffffff18}.spatial-visual canvas{display:block;width:100%;height:auto;aspect-ratio:1.45}.spatial-caption{text-align:center;color:#b9b4c5;font-size:12px;margin-top:8px}.custom-ranges{margin:14px 2px;color:#b9b4c5;font-size:13px}.mode-dialog{border:1px solid #ffffff33;border-radius:22px;background:#151221;color:#fff;padding:22px;width:min(420px,calc(100vw - 32px));box-shadow:0 24px 80px #000b}.mode-dialog::backdrop{background:#000a;backdrop-filter:blur(8px)}.mode-choice{display:block;padding:12px;margin:8px 0;border:1px solid #ffffff22;border-radius:13px}.mode-dialog-menu{display:flex;gap:10px;justify-content:flex-end;margin-top:18px}
  
.task-feedback-action{margin-left:10px;padding:7px 11px;border:1px solid rgba(255,255,255,.22);border-radius:10px;background:rgba(255,255,255,.12);color:inherit;font-weight:800}
.yt-rating-icon{width:38px;min-width:38px;height:34px;padding:0;display:inline-grid;place-items:center;font-size:18px;line-height:1;border-radius:10px}
.yt-rating-icon svg{width:18px;height:18px;fill:currentColor}
</style>
 </head>
 <body>
  <main>
   <section id="profiles">
    <div class="hero">
     <h1>
      CamillaDSP Studio
     </h1>
    </div>
    <div class="section-title">
     System media
    </div>
    <div class="card">
     <div class="system-media-heading">
      <div class="label">
       Active desktop player
      </div>
     </div>
     <div class="title" id="system-title">
      No system media
     </div>
     <div class="details" id="system-details">
     </div>
     <input aria-label="System media playback position" class="range" id="system-seek" max="1" min="0" step=".1" type="range"/>
     <div class="times">
      <span id="system-elapsed">
       0:00
      </span>
      <span id="system-duration">
       0:00
      </span>
     </div>
     <div class="range-row">
      <span aria-hidden="true" class="volume-icon">
       <svg fill="currentColor" viewbox="0 0 24 24">
        <path d="M3 9v6h4l5 4V5L7 9H3z">
        </path>
       </svg>
      </span>
      <input aria-label="Master volume" class="range" id="system-volume" max="100" min="0" type="range" value="100"/>
      <span aria-hidden="true" class="volume-icon">
       <svg fill="currentColor" viewbox="0 0 24 24">
        <path d="M2 9v6h4l5 4V5L6 9H2z">
        </path>
        <path d="M14 8.2a5 5 0 0 1 0 7.6l1.4 1.4a7 7 0 0 0 0-10.4L14 8.2z">
        </path>
        <path d="M17 5.3a9 9 0 0 1 0 13.4l1.4 1.4a11 11 0 0 0 0-16.2L17 5.3z">
        </path>
       </svg>
      </span>
     </div><button class="action safe-volume-button safe-hidden" id="system-safe-volume">Safe max</button>
     <div class="grid three">
      <button aria-label="Previous" class="transport" id="system-previous">
       <svg class="media-icon" viewbox="0 0 24 24">
        <path d="M6 5v14">
        </path>
        <path d="M18 6.5 8.5 12 18 17.5z">
        </path>
       </svg>
      </button>
      <button aria-label="Play" class="transport main" id="system-toggle">
       <svg class="media-icon" viewbox="0 0 24 24">
        <path class="icon-fill" d="M8 5.5 19 12 8 18.5z" id="system-play-shape">
        </path>
       </svg>
      </button>
      <button aria-label="Next" class="transport" id="system-next">
       <svg class="media-icon" viewbox="0 0 24 24">
        <path d="M18 5v14">
        </path>
        <path d="M6 6.5 15.5 12 6 17.5z">
        </path>
       </svg>
      </button>
     </div>
    </div>
    <button class="action green accent-mint" id="open-pc">
     Local Music Library
    </button>
    <button aria-pressed="false" class="action accent-amber" id="away-display-toggle" style="margin-top:10px" type="button">
     Away and display off
    </button>
    <button class="action accent-violet" id="open-audio-options" style="margin-top:10px" type="button">
     Audio options
    </button>
    <div class="section-title">
     YouTube Music
    </div>
    <div class="grid two" id="youtube-music-card">
     <button class="action accent-violet" id="ytmusic-quickpicks">Quick Picks</button>
     <button class="action accent-red" id="ytmusic-like">Like</button>
     <button class="action accent-cyan" id="ytmusic-liked">Liked Music</button>
     <button class="action accent-mint" id="ytmusic-refresh-cookie">Refresh cookie</button>
    </div>
    <div class="section-title" id="restart-heading">
     Media services
    </div>
    <div aria-labelledby="restart-heading" class="grid two">
     <button class="action accent-cyan" id="restart-sonobus">
      Restart SonoBus
     </button>
     <button class="action accent-orange" id="restart-airplay">
      Restart AirPlay
     </button>
     <button aria-pressed="false" class="action accent-blue" id="restart-vnc">
      Start VNC
     </button>
     <button class="action accent-red" id="audio-toggle" type="button">
      Stop audio
     </button>
    </div>
   </section>
   <section class="hidden" id="audio-options-page">
    <div class="header">
     <button class="back" data-back="profiles">
      Back
     </button>
     <h1>
      Audio options
     </h1>
     <div>
     </div>
    </div>
    <div class="section-title">
     Active profile
    </div>
    <div class="card">
     <div class="title" id="active-profile">
      Loading…
     </div>
     <div class="details" id="active-state">
     </div>
    </div>
    <button class="action accent-violet" id="open-listening">
     Select Listening Profile
    </button>
    <div class="section-title">
     Audio source
    </div>
    <div class="card">
     <div class="title" id="source-title">
      Loading…
     </div>
     <div class="details" id="source-details">
     </div>
     <div class="source-grid" id="mode-grid">
     </div>
    </div>
    <div class="section-title">
     Audio output
    </div>
    <div class="card">
     <div class="title" id="output-title">
      Loading…
     </div>
     <div class="details" id="output-details">
     </div>
    </div>
    <button class="action accent-blue" id="open-output" type="button">
     Configure PC output
    </button>
    <div class="section-title">
     SonoBus Group
    </div>
    <div class="card">
     <div class="label">
      Selected group
     </div>
     <div class="title" id="sonobus-group">
      Loading…
     </div>
     <div class="details" id="sonobus-status">
      Checking SonoBus…
     </div>
    </div>
    <button class="action accent-cyan" id="open-groups">
     Manage SonoBus Group
    </button>
   </section>
   <section class="hidden" id="groups-page">
    <div class="header">
     <button class="back" data-back="audio-options-page">
      Back
     </button>
     <h1>
      SonoBus Group
     </h1>
     <div>
     </div>
    </div>
    <div class="card">
     <select class="search" id="group-select">
     </select>
     <input class="search" id="group-key" placeholder="Profile name"/>
     <input class="search" id="group-name" placeholder="Group name"/>
     <input class="search" id="group-user" placeholder="Username"/>
     <input class="search" id="group-server" placeholder="Connection server" value="aoo.sonobus.net:10998"/>
     <label class="details">
      <input id="group-required" type="checkbox"/>
      Password required
     </label>
     <input class="search" id="group-password" placeholder="Password (never saved)" type="password"/>
     <button class="action primary accent-mint" id="save-group">
      Save Group Profile
     </button>
    </div>
   </section>
   <section class="hidden" id="listening-page">
    <div class="header">
     <button class="back" data-back="audio-options-page">
      Back
     </button>
     <h1>
      Listening Profiles
     </h1>
     <div>
     </div>
    </div>
    <div class="section-title">
     Listening profiles
    </div>
    <input class="search" id="profile-search" placeholder="Search profiles"/>
    <div id="profile-list">
    </div>
    <button class="action custom-create-launch" id="open-custom-filter">Create custom spatial filter</button>
   </section>
   <section class="hidden" id="custom-filter-page">
    <div class="header"><button class="back" data-back="listening-page">Back</button><h1>Custom Spatial Filter</h1><div></div></div>
    <div class="card"><div class="label">Device</div><select id="custom-device"></select><div class="label" style="margin-top:14px">Filter name</div><input id="custom-name" placeholder="My spatial filter"/></div>
    <div class="section-title">Measured SOFA points</div><div id="custom-sofa-list"></div>
    <div class="custom-ranges" id="custom-ranges"></div>
    <div class="grid two custom-actions"><button class="action" id="add-custom-sofa-point">Add SOFA point</button><button class="action primary" id="review-custom-filter">Create filter</button></div><div class="spatial-visual"><canvas id="custom-sphere" width="720" height="500" aria-label="Spatial SOFA point visualization"></canvas><div class="spatial-caption">Live spatial preview • front is at the center, rear points wrap behind the sphere</div></div>
    <dialog class="mode-dialog" id="custom-mode-dialog"><h2>Select filter mode</h2><div id="custom-mode-options"></div><div class="mode-dialog-menu"><button class="action" id="cancel-custom-mode">Cancel</button><button class="action primary" id="apply-custom-filter">Apply</button></div></dialog>
   </section>
   <section class="hidden" id="pc">
    <div class="header">
     <button class="back" id="pc-back">
      Back
     </button>
     <h1>
      PC Music
     </h1>
     <div>
     </div>
    </div>
    <div class="card">
     <div class="label">
      Now playing
     </div>
     <div class="local-now-header">
      <span aria-hidden="true" class="local-now-artwork" id="now-cover">
      </span>
      <div class="local-now-copy">
       <div class="title" id="now-title">
        Nothing playing
       </div>
       <div class="details" id="now-details">
       </div>
      </div>
     </div>
     <input aria-label="Local music playback position" class="range" id="seek" max="1" min="0" step=".1" type="range"/>
     <div class="times">
      <span id="elapsed">
       0:00
      </span>
      <span id="duration">
       0:00
      </span>
     </div>
     <div class="range-row">
      <span aria-hidden="true" class="volume-icon">
       <svg fill="currentColor" viewbox="0 0 24 24">
        <path d="M3 9v6h4l5 4V5L7 9H3z">
        </path>
       </svg>
      </span>
      <input aria-label="Master volume" class="range" id="volume" max="100" min="0" type="range" value="100"/>
      <span aria-hidden="true" class="volume-icon">
       <svg fill="currentColor" viewbox="0 0 24 24">
        <path d="M2 9v6h4l5 4V5L6 9H2z">
        </path>
        <path d="M14 8.2a5 5 0 0 1 0 7.6l1.4 1.4a7 7 0 0 0 0-10.4L14 8.2z">
        </path>
        <path d="M17 5.3a9 9 0 0 1 0 13.4l1.4 1.4a11 11 0 0 0 0-16.2L17 5.3z">
        </path>
       </svg>
      </span>
     </div><button class="action safe-volume-button safe-hidden" id="local-safe-volume">Safe max</button>
     <div class="label mpv-gain-control">MPV volume</div>
     <div class="range-row">
      <span aria-hidden="true" class="volume-icon">
       <svg fill="currentColor" viewbox="0 0 24 24"><path d="M3 9v6h4l5 4V5L7 9H3z"></path></svg>
      </span>
      <div class="mpv-gain-wrap">
       <input aria-label="MPV volume" class="range" id="mpv-gain" max="100" min="0" step="1" type="range" value="100"/>
      </div>
      <span aria-hidden="true" class="volume-icon">
       <svg fill="currentColor" viewbox="0 0 24 24"><path d="M2 9v6h4l5 4V5L6 9H2z"></path><path d="M14 8.2a5 5 0 0 1 0 7.6l1.4 1.4a7 7 0 0 0 0-10.4L14 8.2z"></path></svg>
      </span>
     </div>
     <div class="mpv-gain-labels"><span>0%</span><span>100%</span></div>
     <div class="grid two">
      <button class="action" id="repeat">
       Repeat Off
      </button>
      <button class="action" id="shuffle" title="Shuffle the remaining MPV queue">
       Shuffle remaining queue
      </button>
     </div>
    </div>
    <div class="grid three pc-transport">
     <button aria-label="Previous" class="transport" data-cmd="previous">
      <svg class="media-icon" viewbox="0 0 24 24">
       <path d="M6 5v14">
       </path>
       <path d="M18 6.5 8.5 12 18 17.5z">
       </path>
      </svg>
     </button>
     <button aria-label="Play" class="transport main" data-cmd="toggle">
      <svg class="media-icon" viewbox="0 0 24 24">
       <path class="icon-fill" d="M8 5.5 19 12 8 18.5z" id="local-play-shape">
       </path>
      </svg>
     </button>
     <button aria-label="Next" class="transport" data-cmd="next">
      <svg class="media-icon" viewbox="0 0 24 24">
       <path d="M18 5v14">
       </path>
       <path d="M6 6.5 15.5 12 6 17.5z">
       </path>
      </svg>
     </button>
    </div>
    <div class="grid pc-library-actions">
     <button class="action primary accent-violet" id="all-songs">
      All Songs
     </button>
    </div>
    <div class="section-title">
     Playlists
    </div>
    <div class="list" id="playlists">
    </div>
   </section>
   <section class="hidden" id="songs">
    <div class="header">
     <button class="back" data-back="pc">
      Back
     </button>
     <h1>
      All Songs
     </h1>
     <div>
     </div>
    </div>
    <input aria-label="Search all songs" class="search" id="song-search" placeholder="Search all songs" type="search"/>
    <div class="list" id="song-list">
    </div>
   </section>
   <section class="hidden" id="playlist">
    <div class="header">
     <button class="back" data-back="pc">
      Back
     </button>
     <h1 id="playlist-title">
      Playlist
     </h1>
     <div>
     </div>
    </div>
    <input aria-label="Search this playlist" class="search" id="playlist-search" placeholder="Search this playlist" type="search"/>
    <div class="list" id="playlist-songs">
    </div>
   </section>
   <button class="back-to-top hidden" id="back-to-top" type="button">
    Go back to top of page ↑
   </button>
   <div class="output-modal hidden" id="output-modal">
    <div class="output-sheet">
     <div class="label">
      PC output
     </div>
     <div class="title" id="output-mode-title">
      Choose output
     </div>
     <div class="details">
      Select device, profile, route, then sink.
     </div>
     <div class="output-options">
      <label class="details" id="output-card-step">
       Device
       <select class="search" id="output-card">
       </select>
      </label>
      <label class="details hidden" id="output-profile-step">
       Profile
       <select class="search" id="output-profile">
       </select>
      </label>
      <label class="details hidden" id="output-route-step">
       Route
       <select class="search" id="output-route">
       </select>
      </label>
      <label class="details hidden" id="output-sink-step">
       Sink
       <select class="search" id="output-sink">
       </select>
      </label>
     </div>
     <div class="modal-actions">
      <button class="action" id="output-cancel">
       Back
      </button>
      <button class="action primary" id="output-next">
       Select
      </button>
     </div>
    </div>
   </div>
  <section class="hidden" id="ytmusic-liked-page">
   <div class="page-head">
    <button class="back" id="ytmusic-liked-back" type="button">Back</button>
    <div><h1>Liked Music</h1></div>
   </div>
   <input class="search" id="ytmusic-liked-search" type="search" placeholder="Search liked songs or artists" autocomplete="off" aria-label="Search Liked Music">
   <div class="liked-toolbar"><button class="action accent-red" id="ytmusic-remove-selected" disabled>Remove selected</button><span class="details" id="ytmusic-selected-count">0 selected</span></div>
   <div class="list" id="ytmusic-liked-list"></div>
  </section>
  </main>
  <script>
   const $=selector=>document.querySelector(selector);
const screens=['profiles','audio-options-page','pc','songs','playlist','groups-page','listening-page','custom-filter-page','ytmusic-liked-page'].map(id=>$('#'+id));
let all=[],inside=[],current='';
const state={
  system:{slider:$('#system-seek'),elapsed:$('#system-elapsed'),durationLabel:$('#system-duration'),volume:$('#system-volume'),duration:0,dragging:false,polling:false,repoll:false,clockPosition:0,clockAt:0,clockPlaying:false,lastAvailableAt:0,trackKey:'',skipPending:false,skipFrom:'',skipStarted:0,skipWarned:false,volumeHold:0,volumeTimer:null,volumePending:null},
  local:{slider:$('#seek'),elapsed:$('#elapsed'),durationLabel:$('#duration'),volume:$('#volume'),mpvGain:$('#mpv-gain'),duration:0,dragging:false,polling:false,volumeHold:0,volumeTimer:null,volumePending:null,mpvGainHold:0,mpvGainTimer:null,mpvGainPending:null}
};
const fmt=value=>{const v=Math.max(0,Number(value)||0);return Math.floor(v/60)+':'+String(Math.floor(v)%60).padStart(2,'0')};
function show(id){screens.forEach(screen=>screen.classList.toggle('hidden',screen.id!==id));scrollTo({top:0,behavior:'smooth'});updateBackToTop()}
function updateBackToTop(){const visible=['songs','playlist'].some(id=>!$('#'+id).classList.contains('hidden'));$('#back-to-top').classList.toggle('hidden',!visible||window.scrollY<320)}
window.addEventListener('scroll',updateBackToTop,{passive:true});$('#back-to-top').onclick=()=>window.scrollTo({top:0,behavior:'smooth'});$('#open-groups').onclick=()=>show('groups-page');$('#open-listening').onclick=()=>{show('listening-page');filterDirectoryPath='';openFilterDirectory('').catch(error=>note(error.message,true))};
function notificationBox(){const banners=[...document.querySelectorAll('#global-task-feedback')];let banner=banners.shift()||null;banners.forEach(item=>item.remove());if(!banner){banner=document.createElement('div');banner.id='global-task-feedback';banner.className='task-feedback hidden';banner.setAttribute('role','status');banner.setAttribute('aria-live','polite');const message=document.createElement('span');message.className='task-feedback-message';const close=document.createElement('button');close.type='button';close.className='task-feedback-close';close.textContent='×';close.setAttribute('aria-label','Close notification');close.addEventListener('click',()=>{clearTimeout(showTaskFeedback.timer);banner.classList.add('hidden')});banner.append(message,close);document.body.appendChild(banner)}return banner}function showTaskFeedback(message,kind='working'){const banner=notificationBox(),messageNode=banner.querySelector('.task-feedback-message');banner.querySelectorAll('.task-feedback-action').forEach(action=>action.remove());if(messageNode)messageNode.textContent=String(message||'Working…');banner.classList.remove('working','done','error','hidden');banner.classList.add(['working','done','error'].includes(kind)?kind:'working');clearTimeout(showTaskFeedback.timer);if(kind!=='working')showTaskFeedback.timer=setTimeout(()=>banner.classList.add('hidden'),10000)}function note(message,error=false){if(!message)return;if(error===true){showTaskFeedback(message,'error');return}showTaskFeedback(message,'done');if(error&&typeof error==='object'&&error.label&&typeof error.action==='function'){const banner=notificationBox(),action=document.createElement('button');action.type='button';action.className='task-feedback-action';action.textContent=error.label;action.onclick=async()=>{action.disabled=true;try{await error.action();banner.classList.add('hidden')}catch(e){showTaskFeedback(e?.message||String(e),'error')}finally{action.disabled=false}};banner.insertBefore(action,banner.querySelector('.task-feedback-close'))}}
/* FEEDBACK_POLICY_V13: only actions without inherent page/audio feedback get an in-button loader. */
const requestCounts=new WeakMap(),requestWasDisabled=new WeakMap();
let requestClick=null;
function rgbChannels(value){const match=String(value).match(/[\d.]+/g);return match?match.slice(0,3).map(Number):[0,0,0]}
function busySheenFor(button){
 const [r,g,b]=rgbChannels(getComputedStyle(button).backgroundColor);
 const light=(.2126*r+.7152*g+.0722*b)/255;
 return light>.58?'rgba(0,0,0,.28)':'rgba(255,255,255,.34)'
}
function refreshButtonSheen(button){if(button?.classList.contains('busy'))button.style.setProperty('--busy-sheen',busySheenFor(button))}
function beginButtonRequest(button){
 if(!button||button.dataset.loading==='off')return;
 const count=requestCounts.get(button)||0;
 if(count===0){requestWasDisabled.set(button,button.disabled);button.disabled=true;button.classList.add('busy');refreshButtonSheen(button)}
 requestCounts.set(button,count+1)
}
function endButtonRequest(button){
 if(!button||button.dataset.loading==='off')return;
 const count=Math.max(0,(requestCounts.get(button)||1)-1);
 if(count){requestCounts.set(button,count);return}
 requestCounts.delete(button);button.classList.remove('busy');button.style.removeProperty('--busy-sheen');button.disabled=Boolean(requestWasDisabled.get(button));requestWasDisabled.delete(button)
}
new MutationObserver(records=>records.forEach(record=>refreshButtonSheen(record.target))).observe(document.documentElement,{subtree:true,attributes:true,attributeFilter:['class','style','aria-pressed','aria-selected']});
document.addEventListener('click',event=>{
 const button=event.target.closest('button');
 if(!button||button.disabled||button.dataset.loading==='off')return;
 requestClick=button;beginButtonRequest(button);
 setTimeout(()=>{if(requestClick===button)requestClick=null;endButtonRequest(button)},450)
},true);
const mediaControlFetch=window.fetch.bind(window);
window.fetch=(...args)=>{
 const button=requestClick;requestClick=null;
 if(button)beginButtonRequest(button);
 const request=mediaControlFetch(...args);
 if(button)request.finally(()=>endButtonRequest(button));
 return request
};
async function api(url,options={}){const response=await fetch(url,{cache:'no-store',...options});const data=await response.json().catch(()=>null);if(!data||typeof data!=='object'||Array.isArray(data))throw Error('Invalid server response');if(!response.ok||data.ok===false)throw Error(data.error||'Request failed');return data}
const post=(url,data={})=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function fill(element,value,maximum){const max=Number(maximum),v=Number(value);const percent=Number.isFinite(max)&&max>0&&Number.isFinite(v)?Math.max(0,Math.min(100,v/max*100)):0;element.style.setProperty('--fill',percent+'%')}
function renderTimeline(media,position,duration){
  const total=Number.isFinite(Number(duration))?Math.max(0,Number(duration)):0;
  const current=Number.isFinite(Number(position))?Math.max(0,Math.min(Number(position),total>0?total:Number(position))):0;
  media.duration=total;
  media.slider.max=String(total>0?total:1);
  if(!media.dragging)media.slider.value=String(current);
  const shown=media.dragging?Number(media.slider.value):current;
  fill(media.slider,shown,total);
  media.elapsed.textContent=fmt(shown);
  media.durationLabel.textContent=fmt(total);
}
function renderActiveProfile(data){
  const info=data.filters?.[data.active];
  $('#active-profile').textContent=data.active?(info?.displayLabel||info?.label||data.active):'No profile';
  $('#active-state').textContent=data.active?((info?.label||data.active)+' · '+(data.active==='__no_filter__'?'Bypassed':data.running?'Running':'Stopped')):'Stopped'
}
function renderSonobusStatus(mode,groups){
  const selected=groups?.profiles?.[groups.active];
  $('#sonobus-group').textContent=selected?.group||'No group selected';
  $('#sonobus-status').textContent=(mode.sonobus?'Process running':'Process stopped')+
  (selected?' • Profile: '+groups.active+' • User: '+selected.username:'')
}
function renderVncState(data){
  const button=$('#restart-vnc'),running=!!data?.running;
  button.textContent=running?'Stop VNC':'Start VNC';
  button.setAttribute('aria-pressed',String(running));
  button.classList.toggle('mode-active',running);
}
async function refreshVncState(){
  try{renderVncState(await api('/api/vnc-state'))}
  catch(error){console.warn('VNC state refresh failed',error)}
}
async function busy(button,work,label='Working…',doneLabel=null,feedback=false){try{const result=await work();if(feedback&&doneLabel)showTaskFeedback(doneLabel,'done');return result}catch(error){if(feedback)showTaskFeedback(error?.message||String(error),'error');else console.error(error);throw error}}
function setPlayIcon(shape,button,playing){if(!shape||!button)return;shape.setAttribute('d',playing?'M8 5h3v14H8z M14 5h3v14h-3z':'M8 5.5 19 12 8 18.5z');button.setAttribute('aria-label',playing?'Pause':'Play');button.classList.toggle('playing',playing)}
function setSystemAvailability(data){for(const id of ['system-previous','system-toggle','system-next'])$('#'+id).disabled=!data.available;$('#system-seek').disabled=!data.available||!data.seekable}
function systemTrackKey(data){
  if(!data||!data.available)return '';
  return [data.player||'',data.trackId||'',data.title||'',data.artist||'',data.duration||''].join('\x1f')
}
function resetSystemPosition(){
  const media=state.system;
  media.clockPlaying=false;media.clockAt=0;media.clockPosition=0;
  media.dragging=false;media.slider.classList.remove('dragging');
  renderTimeline(media,0,0)
}
function systemPositionError(message){
  const media=state.system;
  if(!media.skipWarned){media.skipWarned=true;showTaskFeedback(message,'error')}
}
function renderSystem(data){
  const media=state.system,now=performance.now();
  if(!data||typeof data!=='object')return;
  if(!data.available&&media.lastAvailableAt&&now-media.lastAvailableAt<1500){
    if(media.skipPending)resetSystemPosition();
    return
  }
  if(data.available)media.lastAvailableAt=now;
  else media.lastAvailableAt=0;
  $('#system-title').textContent=data.title||'No system media';
  $('#system-details').textContent=[data.artist,data.player,data.status].filter(Boolean).join(' • ');
  const key=systemTrackKey(data),duration=Number(data.duration),position=data.position===null||data.position===undefined||data.position===''?NaN:Number(data.position);
  const valid=!!data.available&&Number.isFinite(duration)&&duration>0&&Number.isFinite(position)&&position>=0&&position<=duration+2;
  if(media.skipPending){
    // Ignore snapshots from the old track after the skip command.
    const changed=key&&key!==media.skipFrom;
    const restarted=key&&key===media.skipFrom&&valid&&position<=3;
    if(!changed&&!restarted){
      resetSystemPosition();
      if(now-media.skipStarted>2000)systemPositionError('Could not confirm the new track position. Marker held at 0:00.');
      return
    }
    media.skipPending=false;media.skipWarned=false
  }
  media.trackKey=key;
  if(!valid){
    resetSystemPosition();
    if(data.available)systemPositionError('System media position is unavailable or invalid. Marker held at 0:00.');
  }else{
    media.skipWarned=false;
    media.clockPosition=Math.min(position,duration);
    media.clockAt=now;
    media.clockPlaying=!!data.playing;
    renderTimeline(media,media.clockPosition,duration)
  }
  setSystemAvailability(data);
  if(media.volumePending!==null&&Number.isFinite(data.volume)&&Math.abs(data.volume-media.volumePending)<=1)media.volumePending=null;
  if(media.volumePending===null&&now>=media.volumeHold&&document.activeElement!==media.volume&&Number.isFinite(data.volume)){
    displayMasterVolume(data.volume)
  }
  setPlayIcon($('#system-play-shape'),$('#system-toggle'),!!data.playing)
}
function tickSystem(){
  const media=state.system;
  if(document.hidden||media.skipPending||!media.clockPlaying||media.dragging||!media.clockAt||media.duration<=0)return;
  renderTimeline(media,Math.min(media.duration,media.clockPosition+(performance.now()-media.clockAt)/1000),media.duration)
}
async function pollSystem(force=false){
  const media=state.system;
  if(media.polling){if(force)media.repoll=true;return}
  media.polling=true;
  try{renderSystem(await api('/api/system-media'))}
  catch(error){resetSystemPosition();systemPositionError('System media update failed: '+(error?.message||String(error)))}
  finally{media.polling=false;if(media.repoll){media.repoll=false;pollSystem()}}
}
async function pollLocal(){const media=state.local;if(media.polling)return;media.polling=true;try{if(!$('#pc').classList.contains('hidden')){const data=await api('/api/player');$('#now-title').textContent=data.title||'Nothing playing';updateLocalNowArtwork(data.cover||'');$('#now-details').textContent=[data.artist,data.album].filter(Boolean).join(' • ')||(data.path||'');renderTimeline(media,data.currentTime,data.duration);if(media.volumePending!==null&&Number.isFinite(data.volume)&&Math.abs(data.volume-media.volumePending)<=1){media.volumePending=null}if(media.volumePending===null&&performance.now()>=media.volumeHold&&document.activeElement!==media.volume&&Number.isFinite(data.volume)){displayMasterVolume(data.volume)}$('#shuffle').disabled=!data.canShuffleQueue;$('#repeat').textContent='Repeat '+({off:'Off',all:'All',one:'1'}[data.repeat]||'Off');$('#repeat').classList.toggle('active',data.repeat!=='off');setPlayIcon($('#local-play-shape'),document.querySelector('[data-cmd="toggle"]'),data.playing)}}catch(error){note(error.message,true)}finally{media.polling=false}}
function bindSeek(media,url){
  const slider=media.slider;
  const begin=()=>{media.dragging=true;slider.classList.add('dragging')};
  const end=()=>{media.dragging=false;slider.classList.remove('dragging')};
  slider.addEventListener('pointerdown',begin);
  slider.addEventListener('pointercancel',end);
  slider.addEventListener('touchstart',begin,{passive:true});
  slider.oninput=()=>{media.dragging=true;const value=Number(slider.value);fill(slider,value,media.duration);media.elapsed.textContent=fmt(value)};
  slider.onchange=async()=>{const target=Math.max(0,Number(slider.value)||0);end();try{await post(url,{seconds:target});if(media===state.system)await pollSystem(true)}catch(error){note(error.message,true);if(media===state.system)await pollSystem(true)}};
}
function displayMasterVolume(value,origin=null){
  const v=Number(value);
  if(!Number.isFinite(v))return;
  for(const media of [state.system,state.local]){
    if(media!==origin && (document.activeElement===media.volume || media.volumePending!==null))continue;
    media.volume.value=String(v);fill(media.volume,v,100)
  }
}
let volumeWriteQueue=Promise.resolve();
function bindRangeWheel(control){
  control.addEventListener('wheel',event=>{
    if(event.deltaY===0)return;
    event.preventDefault();
    const min=Number(control.min||0),max=Number(control.max||100);
    const step=event.shiftKey?0.1:1;
    const direction=event.deltaY<0?1:-1;
    const value=Math.max(min,Math.min(max,Math.round((Number(control.value)+direction*step)*10)/10));
    if(value===Number(control.value))return;
    control.value=String(value);
    control.dispatchEvent(new Event('input',{bubbles:true}));
    clearTimeout(control._wheelCommit);
    control._wheelCommit=setTimeout(()=>control.dispatchEvent(new Event('change',{bubbles:true})),140);
  },{passive:false});
}
function bindVolume(media,url){
  const control=media.volume;
  const send=()=>{
    const value=Math.max(0,Math.min(100,Number(control.value)||0));
    for(const item of [state.system,state.local]){
      item.volumeHold=performance.now()+1500;item.volumePending=value;
    }
    for(const item of [state.system,state.local]){item.volume.value=String(value);fill(item.volume,value,100)}
    volumeWriteQueue=volumeWriteQueue.catch(()=>{}).then(()=>post(url,{volume:value}));
    volumeWriteQueue.then(result=>{
      const actual=Number(result.volume);
      if(Number.isFinite(actual)){
        for(const item of [state.system,state.local])item.volumePending=null;
        displayMasterVolume(actual)
      }
    }).catch(error=>{
      for(const item of [state.system,state.local])item.volumePending=null;
      note(error.message,true);pollSystem(true);pollLocal()
    })
  };
  control.oninput=()=>{
    const value=Number(control.value);
    media.volumeHold=performance.now()+1500;
    for(const item of [state.system,state.local]){item.volume.value=String(value);fill(item.volume,value,100)}
    clearTimeout(media.volumeTimer);media.volumeTimer=setTimeout(send,90)
  };
  control.onchange=()=>{clearTimeout(media.volumeTimer);send()};
  bindRangeWheel(control)
}
function bindMpvGain(){
  const media=state.local,control=media.mpvGain;
  const send=()=>{
    const value=Math.max(0,Math.min(100,Number(control.value)||0));
    media.mpvGainHold=performance.now()+1500;media.mpvGainPending=value;
    fill(control,value,100);
    post('/api/player/mpv-gain',{volume:value}).then(result=>{
      const actual=Number(result.volume);
      media.mpvGainPending=null;
      if(Number.isFinite(actual)){control.value=String(actual);fill(control,actual,100)}
    }).catch(error=>{media.mpvGainPending=null;note(error.message,true);pollLocal()})
  };
  control.oninput=()=>{media.mpvGainHold=performance.now()+1500;fill(control,Number(control.value),100);clearTimeout(media.mpvGainTimer);media.mpvGainTimer=setTimeout(send,90)};
  control.onchange=()=>{clearTimeout(media.mpvGainTimer);send()};
  bindRangeWheel(control)
}
function coverNode(url,large=false){const node=document.createElement(url?'img':'div');node.className='cover-art'+(large?' large':'')+(url?'':' cover-fallback');if(url){node.src=url;node.loading=large?'eager':'lazy';node.alt='Album cover';node.onerror=()=>{const fallback=coverNode('',large);fallback.id=node.id;fallback.dataset.failedCover=url;node.replaceWith(fallback)}}else{node.textContent='♫';node.setAttribute('aria-label','No album art')}return node}
let localNowArtworkURL='';
function updateLocalNowArtwork(url){const next=url||'';if(next===localNowArtworkURL)return;localNowArtworkURL=next;const tile=$('#now-cover');tile.replaceChildren();if(!next)return;const image=document.createElement('img');image.alt='';image.decoding='async';image.addEventListener('error',()=>image.remove(),{once:true});image.src=next;tile.appendChild(image)}
function render(sel,data,query){const list=$(sel);list.replaceChildren();const term=query.toLowerCase();const filtered=data.filter(song=>!term||[song.title,song.artist,song.album,song.relative].join(' ').toLowerCase().includes(term));if(!filtered.length){const empty=document.createElement('div');empty.className='card details';empty.textContent=query?'No matches':'Nothing here yet';list.append(empty);return}const fragment=document.createDocumentFragment();for(const song of filtered){const button=document.createElement('button');button.className='item';button.innerHTML='<span class="name"></span><span class="meta"></span>';button.children[0].textContent=song.title;button.children[1].textContent=[song.artist,song.album].filter(Boolean).join(' • ')||song.relative;const wrap=document.createElement('div');wrap.className='item-cover';const copy=document.createElement('div');copy.className='copy';copy.append(...button.children);wrap.append(coverNode(song.cover),copy);button.append(wrap);button.onclick=async()=>{try{await post('/api/play/song',{path:song.path});await refreshVolatile()}catch(error){console.error(error)}};fragment.append(button)}list.append(fragment)}
function selectPrompt(label){const option=new Option(label,'',false,false);option.disabled=true;return option}
const MODE_LABELS={ipad_ipad:'External',laptop_laptop:'PC',ipad_laptop:'PC',ipad_both:'Both',laptop_ipad:'External',laptop_both:'Both'};const LOCAL_MODES=new Set(['ipad_laptop','ipad_both','laptop_laptop','laptop_both']);let pendingMode=null,outputTopology=null,outputProfileBusy=false,outputPreviewToken=null;const outputCard=$('#output-card'),outputProfile=$('#output-profile'),outputRoute=$('#output-route'),outputSink=$('#output-sink');function selectedCard(){return outputTopology?.cards.find(item=>item.name===outputCard.value)}
function labelledOutputOption(row){return /^\[(?:N\/A|INT|MON|LOOP|VIRT)\] /.test(String(row?.label||''))}
function mediaControlOutputLabel(row,unavailable=false){
  const label=String(row?.label||'').replace(/^\[(?:N\/A|INT|MON|LOOP|VIRT)\] /,'');
  return (unavailable?'[N/A] ':'')+label;
}
function availableOption(row,profile){
  return row.available!=='no'&&row.available!=='false'&&
  !['off','[out] off'].includes(String(row.name||'').toLowerCase())&&
  (!row.profiles?.length||row.profiles.map(String).includes(String(profile)));
}
function fillRoutes(preferred=''){
  const card=selectedCard(),profile=outputProfile.value,old=preferred||outputRoute.value;
  outputRoute.replaceChildren(selectPrompt('Select route…'));
  for(const route of card?.routes||[]){
    const blocked=!availableOption(route,profile);
    const option=new Option(mediaControlOutputLabel(route,blocked),String(route.index));
    option.disabled=blocked;outputRoute.add(option);
  }
  const options=[...outputRoute.options],saved=outputTopology?.saved?.port;
  const choice=[old,saved,...(card?.activeRoutes||[]).map(String)].find(value=>options.some(o=>o.value===value&&!o.disabled));
  if(choice!==undefined)outputRoute.value=choice;
  else outputRoute.selectedIndex=-1;
  fillSinks();
}
function fillSinks(preferred=''){
  const card=selectedCard(),route=card?.routes.find(item=>String(item.index)===outputRoute.value);
  const devices=(route?.devices||[]).map(String),prior=preferred||outputSink.value;
  outputSink.replaceChildren(selectPrompt('Select sink…'));
  if(!card)return;
  for(const sink of card.sinks||[]){
    const incompatible=devices.length>0&&sink.profileDevice!=null&&!devices.includes(String(sink.profileDevice));
    const option=new Option(mediaControlOutputLabel(sink,incompatible),sink.name);
    option.disabled=incompatible;outputSink.add(option);
  }
  const options=[...outputSink.options];
  const choice=[prior,outputTopology?.saved?.sink].find(value=>options.some(o=>o.value===value&&!o.disabled));
  if(choice!==undefined)outputSink.value=choice;
  else outputSink.selectedIndex=options.findIndex(o=>!o.disabled);
}
function fillProfiles(preferred=''){
  const card=selectedCard(),old=preferred||outputProfile.value;outputProfile.replaceChildren(selectPrompt('Select profile…'));
  for(const profile of card?.profiles||[]){
    const blocked=!!card?.internal||labelledOutputOption(card)||labelledOutputOption(profile)||!availableOption(profile);
    const option=new Option(mediaControlOutputLabel(profile,blocked),String(profile.index));
    option.disabled=blocked;outputProfile.add(option);
  }
  const options=[...outputProfile.options];
  const choice=[old,String(card?.activeProfile),outputTopology?.rememberedProfiles?.[card?.name]].find(value=>options.some(o=>o.value===value&&!o.disabled));
  if(choice!==undefined)outputProfile.value=choice;else outputProfile.selectedIndex=-1;
  fillRoutes();
}
function fillCards(preferred=''){
  const previous=preferred||outputCard.value;outputCard.replaceChildren(selectPrompt('Select device…'));
  for(const card of outputTopology?.cards||[]){
    const blocked=!!card.internal||labelledOutputOption(card);
    const option=new Option(card.label,card.name);
    option.disabled=blocked;outputCard.add(option);
  }
  const options=[...outputCard.options];
  const choice=[previous,outputTopology?.saved?.card].find(value=>options.some(o=>o.value===value&&!o.disabled));
  if(choice!==undefined)outputCard.value=choice;
  else outputCard.selectedIndex=options.findIndex(o=>!o.disabled);
  fillProfiles();
}
async function refreshDevicePicker(cardName){
  outputTopology=await api('/api/outputs');fillCards(cardName);
}
async function applyMode(mode,output=null){const b=document.querySelector(`[data-mode="${mode}"]`);try{await post('/api/mode',{mode,password:$('#group-password').value,output});await refreshAll()}catch(error){note(error.message,true)}}
let outputStep='card';
const outputSteps=['card','profile','route','sink'];
function showOutputStep(step){
  outputStep=step;
  for(const name of outputSteps)$('#output-'+name+'-step').classList.toggle('hidden',name!==step);
  $('#output-cancel').textContent='Back';$('#output-next').textContent=step==='sink'?'Select sink':'Select';
}
async function chooseOutput(mode){
  const topology=await api('/api/outputs');
  if(!topology.cards?.length)throw Error('No playback devices are exposed');
  const preview=await post('/api/output-preview-begin');
  const token=preview.previewToken;
  try{
    outputPreviewToken=token;pendingMode=mode;outputTopology=topology;
    fillCards(topology.saved?.card||'');
    if(!selectedCard())throw Error('No playback device is exposed');
    showOutputStep('card');$('#output-mode-title').textContent=MODE_LABELS[mode];
    $('#output-modal').classList.remove('hidden');
  }catch(error){
    try{await post('/api/output-preview-cancel',{token})}
    catch(rollback){throw Error(error.message+'; preview rollback failed: '+rollback.message)}
    outputPreviewToken=null;pendingMode=null;outputTopology=null;throw error;
  }
}
async function commitOutput(){
  if(outputProfileBusy||!pendingMode)return;
  const card=selectedCard(),profile=outputProfile.value;
  if(!card)throw Error('Choose an exposed playback device');
  if(!outputProfile.selectedOptions[0]||outputProfile.selectedOptions[0].disabled)throw Error('Select an available profile');
  if(card.routes.length&&(!outputRoute.selectedOptions[0]||outputRoute.selectedOptions[0].disabled))throw Error('Select an available route');
  if(!outputSink.value||!outputSink.selectedOptions[0]||outputSink.selectedOptions[0].disabled)throw Error('Select an exposed playback sink');
  const output={card:card.name,profile,sink:outputSink.value,port:outputRoute.value};
  outputProfileBusy=true;
  try{
    showTaskFeedback('Applying playback sink','working');
    await post('/api/output-activate',{output,mode:pendingMode,password:$('#group-password').value,token:outputPreviewToken});
    outputTopology=await api('/api/outputs');fillCards(card.name);
    $('#output-modal').classList.add('hidden');pendingMode=null;outputPreviewToken=null;showOutputStep('card');showTaskFeedback('Playback sink applied','done');await refreshAll();
  }catch(error){showTaskFeedback(error.message,'error');await refreshDevicePicker(card.name)}
  finally{outputProfileBusy=false}
}
outputCard.onchange=()=>{if(!outputProfileBusy)fillProfiles()};
outputProfile.onchange=()=>{if(!outputProfileBusy)fillRoutes()};
outputRoute.onchange=()=>{if(!outputProfileBusy)fillSinks()};
for(const [source,items] of [['External',['ipad_ipad','ipad_laptop','ipad_both']],['PC',['laptop_ipad','laptop_laptop','laptop_both']]]){const heading=document.createElement('div');heading.className='source-heading';heading.textContent='Playing from '+source;$('#mode-grid').append(heading);for(const key of items){const b=document.createElement('button');b.className='action';b.dataset.mode=key;b.textContent=MODE_LABELS[key];b.onclick=async()=>{try{await applyMode(key)}catch(error){note(error.message,true)}};$('#mode-grid').append(b)}}$('#open-output').onclick=async()=>{try{const current=(await api('/api/volatile')).mode.mode;if(!LOCAL_MODES.has(current))throw Error('PC output can only be configured while the current audio output includes PC');await chooseOutput(current)}catch(error){note(error.message,true)}};$('#output-next').onclick=async()=>{
  if(outputProfileBusy)return;
  const selector={card:outputCard,profile:outputProfile,route:outputRoute,sink:outputSink}[outputStep];
  if(!selector?.value||!selector.selectedOptions[0]||selector.selectedOptions[0].disabled){
    showTaskFeedback('Select an available option','error');return;
  }
  if(outputStep==='sink'){commitOutput().catch(error=>showTaskFeedback(error.message,'error'));return;}
  outputProfileBusy=true;
  try{
    if(outputStep==='card'){
      const card=selectedCard();
      if(!card)throw Error('Choose an exposed playback device');
      if(card.internal||labelledOutputOption(card))throw Error('That playback device is informational. Select another device.');
      // Only Select triggers pause and normalization; changing the dropdown is read-only.
      outputTopology=await post('/api/output-device-stage',{card:card.name,token:outputPreviewToken});
      fillCards(card.name);outputProfile.value=String(outputTopology.stageSelection.profile);fillRoutes(outputTopology.stageSelection.port);fillSinks(outputTopology.stageSelection.sink);showOutputStep('profile');
    }else if(outputStep==='profile'){
      const card=selectedCard(),profile=outputProfile.value;
      outputTopology=await post('/api/output-profile-stage',{card:card.name,profile,mode:pendingMode,token:outputPreviewToken});
      fillCards(card.name);outputProfile.value=profile;fillRoutes(outputTopology.stageSelection.port);fillSinks(outputTopology.stageSelection.sink);
      showOutputStep(selectedCard()?.routes?.length?'route':'sink');
    }else if(outputStep==='route'){
      const card=selectedCard(),profile=outputProfile.value,route=outputRoute.value;
      outputTopology=await post('/api/output-route-stage',{card:card.name,profile,route,mode:pendingMode,token:outputPreviewToken});
      fillCards(card.name);outputProfile.value=profile;fillRoutes(route);fillSinks(outputTopology.stageSelection.sink);showOutputStep('sink');
    }
  }catch(error){showTaskFeedback(error.message,'error')}
  finally{outputProfileBusy=false}
};
async function closeOutputPicker(){
  if(outputProfileBusy)return;
  outputProfileBusy=true;
  try{await post('/api/output-preview-cancel',{token:outputPreviewToken})}catch(error){
    showTaskFeedback('Could not clear output selection: '+error.message,'error');
    outputProfileBusy=false;return;
  }
  $('#output-modal').classList.add('hidden');pendingMode=null;outputTopology=null;outputPreviewToken=null;
  showOutputStep('card');outputProfileBusy=false;
}
$('#output-cancel').onclick=()=>{closeOutputPicker()}
;function loadGroups(data){const state=data.groups,select=$('#group-select');select.replaceChildren(selectPrompt('Select group…'),...Object.entries(state.profiles).map(([key,g])=>new Option(key+' • '+g.group,key,key===state.active,key===state.active)));const show=()=>{const key=select.value||state.active,g=state.profiles[key];if(!g)return;$('#group-key').value=key;$('#group-name').value=g.group;$('#group-user').value=g.username;$('#group-server').value=g.server;$('#group-required').checked=!!g.passwordRequired};select.onchange=show;show()}$('#save-group').onclick=async()=>{try{await post('/api/groups/save',{key:$('#group-key').value,group:$('#group-name').value,username:$('#group-user').value,server:$('#group-server').value,passwordRequired:$('#group-required').checked});await refreshAll()}catch(error){note(error.message,true)}};function ytIcon(label,path){const b=document.createElement('button');b.type='button';b.className='playlist-icon yt-rating-icon';b.title=label;b.setAttribute('aria-label',label);b.innerHTML=`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>`;return b}
let ytLikedSongs=[],ytLikedSelected=new Set();
function updateYtLikedSelection(){
 const count=ytLikedSelected.size,button=$('#ytmusic-remove-selected');
 $('#ytmusic-selected-count').textContent=count+' selected';button.disabled=!count;
}
async function removeYtLiked(items,button){
 if(!items.length)return;
 const label=items.length===1?(items[0].title||'this song'):items.length+' selected songs';
 if(!confirm('Are you sure you want to remove '+label+' from Liked Music?'))return;
 const result=await post('/api/ytmusic/remove-items',{items});
 const removed=new Set(items.map(song=>song.videoId));
 ytLikedSongs=ytLikedSongs.filter(song=>!removed.has(song.videoId));
 for(const id of removed)ytLikedSelected.delete(id);
 renderYtLiked();await refreshYtLike();return result;
}
function renderYtLiked(){
 const box=$('#ytmusic-liked-list'),query=$('#ytmusic-liked-search').value.trim().toLocaleLowerCase();
 const songs=query?ytLikedSongs.filter(song=>(song.title+'\n'+song.artist).toLocaleLowerCase().includes(query)):ytLikedSongs;
 box.replaceChildren();updateYtLikedSelection();
 if(!songs.length){const empty=document.createElement('div');empty.className='details';empty.textContent=query?'No matching liked music':'No liked music';box.append(empty);return}
 for(const song of songs){
  const row=document.createElement('div');row.className='playlist-row';
  const selectWrap=document.createElement('label');selectWrap.className='playlist-select-wrap';
  const select=document.createElement('input');select.type='checkbox';select.className='playlist-select';select.checked=ytLikedSelected.has(song.videoId);select.setAttribute('aria-label','Select '+(song.title||'track'));
  select.onchange=()=>{select.checked?ytLikedSelected.add(song.videoId):ytLikedSelected.delete(song.videoId);updateYtLikedSelection()};selectWrap.append(select);
  const info=document.createElement('button');info.type='button';info.className='item playlist-open';info.setAttribute('aria-label','Play '+(song.title||'track'));
  const name=document.createElement('span');name.className='name';name.textContent=song.title||song.videoId;
  const meta=document.createElement('span');meta.className='meta';meta.textContent=song.artist||'';info.append(name,meta);
  info.onclick=async()=>{try{await post('/api/ytmusic/play-item',song)}catch(e){note(e.message,true)}};
  row.append(info,selectWrap);box.append(row)
 }
}
async function loadYtLiked(){const data=await api('/api/ytmusic/liked');ytLikedSongs=Array.isArray(data.songs)?data.songs:[];const ids=new Set(ytLikedSongs.map(song=>song.videoId));ytLikedSelected=new Set([...ytLikedSelected].filter(id=>ids.has(id)));renderYtLiked();await refreshYtLike()}
$('#ytmusic-liked-search').addEventListener('input',renderYtLiked);
$('#ytmusic-remove-selected').onclick=async()=>{try{await removeYtLiked(ytLikedSongs.filter(song=>ytLikedSelected.has(song.videoId)),$('#ytmusic-remove-selected'))}catch(e){note(e.message,true)}};
$('#ytmusic-quickpicks').onclick=async()=>{try{await post('/api/ytmusic/quickpicks',{})}catch(e){console.error(e)}};
$('#ytmusic-refresh-cookie').onclick=async()=>{const button=$('#ytmusic-refresh-cookie');try{await post('/api/ytmusic/refresh-cookie-begin',{});button.disabled=true;button.classList.add('busy');note('YouTube Music opened; capturing signed-in request…');const until=Date.now()+125000;while(Date.now()<until){await new Promise(resolve=>setTimeout(resolve,1000));const result=await api('/api/ytmusic/refresh-cookie-status');if(result.status==='saved'){note('YouTube Music cookie refreshed');return}if(result.status==='failed')throw Error(result.error||'Cookie refresh failed')}throw Error('No YouTube Music request was captured')}catch(e){note(e.message,true)}finally{button.disabled=false;button.classList.remove('busy')}};

async function refreshYtLike(){const button=$('#ytmusic-like');try{const status=await api('/api/ytmusic/current-status');button.textContent=status.liked?'Unlike':'Like';button.dataset.liked=status.liked?'1':'0';button.title=status.title||'';return status}catch(error){button.title=error.message||String(error);return null}}
$('#ytmusic-like').onclick=async()=>{const button=$('#ytmusic-like');try{const result=await post('/api/ytmusic/rate-current',{rating:'TOGGLE'});button.textContent=result.liked?'Unlike':'Like';button.dataset.liked=result.liked?'1':'0';if(result.liked){if(!ytLikedSongs.some(song=>song.videoId===result.videoId))ytLikedSongs.unshift({videoId:result.videoId,title:result.title,artist:''})}else ytLikedSongs=ytLikedSongs.filter(song=>song.videoId!==result.videoId);renderYtLiked()}catch(e){note(e.message,true)}};
$('#ytmusic-liked').onclick=async()=>{show('ytmusic-liked-page');try{await loadYtLiked()}catch(e){show('profiles');note(e.message,true)}};
$('#ytmusic-liked-back').onclick=()=>show('profiles');
refreshYtLike();setInterval(refreshYtLike,2000);
$('#open-audio-options').onclick=()=>show('audio-options-page');
$('#open-pc').onclick=async()=>{try{show('pc');await refreshStatic()}catch(error){note(error.message,true)}};
$('#away-display-toggle').onclick=async()=>{try{await busy($('#away-display-toggle'),()=>post('/api/away-display'),'Switching away/display…','Away/display state changed',true);await refreshVolatile()}catch(error){note(error.message,true)}};
$('#pc-back').onclick=()=>show('profiles');
$('#all-songs').onclick=async()=>{try{all=(await api('/api/songs')).songs;show('songs');render('#song-list',all,'');}catch(error){note(error.message,true)}};
$('#song-search').oninput=()=>render('#song-list',all,$('#song-search').value);
$('#playlist-search').oninput=()=>render('#playlist-songs',inside,$('#playlist-search').value);
document.querySelectorAll('[data-back]').forEach(button=>button.onclick=()=>show(button.dataset.back));
document.querySelectorAll('[data-cmd]').forEach(button=>button.onclick=async()=>{try{await post('/api/player/command',{command:button.dataset.cmd});await pollLocal()}catch(error){console.error('Player command failed',error)}});
$('#repeat').onclick=async()=>{try{const data=await api('/api/player');await post('/api/player/repeat',{mode:{off:'all',all:'one',one:'off'}[data.repeat]||'off'});await pollLocal()}catch(error){console.error(error)}};
$('#shuffle').onclick=async()=>{try{await post('/api/player/shuffle')}catch(error){note(error.message,true)}};
for(const [id,command] of [['system-previous','previous'],['system-toggle','toggle'],['system-next','next']])$('#'+id).onclick=async()=>{if(command!=='toggle'){const media=state.system;media.skipPending=true;media.skipFrom=media.trackKey;media.skipStarted=performance.now();media.skipWarned=false;resetSystemPosition()}else{state.system.skipPending=false}try{await post('/api/system-media',{command});await pollSystem(true)}catch(error){if(command!=='toggle')systemPositionError('System media skip failed: '+(error?.message||String(error)));else console.error('System media toggle failed',error)}};
$('#audio-toggle').onclick=async()=>{try{await post('/api/audio-toggle');await refreshStatic()}catch(error){note(error.message,true)}};
$('#restart-sonobus').onclick=()=>busy($('#restart-sonobus'),()=>post('/api/restart-sonobus'),'Restarting SonoBus…','SonoBus restarted',true).then(refreshVolatile).catch(error=>note(error.message,true));
$('#restart-airplay').onclick=()=>busy($('#restart-airplay'),()=>post('/api/restart-airplay'),'Restarting AirPlay…','AirPlay restarted',true).then(refreshVolatile).catch(error=>note(error.message,true));
$('#restart-vnc').onclick=()=>busy(
  $('#restart-vnc'),
  async()=>{const result=await post('/api/restart-vnc');renderVncState(result);return result},
  'Toggling VNC…','VNC state updated',true
).catch(error=>{note(error.message,true);refreshVncState()});
$('#profile-search').oninput=()=>openFilterDirectory(filterDirectoryPath).catch(error=>note(error.message,true));
bindSeek(state.local,'/api/player/seek');bindSeek(state.system,'/api/system-media/seek');bindVolume(state.local,'/api/player/volume');bindVolume(state.system,'/api/system-volume');bindMpvGain();
let staticRefreshRunning=false,volatileRefreshRunning=false,profileSnapshot=null;
let filterDirectoryPath='';
async function openFilterDirectory(path=''){
  filterDirectoryPath=path;
  const data=await api('/api/filter-directory?path='+encodeURIComponent(path));
  renderFilterDirectory(data);
}
function renderFilterDirectory(directory){
  const query=$('#profile-search').value.trim().toLowerCase(),root=$('#profile-list');root.replaceChildren();
  const list=document.createElement('div');list.className='list';
  if(directory.path){
    const back=document.createElement('button');back.className='item';
    back.innerHTML='<span class="name">Back</span><span class="meta"></span>';
    back.children[1].textContent=directory.parent||'Listening profiles';
    back.onclick=()=>openFilterDirectory(directory.parent||'').catch(error=>note(error.message,true));list.append(back)
  }else{
    const bypass=document.createElement('button');bypass.className='item'+(profileSnapshot?.active==='__no_filter__'?' active':'');
    bypass.innerHTML='<span class="name">No filter (bypass CamillaDSP)</span>';
    bypass.onclick=()=>selectFilterProfile(bypass,'__no_filter__');list.append(bypass)
  }
  for(const entry of directory.entries||[]){
    if(query&&![entry.name,entry.label,entry.id].some(value=>String(value).toLowerCase().includes(query)))continue;
    const button=document.createElement('button');button.className='item'+(entry.type==='profile'&&entry.id===profileSnapshot?.active?' active':'');
    const title=document.createElement('span');title.className='name';title.textContent=entry.label;
    const detail=document.createElement('span');detail.className='meta';detail.textContent='';
    button.append(title,detail);
    button.onclick=()=>entry.type==='directory'?openFilterDirectory(entry.id).catch(error=>note(error.message,true)):selectFilterProfile(button,entry.id);
    list.append(button)
  }
  if(!list.children.length){const empty=document.createElement('div');empty.className='card details';empty.textContent=query?'No matches':'No folders or YAML files here';list.append(empty)}
  root.append(list)
}
async function selectFilterProfile(button,name){
  try{await post('/api/select',{profile:name});await refreshStatic();await openFilterDirectory(filterDirectoryPath)}catch(error){note(error.message,true)}
}
function renderProfileSnapshot(data){
  renderActiveProfile(data);
  if(!$('#listening-page').classList.contains('hidden'))openFilterDirectory(filterDirectoryPath).catch(error=>note(error.message,true));
}
function playlistAction(playlist,shuffle){
  const button=document.createElement('button');button.type='button';button.className='playlist-icon';
  button.setAttribute('aria-label',(shuffle?'Play saved shuffle of ':'Play ')+playlist.name);
  button.title=(shuffle?'Play saved shuffle':'Play playlist')+' · '+playlist.name;
  button.innerHTML=shuffle
  ? '<svg class="media-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7h3c5 0 7 10 12 10h3m-4-4 4 4-4 4M3 17h3c2 0 3-1 4-3m3-4c1-2 2-3 5-3h3m-4-4 4 4-4 4"/></svg>'
  : '<svg class="media-icon" viewBox="0 0 24 24" aria-hidden="true"><path class="icon-fill" d="M8 5.5 19 12 8 18.5z"/></svg>';
  button.onclick=()=>post('/api/play/playlist',{name:playlist.name,shuffle}).then(refreshVolatile).catch(error=>console.error(error));
  return button
}
function renderPlaylistSnapshot(playlists){
  const list=$('#playlists');list.replaceChildren();
  for(const playlist of playlists){
    const row=document.createElement('div');row.className='playlist-row';
    const open=document.createElement('button');open.className='item playlist-open';open.dataset.playlistName=playlist.name;open.innerHTML='<span class="name"></span><span class="meta"></span>';open.children[0].textContent=playlist.name;open.children[1].textContent=playlist.count+(playlist.count===1?' song':' songs');const wrap=document.createElement('div');wrap.className='item-cover';const copy=document.createElement('div');copy.className='copy';copy.append(...open.children);wrap.append(coverNode(playlist.cover),copy);open.append(wrap);
    open.onclick=async()=>{current=playlist.name;inside=(await api('/api/playlist?name='+encodeURIComponent(playlist.name))).songs;$('#playlist-title').textContent=playlist.name;show('playlist');render('#playlist-songs',inside,'')};
    const controls=document.createElement('div');controls.className='playlist-controls';
    const play=playlistAction(playlist,false),shuffle=playlistAction(playlist,true);
    controls.append(play,shuffle);row.append(open,controls);list.append(row)
  }
}
function updatePlaylistCover(player){if(!player?.playlist||!player.playing)return;for(const button of document.querySelectorAll('#playlists [data-playlist-name]')){if(button.dataset.playlistName!==player.playlist)continue;const old=button.querySelector('.item-cover .cover-art');if(!old)continue;const url=player.cover||'';if(url&&old.tagName==='IMG'&&old.getAttribute('src')===url)return;if(!url&&old.classList.contains('cover-fallback'))return;old.replaceWith(coverNode(url))}}
const safeButtons=[$('#system-safe-volume'),$('#local-safe-volume')];
const safeSliders=[$('#system-volume'),$('#volume')];
let currentSafePercent=null;
function safeMarkerPosition(slider,percent){
  const min=Number(slider.min||0),max=Number(slider.max||100),thumb=21;
  const ratio=Math.max(0,Math.min(1,(percent-min)/(max-min||1)));
  return thumb/2+Math.max(0,slider.clientWidth-thumb)*ratio;
}
function positionSafeMarkers(){if(!Number.isFinite(currentSafePercent))return;for(const slider of safeSliders)slider.style.setProperty('--safe-x',safeMarkerPosition(slider,currentSafePercent)+'px')}
function renderSafeVolume(info){const ok=!!(info&&info.available&&Number.isFinite(Number(info.percent)));const p=ok?Number(info.percent):0;currentSafePercent=ok?p:null;for(const slider of safeSliders){slider.classList.toggle('safe-mark',ok);if(ok)slider.style.setProperty('--safe-x',safeMarkerPosition(slider,p)+'px');else slider.style.removeProperty('--safe-x')}for(const button of safeButtons){button.classList.toggle('safe-hidden',!ok);if(ok){const attenuation=60*Math.log10(p/100);button.textContent='Set safe max '+p.toFixed(6)+'% (volume '+attenuation.toFixed(6)+' dB)'}}}
window.addEventListener('resize',positionSafeMarkers,{passive:true});
if(window.ResizeObserver)new ResizeObserver(positionSafeMarkers).observe(document.documentElement);
for(const b of safeButtons)b.onclick=async()=>{try{const r=await post('/api/safe-volume',{});if(!r.state)throw new Error('Safe max response omitted physical state');renderVolatile(r.state);note('Safe max '+Number(r.safeVolume.percent).toFixed(6)+'% applied; physical readback '+Number(r.physicalVolume).toFixed(2)+'%')}catch(e){note(e.message,true)}};
function renderVolatile(data){renderSafeVolume(data.safeVolume);
  const sonoAllowed=!!data.mode.sonobusWanted,airAllowed=!!data.mode.airplay;
  $('#restart-sonobus').disabled=!sonoAllowed;$('#restart-sonobus').setAttribute('aria-disabled',String(!sonoAllowed));
  $('#restart-airplay').disabled=!airAllowed;$('#restart-airplay').setAttribute('aria-disabled',String(!airAllowed));
  $('#audio-toggle').textContent=data.mode.mode==='stopped'?'Start audio':'Stop audio';
  if(data.awayDisplay){const button=$('#away-display-toggle');button.textContent=data.awayDisplay.displayOff?'Show lock screen':data.awayDisplay.away?'Unlock desktop':'Away and display off';button.setAttribute('aria-pressed',String(!!data.awayDisplay.active));button.classList.toggle('mode-active',!!data.awayDisplay.active)}
  if(data.profiles)renderActiveProfile(data.profiles);
  renderSonobusStatus(data.mode,data.groups);
  $('#source-title').textContent=({ipad_external:'External',laptop_external:'PC',ipad_ipad:'External',ipad_laptop:'PC',ipad_both:'Both',laptop_ipad:'External',laptop_laptop:'PC',laptop_both:'Both'})[data.mode.mode]||data.mode.label;$('#source-details').textContent='CamillaDSP '+(data.mode.engineRunning?'running':'stopped')+' • SonoBus '+(data.mode.sonobus?'on':'off')+' • AirPlay '+(data.mode.airplay?'on':'off')+(data.mode.localWanted?' • Output '+(data.mode.localOutputLabel||'Not selected'):'');
  document.querySelectorAll('[data-mode]').forEach(button=>button.classList.toggle('mode-active',button.dataset.mode===data.mode.mode));const localOutput=!!data.mode.localWanted;$('#open-output').disabled=!localOutput;$('#open-output').setAttribute('aria-disabled',String(!localOutput));$('#output-title').textContent=localOutput?(data.mode.localOutputLabel||'PC output not selected'):'Not used by current output';$('#output-details').textContent=localOutput?'':'Switch audio output to PC or Both before configuring a physical output.';
  updatePlaylistCover(data.player);
  const local=data.player,lm=state.local;$('#now-title').textContent=local.title||'Nothing playing';updateLocalNowArtwork(local.cover||'');$('#now-details').textContent=[local.artist,local.album].filter(Boolean).join(' • ')||(local.path||'');renderTimeline(lm,local.currentTime,local.duration);if(lm.volumePending===null&&document.activeElement!==lm.volume){displayMasterVolume(local.volume)}if(lm.mpvGainPending===null&&document.activeElement!==lm.mpvGain){const gain=Number(local.mpvGain);if(Number.isFinite(gain)){lm.mpvGain.value=String(gain);fill(lm.mpvGain,gain,100)}}$('#shuffle').disabled=!local.canShuffleQueue;$('#repeat').textContent='Repeat '+({off:'Off',all:'All',one:'1'}[local.repeat]||'Off');$('#repeat').classList.toggle('active',local.repeat!=='off');setPlayIcon($('#local-play-shape'),document.querySelector('[data-cmd="toggle"]'),local.playing)
}
async function refreshStatic(){if(staticRefreshRunning)return;staticRefreshRunning=true;try{const data=await api('/api/state');profileSnapshot=data.profiles;renderProfileSnapshot(data.profiles);renderPlaylistSnapshot(data.playlists);loadGroups(data);renderVolatile(data);renderSystem(data.systemMedia);return data}catch(error){note(error.message,true)}finally{staticRefreshRunning=false}}
async function refreshVolatile(){if(volatileRefreshRunning||document.hidden)return;volatileRefreshRunning=true;try{renderVolatile(await api('/api/volatile'));}catch(error){console.warn('volatile refresh failed',error)}finally{volatileRefreshRunning=false}}

let customSchema=null,customRowCount=0;
function customSofaPointRow(){
  const row=document.createElement('div');row.className='custom-hrir-row';row.dataset.row=String(++customRowCount);
  row.innerHTML='<div class="custom-fields"><label>Azimuth<input inputmode="text" data-field="azimuth" placeholder="20.5"></label><label>Elevation<input inputmode="text" data-field="elevation" placeholder="-15"></label><label>Audio at this point<select data-field="source"><option value="" selected disabled>Select audio source…</option><option value="average">Average left + right</option><option value="left">Left only</option><option value="right">Right only</option><option value="stereo">Stereo L→L, R→R</option><option value="swap">Stereo L→R, R→L</option></select></label><button class="action" type="button" data-action="remove-sofa-point">Remove SOFA point</button></div>';
  row.querySelector('[data-action="remove-sofa-point"]').onclick=()=>{if(document.querySelectorAll('.custom-hrir-row').length>1){row.remove();drawCustomSphere()}else note('At least one HRIR is required',true)};row.querySelectorAll('input,select').forEach(control=>control.addEventListener('input',drawCustomSphere));return row
}
async function openCustomFilterPage(){
  customSchema=await api('/api/custom-filter/schema');if(!customSchema.devices.length)throw new Error('No compatible device baselines found');
  const select=$('#custom-device');select.replaceChildren(selectPrompt('Select device…'),...customSchema.devices.map(d=>new Option(d.label,d.id)));select.value='';$('#custom-name').value='';
  const list=$('#custom-sofa-list');list.replaceChildren(customSofaPointRow());drawCustomSphere();$('#custom-ranges').textContent=`Available ranges • azimuth ${customSchema.azimuth.min} to ${customSchema.azimuth.max} • elevation ${customSchema.elevation.min} to ${customSchema.elevation.max}. Enter each coordinate as one decimal number. Filename 20,5 is entered as 20.5.`;show('custom-filter-page')
}
function customPoints(){return [...document.querySelectorAll('.custom-hrir-row')].map(row=>Object.fromEntries([...row.querySelectorAll('[data-field]')].map(input=>[input.dataset.field,input.value.trim()]))) }
function customDecimal(value){const text=String(value).trim().replace(',','.');return /^-?\d+(?:\.\d+)?$/.test(text)?Number(text):NaN}
function customPointStatus(point,index){const az=customDecimal(point.azimuth),el=customDecimal(point.elevation);if(!Number.isFinite(az)||!Number.isFinite(el))return {valid:false,message:`SOFA point ${index+1} has an incomplete or invalid coordinate.`};if(!['average','left','right','stereo','swap'].includes(point.source))return {valid:false,message:`SOFA point ${index+1} needs an audio routing selection.`};const exact=customSchema.coordinates.some(p=>p.azimuth===az&&p.elevation===el);if(exact)return {valid:true};const nearby=[...customSchema.coordinates].sort((a,b)=>(a.azimuth-az)**2+(a.elevation-el)**2-((b.azimuth-az)**2+(b.elevation-el)**2)).slice(0,5).map(p=>`az ${p.azimuth}° / el ${p.elevation}°`).join(', ');return {valid:false,message:`SOFA point ${index+1} does not exist at az ${az}° / el ${el}°. Nearby valid options: ${nearby}.`}}
function validateCustomPoints(){const points=customPoints();for(let i=0;i<points.length;i++){const status=customPointStatus(points[i],i);if(!status.valid)return status}return {valid:true}}
function drawCustomSphere(){
  const canvas=$('#custom-sphere');if(!canvas)return;const ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height,cx=w/2,cy=h/2-5,r=Math.min(w,h)*.37;ctx.clearRect(0,0,w,h);
  const glow=ctx.createRadialGradient(cx-r*.25,cy-r*.3,r*.08,cx,cy,r);glow.addColorStop(0,'#7059c955');glow.addColorStop(.7,'#291e5355');glow.addColorStop(1,'#07091022');ctx.fillStyle=glow;ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.fill();
  ctx.lineWidth=1;ctx.strokeStyle='#a891ff45';for(const scale of [.5,1]){ctx.beginPath();ctx.ellipse(cx,cy,r,r*.28*scale,0,0,Math.PI*2);ctx.stroke()}for(const angle of [-60,-30,30,60]){const x=r*Math.sin(angle*Math.PI/180);ctx.beginPath();ctx.ellipse(cx,cy,Math.abs(x),r,0,0,Math.PI*2);ctx.stroke()}ctx.strokeStyle='#d9d1ff88';ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.stroke();
  ctx.fillStyle='#d8d1ec';ctx.font='13px sans-serif';ctx.textAlign='center';ctx.fillText('FRONT',cx,cy+5);ctx.fillStyle='#8e88a0';ctx.fillText('L',cx-r-18,cy+5);ctx.fillText('R',cx+r+18,cy+5);ctx.fillText('TOP',cx,cy-r-12);
  const colors={average:'#35d6a0',left:'#3f8eff',right:'#ff7ab8',stereo:'#ffc857',swap:'#a56eff'};customPoints().forEach((p,index)=>{const az=customDecimal(p.azimuth),el=customDecimal(p.elevation);if(!Number.isFinite(az)||!Number.isFinite(el))return;const azr=az*Math.PI/180,elr=el*Math.PI/180,x=Math.sin(azr)*Math.cos(elr),y=-Math.sin(elr),z=Math.cos(azr)*Math.cos(elr),px=cx+x*r*.92,py=cy+y*r*.92,front=z>=0,size=front?9:6,color=colors[p.source]||colors.average;ctx.globalAlpha=front?1:.45;ctx.fillStyle=color;ctx.beginPath();ctx.arc(px,py,size,0,Math.PI*2);ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();ctx.font='bold 12px sans-serif';ctx.textAlign=px>cx?'left':'right';ctx.fillStyle='#fff';const side=ctx.textAlign==='left'?14:-14;ctx.fillText(`${index+1} • az ${az}° • el ${el}° • ${p.source||'average'}`,px+side,py-10);ctx.globalAlpha=1});
  ctx.font='12px sans-serif';ctx.textAlign='left';let lx=24;for(const [name,color] of Object.entries(colors)){ctx.fillStyle=color;ctx.beginPath();ctx.arc(lx,h-20,5,0,Math.PI*2);ctx.fill();ctx.fillStyle='#c8c2d4';ctx.fillText(name,lx+10,h-16);lx+=105}
}
$('#open-custom-filter').onclick=()=>openCustomFilterPage().catch(error=>note(error.message,true));
$('#add-custom-sofa-point').onclick=()=>{const status=validateCustomPoints();if(!status.valid)return note(status.message,true);$('#custom-sofa-list').append(customSofaPointRow());drawCustomSphere()};
$('#review-custom-filter').onclick=()=>{const device=customSchema.devices.find(d=>d.id===$('#custom-device').value);if(!device||!$('#custom-name').value.trim())return note('Choose a device and enter a filter name',true);const pointStatus=validateCustomPoints();if(!pointStatus.valid)return note(pointStatus.message,true);const options=$('#custom-mode-options');options.replaceChildren(...device.modes.map(mode=>{const label=document.createElement('label');label.className='mode-choice';label.innerHTML=`<input type="radio" name="custom-mode" value="${mode}" > ${mode[0].toUpperCase()+mode.slice(1)}`;return label}));$('#custom-mode-dialog').showModal()};
$('#cancel-custom-mode').onclick=()=>$('#custom-mode-dialog').close();
$('#apply-custom-filter').onclick=async()=>{try{const mode=document.querySelector('input[name="custom-mode"]:checked')?.value;if(!mode)throw new Error('Select a mode');const result=await post('/api/custom-filter/create',{device:$('#custom-device').value,name:$('#custom-name').value.trim(),mode,points:customPoints()});$('#custom-mode-dialog').close();note('Created '+result.profile);show('listening-page');await openFilterDirectory($('#custom-device').value+'/custom')}catch(error){if(error.message.startsWith('Custom filter already exists:')){$('#custom-mode-dialog').close();note('That name already exists for this device. Enter another name.',true);$('#custom-name').focus();$('#custom-name').select();return}note(error.message,true)}};

refreshAll=async()=>refreshStatic();
refreshStatic();refreshVncState();window.addEventListener('pageshow',event=>{if(event.persisted){refreshStatic();refreshVolatile();refreshVncState()}});document.addEventListener('visibilitychange',()=>{if(!document.hidden){renewVisibleOutput();refreshStatic();refreshVolatile()}});async function renewVisibleOutput(){if(!outputPreviewToken||$('#output-modal').classList.contains('hidden'))return;try{await post('/api/output-preview-renew',{token:outputPreviewToken})}catch(error){$('#output-modal').classList.add('hidden');pendingMode=null;outputTopology=null;outputPreviewToken=null;showOutputStep('card');showTaskFeedback('Output selection expired; reopen it. '+error.message,'error')}}setInterval(()=>{if(!document.hidden)renewVisibleOutput()},15000);setInterval(refreshVolatile,1000);setInterval(()=>{if(!document.hidden)refreshVncState()},3000);setInterval(()=>{if(!document.hidden)pollSystem()},1000);setInterval(tickSystem,250);
  </script>
 </body>
</html>
'''
