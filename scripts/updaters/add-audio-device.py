#!/usr/bin/env python3
"""Manage limiter-backed H5 profiles and their safe master-volume limits."""
import ast, math, re, os, json, shutil, subprocess, sys, tempfile, termios, textwrap, tty
from datetime import datetime
from pathlib import Path
import numpy as np
import soundfile as sf
import yaml
HOME=Path.home()
CONTEXT_FILE=HOME/'.config/sway/scripts/audio-backends/00_context.py'
def load_context_paths():
    defaults={'UPDATERS':HOME/'.config/sway/scripts/updaters','AUDIO_DEVICE_BACKUPS':HOME/'.local/state/sway/audio-device-updater/backups'}
    if not CONTEXT_FILE.is_file():return defaults
    namespace={'Path':Path,'HOME':HOME};wanted=set(defaults)|{'HOME','SCRIPTS'}
    for statement in ast.parse(CONTEXT_FILE.read_text(encoding='utf-8'),str(CONTEXT_FILE)).body:
        if not isinstance(statement,ast.Assign):continue
        names=[t.id for t in statement.targets if isinstance(t,ast.Name)]
        if not any(name in wanted for name in names):continue
        try:exec(compile(ast.Module(body=[statement],type_ignores=[]),str(CONTEXT_FILE),'exec'),namespace,namespace)
        except Exception:continue
    return {name:Path(namespace.get(name,default)) for name,default in defaults.items()}
CONTEXT_PATHS=load_context_paths()
AUDIO_ROOT=HOME/'Documents/prefs/audio'
AUDIO_FILTERS=AUDIO_ROOT/'filters'
SCRIPT_DIR=CONTEXT_PATHS['UPDATERS']
CONFIG_SCRIPT=Path(__file__).resolve()
BACKUP_DIR=CONTEXT_PATHS['AUDIO_DEVICE_BACKUPS']
HPCF_ANECHOIC=AUDIO_ROOT/'b&k 5128 hpcfs'
COMPENSATION_WAV=AUDIO_ROOT/'other/Beyerdynamic DT 990 Pro - B&K 5128 to SADIE H5 Approximate Compensation.wav'
SOFA_AVERAGE_WAV=AUDIO_ROOT/'other/H5_2945_Open_Sphere_Timed.wav'
MEASUREMENTS_DIR=AUDIO_ROOT/'measurements'
COMBINED_FILTER_ROOT=HOME/'Downloads-S5/filters'
HPCFS = (
    {
        'file': 'Apple EarPods - B&K 5128 - HpCF - Raw.wav',
        'label': 'Apple Earpods',
        'type': 'in-ear',
    },
    {
        'file': 'CMF Buds Pro 2 - B&K 5128 - HpCF - Raw.wav',
        'label': 'CMF Buds Pro 2',
        'type': 'in-ear',
    },
    {
        'file': 'HyperX Cloud III - B&K 5128 - HpCF - Raw.wav',
        'label': 'HyperX Cloud III',
        'type': 'over-ear',
    },
)
SAMPLE_RATE=96000
CAMILLA_LIMITED_PLAYBACK_DEVICE='camilladsp_peak_limited'
CAMILLA_PLAYBACK_FORMAT='F32_LE'
CAMILLA_ROOT=AUDIO_FILTERS

def profile_directory_name(value):
    """Use the profile name verbatim as its filters directory name."""
    name=str(value)
    if not name or name in ('.','..') or name!=name.strip():
        raise RuntimeError('Profile name must be nonempty and cannot start or end with whitespace')
    if '/' in name or '\\' in name or any(ord(character)<32 or ord(character)==127 for character in name):
        raise RuntimeError('Profile name cannot contain path separators or control characters')
    return name
def yaml_string(value):return '"'+str(value).replace('\\','\\\\').replace('"','\\"')+'"'
def atomic_write(path,text):
    temporary=path.with_name('.'+path.name+'.new');temporary.write_text(text,encoding='utf-8');temporary.replace(path)
def dense_response_nfft(*lengths):
    minimum=max(65536,max(int(length) for length in lengths)*16);return 1<<(minimum-1).bit_length()
def read_fir(path,channels):
    data,rate=sf.read(path,always_2d=True,dtype='float64')
    if rate!=SAMPLE_RATE or data.shape[1]!=channels or not len(data):raise RuntimeError(f'{path}: expected nonempty {channels}-channel {SAMPLE_RATE} Hz FIR')
    if not np.isfinite(data).all():raise RuntimeError(f'{path}: non-finite FIR samples')
    return data
def chain_gain(paths):
    """Shared stereo gain: negative complete-chain peak within 20-20000 Hz."""
    arrays=[read_fir(path,2) for path in paths]
    nfft=dense_response_nfft(*(len(x) for x in arrays))
    frequency=np.fft.rfftfreq(nfft,1.0/SAMPLE_RATE)
    keep=(frequency>=20.0)&(frequency<=20000.0)
    combined=np.ones((int(keep.sum()),2),dtype=np.float64)
    edges=np.ones((2,2),dtype=np.float64)
    for data in arrays:
        combined*=np.abs(np.fft.rfft(data,n=nfft,axis=0)[keep])
        kernel=np.exp(-2j*np.pi*np.outer([20.0,20000.0],np.arange(len(data)))/SAMPLE_RATE)
        edges*=np.abs(kernel@data)
    peak=max(float(np.max(combined)),float(np.max(edges)))
    if not np.isfinite(peak) or peak<=0:raise RuntimeError('Invalid complete-chain audible response')
    gain=-20.0*np.log10(peak)
    print(f'Complete chain 20 Hz-20 kHz peak {-gain:.12f} dB; gain {gain:.12f} dB')
    return gain

def _matrix_convolve(left,right):
    size=len(left)+len(right)-1;nfft=1<<(size-1).bit_length()
    return np.fft.irfft(np.fft.rfft(left,nfft)*np.fft.rfft(right,nfft),nfft)[:size]
def _pad_matrix(matrix,length):
    if matrix.shape[2]==length:return matrix
    return np.pad(matrix,((0,0),(0,0),(0,length-matrix.shape[2])))
def _mix_transfer(transfer,mixer):
    channels=mixer.get('channels') or {};outputs=int(channels.get('out',0));mapping=mixer.get('mapping') or []
    if outputs<1:raise RuntimeError('Mixer has no output channels')
    result=np.zeros((outputs,transfer.shape[1],transfer.shape[2]),dtype=np.float64)
    for row in mapping:
        destination=int(row['dest'])
        for source in row.get('sources') or []:
            channel=int(source['channel']);gain=10.0**(float(source.get('gain',0.0))/20.0)
            result[destination]+=gain*transfer[channel]
    return result
def _filter_transfer(transfer,channels,names,filters):
    if len(names)==1:assignments=[(channel,names[0]) for channel in channels]
    elif len(names)==len(channels):assignments=list(zip(channels,names))
    else:raise RuntimeError('Filter stage channel/name count mismatch')
    result=transfer.copy()
    expanded=[]
    for channel,name in assignments:
        definition=filters.get(name) or {}
        if definition.get('type')!='Conv':raise RuntimeError(f'Unsupported combined-WAV filter type for {name}: {definition.get("type")}')
        parameters=definition.get('parameters') or {}
        if parameters.get('type')!='Wav':raise RuntimeError(f'{name}: only Wav convolution is supported')
        data=read_fir(Path(parameters['filename']),max(2,int(parameters.get('channel',0))+1))
        impulse=data[:,int(parameters.get('channel',0))]
        rows=[_matrix_convolve(result[channel,input_channel],impulse) for input_channel in range(result.shape[1])]
        expanded.append((channel,rows))
    length=max([result.shape[2],*[len(row) for _channel,rows in expanded for row in rows]])
    result=_pad_matrix(result,length)
    for channel,rows in expanded:
        for input_channel,row in enumerate(rows):result[channel,input_channel,:len(row)]=row;result[channel,input_channel,len(row):]=0.0
    return result
def profile_transfer_matrix(profile_text):
    config=yaml.safe_load(profile_text) or {};transfer=np.zeros((2,2,1),dtype=np.float64);transfer[0,0,0]=1.0;transfer[1,1,0]=1.0
    mixers=config.get('mixers') or {};filters=config.get('filters') or {}
    for stage in config.get('pipeline') or []:
        kind=stage.get('type')
        if kind=='Mixer':transfer=_mix_transfer(transfer,mixers[stage['name']])
        elif kind=='Filter':transfer=_filter_transfer(transfer,[int(x) for x in stage.get('channels') or []],list(stage.get('names') or []),filters)
        else:raise RuntimeError(f'Unsupported combined-WAV pipeline stage: {kind}')
    if transfer.shape[:2]!=(2,2):raise RuntimeError(f'Profile output is not stereo: {transfer.shape[:2]}')
    if not np.isfinite(transfer).all():raise RuntimeError('Combined transfer contains non-finite samples')
    # Sequential channel-specific stages may pad untouched paths with zeros.
    # Remove only frames that are exactly zero across the entire 2x2 matrix.
    used=np.flatnonzero(np.any(transfer!=0.0,axis=(0,1)))
    return transfer[:,:,:used[-1]+1] if len(used) else transfer[:,:,:1]
def stereo_audible_peak(stereo):
    """Highest left/right magnitude from 20 Hz through 20 kHz, including exact edges."""
    nfft=dense_response_nfft(len(stereo))
    frequency=np.fft.rfftfreq(nfft,1.0/SAMPLE_RATE);keep=(frequency>=20.0)&(frequency<=20000.0)
    peak=float(np.max(np.abs(np.fft.rfft(stereo,n=nfft,axis=0)[keep])))
    kernel=np.exp(-2j*np.pi*np.outer([20.0,20000.0],np.arange(len(stereo)))/SAMPLE_RATE)
    peak=max(peak,float(np.max(np.abs(kernel@stereo))))
    if not math.isfinite(peak) or peak<=0.0:raise RuntimeError('Invalid combined stereo FIR response')
    return peak

def normalize_stereo_to_zero_db(stereo):
    """Use one shared gain so the higher L/R audible response peak is exactly 0 dB."""
    original_peak=stereo_audible_peak(stereo);gain=1.0/original_peak;normalized=stereo*gain
    verified=stereo_audible_peak(normalized)
    if not math.isclose(verified,1.0,rel_tol=2e-12,abs_tol=2e-12):raise RuntimeError(f'Stereo normalization verification failed: {verified}')
    return normalized,original_peak,gain

def combined_wav_candidates(profile_candidates):
    exports={}
    for profile_path,profile_text in profile_candidates:
        relative=Path(profile_path).resolve().relative_to(CAMILLA_ROOT.resolve()).with_suffix('.wav')
        matrix=profile_transfer_matrix(profile_text)
        # Ordinary stereo export: add all paths feeding each CamillaDSP output.
        # For diagonal profiles this is simply the convolved left/right chain.
        stereo=np.column_stack((np.sum(matrix[0],axis=0),np.sum(matrix[1],axis=0)))
        stereo,original_peak,gain=normalize_stereo_to_zero_db(stereo)
        exports[relative]={'data':stereo,'originalPeakLinear':original_peak,'normalizationGainLinear':gain}
    return exports

def commit_combined_wavs(exports):
    root=COMBINED_FILTER_ROOT.resolve();root.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.combined-wavs-',dir=root.parent) as transaction:
        stage=Path(transaction)/'filters';stage.mkdir();manifest={'version':2,'sampleRate':SAMPLE_RATE,'channels':['left','right'],'normalization':'one shared gain to 0 dB highest L/R response from 20 Hz through 20 kHz','files':{}}
        for relative,export in sorted(exports.items(),key=lambda item:item[0].as_posix()):
            data=export['data'];destination=stage/relative;destination.parent.mkdir(parents=True,exist_ok=True);sf.write(destination,data,SAMPLE_RATE,subtype='DOUBLE')
            check,rate=sf.read(destination,always_2d=True,dtype='float64')
            if rate!=SAMPLE_RATE or not np.array_equal(check,data) or check.shape[1]!=2:raise RuntimeError(f'Combined stereo WAV verification failed: {relative}')
            response_peak=stereo_audible_peak(check)
            if not math.isclose(response_peak,1.0,rel_tol=2e-12,abs_tol=2e-12):raise RuntimeError(f'Written stereo WAV does not peak at 0 dB: {relative}: {response_peak}')
            original=export['originalPeakLinear'];gain=export['normalizationGainLinear']
            manifest['files'][relative.as_posix()]={'frames':len(data),'responsePeakLinear':response_peak,'responsePeakDb':20.0*math.log10(response_peak),'originalResponsePeakLinear':original,'originalResponsePeakDb':20.0*math.log10(original),'normalizationGainLinear':gain,'normalizationGainDb':20.0*math.log10(gain)}
        (stage/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        backup=Path(transaction)/'previous'
        try:
            if root.exists():root.rename(backup)
            stage.rename(root)
        except BaseException:
            if root.exists():shutil.rmtree(root)
            if backup.exists():backup.rename(root)
            raise
    return root


def devices_text(playback_device):
    return f'''devices:
  samplerate: {SAMPLE_RATE}
  chunksize: 1024
  capture:
    type: Alsa
    channels: 2
    device: "hw:Loopback,1,0"
    format: S32_LE
  playback:
    type: Alsa
    channels: 2
    device: "{playback_device}"
    format: {CAMILLA_PLAYBACK_FORMAT}
'''
def filterless_text():
    return '# safe-volume-percent: 100.000000\n# peak-db: 0.000000000000\n---\ntitle: "Filterless / Speakers"\ndescription: "Pass-through CamillaDSP profile with no filters"\n'+devices_text(CAMILLA_LIMITED_PLAYBACK_DEVICE)+'pipeline: []\n'
def profile_label(item):return str(item['label']).strip()
def conv_text(name,path,channel):
    return f'''  {name}:
    type: Conv
    parameters:
      type: Wav
      filename: {yaml_string(path)}
      channel: {channel}
'''
PROFILE_LAYOUTS = {
    'stereo': {'label':'Stereo', 'spatial':False, 'mono':False, 'swap_input':False},
    'mono': {'label':'Mono', 'spatial':False, 'mono':True, 'swap_input':False},
    '360-stereo': {'label':'360 Stereo', 'spatial':True, 'mono':False, 'swap_input':False},
    '360-inverse': {'label':'360 Inverse Stereo', 'spatial':True, 'mono':False, 'swap_input':True},
    '360-mono': {'label':'360 Mono', 'spatial':True, 'mono':True, 'swap_input':False},
}

def mono_mixer_definition():return '''  mono_input:
    channels:
      in: 2
      out: 2
    mapping:
      - dest: 0
        sources:
          - channel: 0
            gain: -6.020599913280
          - channel: 1
            gain: -6.020599913280
      - dest: 1
        sources:
          - channel: 0
            gain: -6.020599913280
          - channel: 1
            gain: -6.020599913280
'''
def channel_swap_mixer_definition():return '''  swap_channels:
    channels:
      in: 2
      out: 2
    mapping:
      - dest: 0
        sources:
          - channel: 1
            gain: 0.0
      - dest: 1
        sources:
          - channel: 0
            gain: 0.0
'''
def chain_gain_for_layout(chain_paths,layout):
    """Peak of the actual routed L/R FIR chain over 20-20000 Hz."""
    arrays=[(name,read_fir(path,2)) for name,path in chain_paths]
    nfft=dense_response_nfft(*(len(data) for _name,data in arrays))
    frequency=np.fft.rfftfreq(nfft,1.0/SAMPLE_RATE);keep=(frequency>=20.0)&(frequency<=20000.0);responses=[]
    for name,data in arrays:
        band=np.abs(np.fft.rfft(data,n=nfft,axis=0)[keep])
        kernel=np.exp(-2j*np.pi*np.outer([20.0,20000.0],np.arange(len(data)))/SAMPLE_RATE)
        responses.append((name,np.concatenate((band,np.abs(kernel@data)),axis=0)))
    output=np.ones((int(keep.sum())+2,2),dtype=np.float64)
    for name,response in responses:
        output*=response
    peak=float(np.max(output))
    if not np.isfinite(peak) or peak<=0.0:raise RuntimeError('Invalid routed complete-chain audible response')
    gain=-20.0*np.log10(peak);print(f"{layout['label']} complete route 20 Hz-20 kHz peak {-gain:.12f} dB; gain {gain:.12f} dB");return gain
def safe_volume_percent(peak_gain_db):
    """wpctl control equivalent of the old Raw CamillaDSP auto_gain."""
    # Preserve the exact old Raw auto_gain result. PipeWire applies the
    # wpctl channel-volume control cubically, so control^3 must equal the
    # old CamillaDSP linear gain 10^(dB/20): control=10^(dB/60).
    value=min(100.0,100.0*10.0**(float(peak_gain_db)/60.0))
    if not math.isfinite(value) or value<=0.0:raise RuntimeError('Invalid safe volume percentage')
    return value

def make_profile(item,layout_key,hpcf_path,chain_paths,peak_gain_db):
    if layout_key not in PROFILE_LAYOUTS:raise ValueError('Unsupported profile layout: '+layout_key)
    layout=PROFILE_LAYOUTS[layout_key];device_dir=CAMILLA_ROOT/profile_directory_name(profile_label(item))
    safe=safe_volume_percent(peak_gain_db)
    text='# safe-volume-percent: '+f'{safe:.6f}'+'\n# peak-db: '+f'{-peak_gain_db:.12f}'+'\n---\ntitle: '+yaml_string(layout['label'])+'\ndescription: '+yaml_string('B&K 5128 correction; '+layout['label']+'; external limiter safety net')+'\n'+devices_text(CAMILLA_LIMITED_PLAYBACK_DEVICE)
    mixers=[]
    if layout['mono']:mixers.append(mono_mixer_definition())
    if layout['swap_input']:mixers.append(channel_swap_mixer_definition())
    if mixers:text+='mixers:\n'+''.join(mixers)
    text+='filters:\n';names=[]
    for prefix,path in chain_paths:text+=conv_text(prefix+'_left',path,0)+conv_text(prefix+'_right',path,1);names.append(prefix)
    text+='pipeline:\n'
    if layout['mono']:text+='  - type: Mixer\n    name: mono_input\n'
    if layout['swap_input']:text+='  - type: Mixer\n    name: swap_channels\n'
    for prefix in names:text+=f'  - type: Filter\n    channels: [0]\n    names: [{prefix}_left]\n  - type: Filter\n    channels: [1]\n    names: [{prefix}_right]\n'
    return device_dir/(layout_key+'.yml'),text
def configured_path(item):
    path=Path(str(item.get('file',''))).expanduser();return (path if path.is_absolute() else HPCF_ANECHOIC/path).resolve()

def measurement_devices():
    if not MEASUREMENTS_DIR.is_dir():raise SystemExit(f'Missing measurement folder: {MEASUREMENTS_DIR}')
    csvs=sorted((p for p in MEASUREMENTS_DIR.iterdir() if p.is_file() and p.suffix.casefold()=='.csv'),key=lambda p:(p.name.casefold(),p.name))
    if not csvs:raise SystemExit(f'No CSV files found in {MEASUREMENTS_DIR}')
    result=[dict(item) for item in HPCFS]
    def key(path):return Path(path).stem.removesuffix(' - Raw').casefold()
    configured={key(configured_path(item)) for item in result};expected=set()
    for csv_path in csvs:
        name=re.sub(r'[\\/:*?"<>|]+','_',csv_path.stem).strip()
        if not name or name in ('.','..') or any(ord(c)<32 for c in name):raise SystemExit(f'Invalid measurement filename: {csv_path.name}')
        wav_name=name+' - HpCF - Raw.wav'
        if wav_name.casefold() in expected:raise SystemExit(f'Measurement output collision: {csv_path.name}')
        expected.add(wav_name.casefold());source=(HPCF_ANECHOIC/wav_name).resolve()
        if key(source) in configured:continue
        if not source.is_file():raise SystemExit(f'Missing CSV-derived Raw HpCF: {source}; run the HpCF builder first')
        result.append({'file':wav_name,'label':name,'type':'in-ear'});configured.add(key(source))
    return result

def build_profile_candidates(all_measurements=False):
    generated=[(CAMILLA_ROOT/'00-filterless.yml',filterless_text())]
    configured=list(measurement_devices() if all_measurements else HPCFS)
    if not configured:return generated,[]
    if not HPCF_ANECHOIC.is_dir():raise SystemExit(f'Missing B&K 5128 HpCF folder: {HPCF_ANECHOIC}')
    if not SOFA_AVERAGE_WAV.is_file():raise SystemExit(f'Missing SOFA average WAV: {SOFA_AVERAGE_WAV}')
    devices=[];labels=set();directory_names=set();sources=set()
    for item in configured:
        label=profile_label(item);source=configured_path(item);kind=str(item.get('type','')).strip().lower()
        if kind not in ('in-ear','over-ear'):raise SystemExit(f'{label}: type must be in-ear or over-ear')
        if not source.is_file():raise SystemExit(f'Missing Raw HpCF WAV: {source}')
        directory_name=profile_directory_name(label);directory_key=os.path.normcase(directory_name)
        if label.casefold() in labels or directory_key in directory_names or os.path.normcase(str(source)) in sources:raise SystemExit(f'Duplicate HpCF device or profile directory: {label} / {source}')
        labels.add(label.casefold());directory_names.add(directory_key);sources.add(os.path.normcase(str(source)));devices.append((item,source))
        common=[]
        if kind=='over-ear':
            if not COMPENSATION_WAV.is_file():raise SystemExit(f'Missing over-ear compensation WAV: {COMPENSATION_WAV}')
            common.append(('fixture',COMPENSATION_WAV))
        for layout_key,layout in PROFILE_LAYOUTS.items():
            chain=([('sofa',SOFA_AVERAGE_WAV)] if layout['spatial'] else [])+common+[('hpcf',source)]
            peak_gain_db=chain_gain_for_layout(chain,layout)
            generated.append(make_profile(item,layout_key,source,chain,peak_gain_db))
    standard_profiles={path.parent.resolve():text for path,text in generated if path.stem=='stereo'}
    rebuilt_custom=[]
    for item,_source in devices:
        custom=CAMILLA_ROOT/profile_directory_name(profile_label(item))/'custom'
        if custom.is_dir():
            for path in sorted(custom.glob('*.y*ml')):
                if path.is_file():rebuilt_custom.append((path,rebuild_custom_profile(path,path.read_text(encoding='utf-8'),standard_profiles)))
    return [*generated,*rebuilt_custom],devices

def rebuild_custom_profile(path,text,standard_profiles):
    """Rebase one generated custom HRIR profile onto its newly built baseline."""
    current=yaml.safe_load(text)
    if not isinstance(current,dict):raise RuntimeError(f'{path}: custom profile is not a YAML object')
    baseline_text=standard_profiles.get(path.parent.parent.resolve())
    if baseline_text is None:raise RuntimeError(f'{path}: newly generated stereo.yml baseline is unavailable')
    config=yaml.safe_load(baseline_text)
    if not isinstance(config,dict):raise RuntimeError(f'{path}: generated baseline is not a YAML object')
    filters=current.get('filters') or {};spread=((current.get('mixers') or {}).get('custom_hrir_spread') or {}).get('mapping')
    if not isinstance(spread,list) or len(spread)%2:raise RuntimeError(f'{path}: custom HRIR spread mapping is invalid')
    count=len(spread)//2
    if count<1:raise RuntimeError(f'{path}: custom filter has no HRIR points')
    points=[]
    for index in range(count):
        left=((filters.get(f'custom_hrir_{index}_left') or {}).get('parameters') or {})
        right=((filters.get(f'custom_hrir_{index}_right') or {}).get('parameters') or {})
        filename=left.get('filename')
        if not filename or right.get('filename')!=filename or left.get('channel')!=0 or right.get('channel')!=1:
            raise RuntimeError(f'{path}: HRIR point {index+1} convolution pair is invalid')
        sources=(spread[2*index] or {}).get('sources')
        if not isinstance(sources,list):raise RuntimeError(f'{path}: HRIR point {index+1} source mapping is invalid')
        left_channels=[row.get('channel') for row in sources if isinstance(row,dict)]
        right_sources=(spread[2*index+1] or {}).get('sources')
        if not isinstance(right_sources,list):raise RuntimeError(f'{path}: HRIR point {index+1} right source mapping is invalid')
        right_channels=[row.get('channel') for row in right_sources if isinstance(row,dict)]
        source=('average' if left_channels==[0,1] and right_channels==[0,1] else 'left' if left_channels==[0] and right_channels==[0] else 'right' if left_channels==[1] and right_channels==[1] else 'stereo' if left_channels==[0] and right_channels==[1] else 'swap' if left_channels==[1] and right_channels==[0] else None)
        if source is None:raise RuntimeError(f'{path}: HRIR point {index+1} source mapping is unsupported')
        points.append((Path(filename),source))
    new_filters=config.setdefault('filters',{});mixers=config.setdefault('mixers',{});pipeline=config.setdefault('pipeline',[]);mapping=[]
    for index,(filename,source) in enumerate(points):
        mono_sources={'average':[{'channel':0,'gain':-6.02059991328},{'channel':1,'gain':-6.02059991328}],'left':[{'channel':0,'gain':0.0}],'right':[{'channel':1,'gain':0.0}]}
        if source in mono_sources:left_sources=right_sources=mono_sources[source]
        elif source=='stereo':left_sources=[{'channel':0,'gain':0.0}];right_sources=[{'channel':1,'gain':0.0}]
        else:left_sources=[{'channel':1,'gain':0.0}];right_sources=[{'channel':0,'gain':0.0}]
        mapping.extend(({'dest':2*index,'sources':left_sources},{'dest':2*index+1,'sources':right_sources}))
        new_filters[f'custom_hrir_{index}_left']={'type':'Conv','parameters':{'type':'Wav','filename':str(filename),'channel':0}}
        new_filters[f'custom_hrir_{index}_right']={'type':'Conv','parameters':{'type':'Wav','filename':str(filename),'channel':1}}
    mixers['custom_hrir_spread']={'channels':{'in':2,'out':2*count},'mapping':mapping};gain=-20*math.log10(count)
    mixers['custom_hrir_merge']={'channels':{'in':2*count,'out':2},'mapping':[{'dest':ear,'sources':[{'channel':2*i+ear,'gain':gain} for i in range(count)]} for ear in (0,1)]}
    pipeline.append({'type':'Mixer','name':'custom_hrir_spread'})
    for i in range(count):pipeline.extend(({'type':'Filter','channels':[2*i],'names':[f'custom_hrir_{i}_left']},{'type':'Filter','channels':[2*i+1],'names':[f'custom_hrir_{i}_right']}))
    pipeline.append({'type':'Mixer','name':'custom_hrir_merge'});name=path.stem;config['title']=name;config['description']=f'Custom binaural HRIR filter based on stereo.yml; {count} point(s)'
    return yaml.safe_dump(config,sort_keys=False)

def commit_profiles(candidates):
    """Atomically replace the updater-owned filter tree and remove stale devices."""
    CAMILLA_ROOT.mkdir(parents=True,exist_ok=True);expected={}
    root=CAMILLA_ROOT.resolve()
    for path,text in candidates:
        path=Path(path).resolve()
        if path.parent!=root and root not in path.parents:
            raise RuntimeError(f'Generated profile escapes output folder: {path}')
        if path in expected:raise RuntimeError(f'Duplicate generated profile path: {path.relative_to(root)}')
        expected[path]=text
    # Verification and stale-device cleanup operate on top-level device
    # directories. Preserved custom filters live below DEVICE/custom, so using
    # each YAML's immediate parent would incorrectly treat custom as a device.
    expected_device_dirs={root/path.relative_to(root).parts[0] for path in expected if path.parent!=root}
    before_yaml={path.resolve() for pattern in ('*.yml','*.yaml') for path in root.rglob(pattern) if path.is_file()}
    before_dirs={path.resolve() for path in root.iterdir() if path.is_dir()}
    with tempfile.TemporaryDirectory(prefix='.profile-commit-',dir=root.parent) as transaction_name:
        backup=Path(transaction_name)/'filters-backup'
        shutil.copytree(root,backup,symlinks=True)
        try:
            for path,text in expected.items():
                path.parent.mkdir(parents=True,exist_ok=True);atomic_write(path,text)
            # The filters directory is generated state. A device is configured
            # only when its slugged top-level directory is expected now.
            for directory in sorted(before_dirs-expected_device_dirs,key=lambda item:len(item.parts),reverse=True):
                if directory.exists():shutil.rmtree(directory)
            for old in before_yaml-set(expected):
                if old.exists():old.unlink()
            # Remove empty nested remnants but never remove an expected device dir.
            for directory in sorted((x for x in root.rglob('*') if x.is_dir()),key=lambda item:len(item.parts),reverse=True):
                if directory not in expected_device_dirs:
                    try:directory.rmdir()
                    except OSError:pass
            actual={path.resolve() for pattern in ('*.yml','*.yaml') for path in root.rglob(pattern) if path.is_file()}
            if actual!=set(expected):
                extra=sorted(str(path.relative_to(root)) for path in actual-set(expected))
                missing=sorted(str(path.relative_to(root)) for path in set(expected)-actual)
                raise RuntimeError(f'Profile verification mismatch; extra={extra}, missing={missing}')
            actual_device_dirs={path.resolve() for path in root.iterdir() if path.is_dir()}
            if actual_device_dirs!=expected_device_dirs:
                extra=sorted(path.name for path in actual_device_dirs-expected_device_dirs)
                missing=sorted(path.name for path in expected_device_dirs-actual_device_dirs)
                raise RuntimeError(f'Device directory verification mismatch; extra={extra}, missing={missing}')
            for path,text in expected.items():
                if path.read_text(encoding='utf-8')!=text:raise RuntimeError(f'Profile verification failed: {path}')
        except BaseException as error:
            restore_errors=[]
            try:
                shutil.rmtree(root)
                shutil.copytree(backup,root,symlinks=True)
            except OSError as restore_error:restore_errors.append(str(restore_error))
            if restore_errors:
                raise RuntimeError('Profile commit failed and complete tree rollback failed: '+'; '.join(restore_errors)) from error
            raise RuntimeError('Profile commit failed; the complete previous filter tree was restored: '+str(error)) from error
    safe_index={}
    for profile_path,profile_text in expected.items():
        match=re.search(r'(?m)^# safe-volume-percent: ([0-9]+(?:\.[0-9]+)?)$',profile_text)
        peak=re.search(r'(?m)^# peak-db: (-?[0-9]+(?:\.[0-9]+)?)$',profile_text)
        if match:
            key=profile_path.relative_to(root).as_posix()
            safe_index[key]={'percent':float(match.group(1)),'peakDb':float(peak.group(1)) if peak else 0.0}
    index_path=root/'.safe-volume.json';index_temp=root/'.safe-volume.json.new';previous_index=index_path.read_bytes() if index_path.is_file() else None
    try:
        index_temp.write_text(json.dumps({'version':1,'profiles':safe_index},indent=2,sort_keys=True)+'\n',encoding='utf-8')
        index_temp.replace(index_path)
    except BaseException:
        index_temp.unlink(missing_ok=True)
        if previous_index is None:index_path.unlink(missing_ok=True)
        else:index_path.write_bytes(previous_index)
        raise
    if json.loads(index_path.read_text(encoding='utf-8')).get('profiles')!=safe_index:
        raise RuntimeError('Safe-volume index verification failed')
    changed=[path for path,text in expected.items() if path not in before_yaml]
    removed_yaml=sorted(before_yaml-set(expected))
    removed_dirs=sorted(before_dirs-expected_device_dirs)
    return changed,removed_yaml,removed_dirs
def rebuild_all_filters(all_measurements=False):
    print(f'Writing profiles and safe-volume index to: {CAMILLA_ROOT}')
    candidates,devices=build_profile_candidates(all_measurements)
    if not Path('/etc/camilladsp-peak-limiter-v11').is_file():raise SystemExit('Generated profiles require the installed NixOS peak-limiter-v11 module.')
    camilladsp=Path('/run/current-system/sw/bin/camilladsp')
    if not camilladsp.is_file():raise SystemExit(f'Missing CamillaDSP executable: {camilladsp}')
    with tempfile.TemporaryDirectory() as validation_name:
        for number,(path,text) in enumerate(candidates):
            candidate=Path(validation_name)/f'{number:04d}-{path.name}';candidate.write_text(text,encoding='utf-8')
            check=subprocess.run([str(camilladsp),'--check',str(candidate)],text=True,capture_output=True)
            if check.returncode:raise SystemExit(f'Invalid generated profile {path.name}: '+(check.stderr.strip() or check.stdout.strip()))
    exports=combined_wav_candidates(candidates)
    existed={path for path,text in candidates if path.is_file()};changed,removed,removed_dirs=commit_profiles(candidates)
    combined_root=commit_combined_wavs(exports)
    for path,text in candidates:
        print(f'{"Replaced existing" if path in existed else "Created"}: {path}')
        match=re.search(r'(?m)^# safe-volume-percent: ([0-9]+(?:\.[0-9]+)?)$',text)
        if match:print(f"{path.name}: safe master {float(match.group(1)):.6f}% before limiter activity")
    print(f'Exported {len(exports)} 0 dB-normalized stereo FIR WAVs to {combined_root}')
    print(f'Using {len(devices)} anechoic HpCF devices; verified {len(candidates)} CamillaDSP YAML profiles in {CAMILLA_ROOT}; {len(changed)} created, {len(removed)} obsolete YAMLs removed, {len(removed_dirs)} unconfigured device directories removed')
TERMINALS=(('foot','-T','Manage Audio Device'),('kitty','--title','Manage Audio Device'),('alacritty','--title','Manage Audio Device','-e'))
def row(number,label):
    prefix=f'[{number}] ';width=max(1,shutil.get_terminal_size((80,24)).columns-len(prefix));lines=textwrap.wrap(str(label),width=width,break_long_words=True,break_on_hyphens=False) or ['']
    print(prefix+lines[0])
    for line in lines[1:]:print(' '*len(prefix)+line)
def choose(title,choices):
    print(f'\n{title}');row(0,'Exit without saving')
    for number,label in enumerate(choices,1):row(number,label)
    while True:
        answer=input('Select:').strip()
        if answer=='0':raise KeyboardInterrupt
        if answer.isdecimal() and 1<=int(answer)<=len(choices):return int(answer)-1
        print('Select an available number.')
def ask(label,default=''):
    suffix=f' [{default}]' if default else ''
    while True:
        value=input(f'{label}{suffix} (0 to exit): ').strip()
        if value=='0':raise KeyboardInterrupt
        value=value or default
        if value and '\n' not in value and '\r' not in value:return value
        print(f'{label} is required.')
def ask_edit(field,current):return choose(f'{field} current: {current}. Edit?',('No','Yes'))==1
def load(text):
    node=next((n for n in ast.parse(text).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='HPCFS' for t in n.targets)),None)
    if node is None:raise RuntimeError('HPCFS tuple was not found in add-audio-device.py')
    entries=ast.literal_eval(node.value)
    if not isinstance(entries,tuple) or not all(isinstance(item,dict) for item in entries):raise RuntimeError('HPCFS must be a tuple of dictionaries')
    return node,list(entries)
def render(entries):
    lines=['HPCFS = (']
    for item in entries:lines+=['    {',f"        'file': {str(item['file'])!r},",f"        'label': {str(item['label'])!r},",f"        'type': {str(item.get('type','in-ear'))!r},",'    },']
    return '\n'.join([*lines,')'])
def replace_block(text,node,entries):
    lines=text.splitlines(keepends=True);lines[node.lineno-1:node.end_lineno]=[render(entries)+'\n'];result=''.join(lines);ast.parse(result);return result
def new_entry():
    value=ask('Raw HpCF WAV filename from hpcf anechoic, or absolute path');candidate=Path(value).expanduser();path=(candidate if candidate.is_absolute() else HPCF_ANECHOIC/candidate).resolve()
    if not path.is_file():raise RuntimeError(f'HpCF file does not exist: {path}')
    if path.suffix.casefold()!='.wav':raise RuntimeError('HpCF must be a WAV file')
    label=ask('Profile name');kind=('in-ear','over-ear')[choose('Device type',('In-ear','Over-ear'))]
    return {'file':path.name if path.parent==HPCF_ANECHOIC.resolve() else str(path),'label':label,'resolved':path,'type':kind}
def validate_unique(entries,skip=None):
    labels=set();paths=set()
    for index,item in enumerate(entries):
        if index==skip:continue
        label=str(item.get('label','')).strip().casefold();path=os.path.normcase(os.path.realpath(configured_path(item)))
        if label in labels:raise RuntimeError(f'Duplicate device label: {item.get("label")}')
        if path in paths:raise RuntimeError(f'Duplicate HpCF file: {configured_path(item)}')
        labels.add(label);paths.add(path)
def save(before,after):
    with tempfile.TemporaryDirectory() as directory:
        candidate=Path(directory)/CONFIG_SCRIPT.name;candidate.write_text(after,encoding='utf-8');subprocess.run([sys.executable,'-m','py_compile',str(candidate)],check=True)
    backup=BACKUP_DIR/datetime.now().strftime('%Y%m%d-%H%M%S-%f');backup.mkdir(parents=True,exist_ok=False);shutil.copy2(CONFIG_SCRIPT,backup/CONFIG_SCRIPT.name);temporary=CONFIG_SCRIPT.with_name('.'+CONFIG_SCRIPT.name+'.new')
    try:
        temporary.write_text(after,encoding='utf-8');os.chmod(temporary,CONFIG_SCRIPT.stat().st_mode);os.replace(temporary,CONFIG_SCRIPT);subprocess.run([sys.executable,'-m','py_compile',str(CONFIG_SCRIPT)],check=True)
    except Exception:temporary.unlink(missing_ok=True);shutil.copy2(backup/CONFIG_SCRIPT.name,CONFIG_SCRIPT);raise
    return backup
def rebuild_after_change(entries,backup):
    global HPCFS
    previous=HPCFS;HPCFS=tuple(dict(item) for item in entries)
    try:rebuild_all_filters()
    except BaseException as error:
        HPCFS=previous;shutil.copy2(backup/CONFIG_SCRIPT.name,CONFIG_SCRIPT);subprocess.run([sys.executable,'-m','py_compile',str(CONFIG_SCRIPT)],check=True)
        raise RuntimeError('Filter update failed; device configuration restored from backup. Cause: '+(str(error).strip() or error.__class__.__name__)) from error
    print('All CamillaDSP filters updated.')
def update_filters(_text=None):print('\nUpdating all CamillaDSP filters...');rebuild_all_filters();print('All CamillaDSP filters updated.')
def clean_custom_filters(_text=None):
    removed=[]
    if COMBINED_FILTER_ROOT.is_dir():
        for custom in sorted(COMBINED_FILTER_ROOT.glob('*/custom')):
            if custom.is_dir():shutil.rmtree(custom);removed.append(custom)
    if CAMILLA_ROOT.is_dir():
        for custom in sorted(CAMILLA_ROOT.glob('*/custom')):
            if custom.is_dir():shutil.rmtree(custom);removed.append(custom)
    print(f"Removed {len(removed)} custom filter director{'y' if len(removed)==1 else 'ies'}.")
def clean_devices(text):
    node,entries=load(text);print('\nClean devices review');row(1,f'Configured devices to remove: {len(entries)}');row(2,f'Anechoic HpCF folder: {HPCF_ANECHOIC}');row(3,f'CamillaDSP YAML folder: {CAMILLA_ROOT}');row(4,'The filters folder is generated state; unconfigured device directories and YAMLs are removed')
    if choose('Remove every configured device and generated filter?',('No','Yes'))==0:print('No files changed.');return
    backup=save(text,replace_block(text,node,[]));rebuild_after_change([],backup);print(f'Removed {len(entries)} configured devices. Backup: {backup}')
def add(text):
    node,entries=load(text);entry=new_entry();trial=[*entries,{key:entry[key] for key in ('file','label','type')}];validate_unique(trial)
    print('\nAdd review');row(1,f"HpCF: {entry['resolved']}");row(2,f"Profile name: {entry['label']}");row(3,f"Type: {entry['type']}")
    if choose('Save this device?',('No','Yes'))==0:return
    backup=save(text,replace_block(text,node,trial));print(f"Added {entry['label']}. Backup: {backup}");rebuild_after_change(trial,backup)
def edit(text):
    node,entries=load(text)
    if not entries:raise RuntimeError('No configured audio devices to edit')
    index=choose('Edit audio device',[f"{x.get('label')} | {x.get('file')}" for x in entries]);current=entries[index];path=configured_path(current);label=str(current.get('label','')).strip()
    if ask_edit('HpCF',str(path)):
        candidate=Path(ask('HpCF WAV filename or absolute path',str(path))).expanduser();path=(candidate if candidate.is_absolute() else HPCF_ANECHOIC/candidate).resolve()
    if ask_edit('Profile name',label):label=ask('Profile name',label)
    if not path.is_file():raise RuntimeError(f'HpCF file does not exist: {path}')
    if path.suffix.casefold()!='.wav':raise RuntimeError('HpCF must be a WAV file')
    kind=('in-ear','over-ear')[choose('Device type',('In-ear','Over-ear'))];stored=path.name if path.parent==HPCF_ANECHOIC.resolve() else str(path);trial=list(entries);trial[index]={'file':stored,'label':label,'type':kind};validate_unique(trial)
    print('\nEdit review');row(1,f'HpCF: {path}');row(2,f'Profile name: {label}');row(3,f'Type: {kind}')
    if choose('Save these edits?',('No','Yes'))==0:print('No files changed.');return
    backup=save(text,replace_block(text,node,trial));print(f'Edited {label}. Backup: {backup}');rebuild_after_change(trial,backup)
def remove(text):
    node,entries=load(text)
    if not entries:raise RuntimeError('No configured audio devices to remove')
    index=choose('Remove audio device',[f"{x.get('label')} | {x.get('file')}" for x in entries]);removed=entries.pop(index);print('\nRemove review');row(1,f'HpCF: {configured_path(removed)}');row(2,f"Profile name: {removed.get('label')}")
    if choose('Remove this device?',('No','Yes'))==0:return
    backup=save(text,replace_block(text,node,entries));print(f"Removed {removed.get('label')}. Backup: {backup}");rebuild_after_change(entries,backup)
def relaunch():
    for command in TERMINALS:
        executable=shutil.which(command[0])
        if executable:subprocess.Popen([executable,*command[1:],sys.executable,str(Path(__file__).resolve())]);return
    raise RuntimeError('No supported terminal found: foot, kitty or alacritty')
def pause():
    print('\nPress any key to close...',end='',flush=True)
    if sys.stdin.isatty():
        fd=sys.stdin.fileno();settings=termios.tcgetattr(fd)
        try:tty.setraw(fd);os.read(fd,1)
        finally:termios.tcsetattr(fd,termios.TCSADRAIN,settings)
    print()
def manager_main():
    text=CONFIG_SCRIPT.read_text(encoding='utf-8');actions=(add,edit,remove,clean_custom_filters,clean_devices,update_filters);choice=choose('Audio device manager',('Add device','Edit device','Remove device','Clean custom filters','Clean devices','Update all filters'));actions[choice](text)
if __name__=='__main__':
    if sys.argv[1:] in (['--rebuild'],['--rebuild-all']):rebuild_all_filters(all_measurements=sys.argv[1:]==['--rebuild-all']);raise SystemExit(0)
    if sys.argv[1:]:raise SystemExit('Usage: add-audio-device.py [--rebuild | --rebuild-all]')
    if not(sys.stdin.isatty() and sys.stdout.isatty()):relaunch();raise SystemExit(0)
    status=0
    try:manager_main()
    except KeyboardInterrupt:print('\nCancelled.');status=130
    except Exception as error:print(f'\nError: {error}',file=sys.stderr);status=1
    finally:shutil.rmtree(SCRIPT_DIR/'__pycache__',ignore_errors=True);pause()
    raise SystemExit(status)
