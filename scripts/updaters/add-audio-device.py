#!/usr/bin/env python3
"""Manage Raw, limiter-smoothed, and gain-smoothed H5 profiles."""
import ast, math, re, os, shutil, subprocess, sys, tempfile, termios, textwrap, tty
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
COMPENSATION_WAV=AUDIO_ROOT/'conversion/Beyerdynamic DT 990 Pro - B&K 5128 to SADIE H5 Approximate Compensation.wav'
SOFA_AVERAGE_WAV=AUDIO_ROOT/'sofa average/H5_2945_Weighted_Power_Average_UNNORMALIZED.wav'
MEASUREMENTS_DIR=AUDIO_ROOT/'measurements'
HPCFS = (
    {
        'file': 'Apple EarPods - B&K 5128 - HpCF - Raw.wav',
        'label': 'earpods',
        'include_smoothed': True,
        'type': 'in-ear',
    },
    {
        'file': 'CMF Buds Pro 2 - B&K 5128 - HpCF - Raw.wav',
        'label': 'buds pro 2',
        'include_smoothed': True,
        'type': 'in-ear',
    },
    {
        'file': 'HyperX Cloud III - B&K 5128 - HpCF - Raw.wav',
        'label': 'cloud iii',
        'include_smoothed': True,
        'type': 'over-ear',
    },
)
SAMPLE_RATE=96000
CAMILLA_RAW_PLAYBACK_DEVICE='camilladsp_peak_integer'
CAMILLA_SMOOTHED_PLAYBACK_DEVICE='camilladsp_peak_limited'
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
def chain_gain(paths,raw=False):
    """Raw: normalize 20 Hz-20 kHz; Gain: preserve full-band attenuation."""
    arrays=[read_fir(path,2) for path in paths]
    nfft=dense_response_nfft(*(len(x) for x in arrays))
    frequencies=np.fft.rfftfreq(nfft,1.0/SAMPLE_RATE)
    combined=np.ones((nfft//2+1,2),dtype=np.float64)
    for data in arrays:combined*=np.abs(np.fft.rfft(data,n=nfft,axis=0))
    if raw:
        combined=combined[(frequencies>=20.0)&(frequencies<=20000.0)]
        # Include the exact band edges, which need not coincide with FFT bins.
        edges=np.ones((2,2),dtype=np.float64)
        for data in arrays:
            kernel=np.exp(-2j*np.pi*np.outer([20.0,20000.0],np.arange(len(data)))/SAMPLE_RATE)
            edges*=np.abs(kernel@data)
        peak=max(float(np.max(combined)),float(np.max(edges)))
    else:peak=float(np.max(combined))
    if not np.isfinite(peak) or peak<=0.0:raise RuntimeError('Invalid complete-chain frequency response')
    peak_db=20.0*np.log10(peak)
    gain=-peak_db if raw else min(0.0,-peak_db)
    print(f'Complete chain {"20 Hz-20 kHz" if raw else "full-band"} peak {peak_db:.12f} dB; gain {gain:.12f} dB')
    return gain
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
def filterless_text():return '---\ntitle: "Filterless / Speakers"\ndescription: "Pass-through CamillaDSP profile with no filters"\n'+devices_text(CAMILLA_RAW_PLAYBACK_DEVICE)+'pipeline: []\n'
def profile_label(item):return str(item['label']).strip()
def conv_text(name,path,channel):
    return f'''  {name}:
    type: Conv
    parameters:
      type: Wav
      filename: {yaml_string(path)}
      channel: {channel}
'''
def gain_text(gain):return f'''  auto_gain:
    type: Gain
    parameters:
      gain: {gain:.12f}
      scale: dB
'''
def mono_mixer_text():return '''mixers:
  mono:
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
def channel_swap_mixer_text():return '''mixers:
  swap:
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
def make_profile(item,mode,hpcf_path,chain_paths,preamp_db):
    device_dir=CAMILLA_ROOT/profile_directory_name(profile_label(item));playback=CAMILLA_SMOOTHED_PLAYBACK_DEVICE if mode.startswith('limited') else CAMILLA_RAW_PLAYBACK_DEVICE;is360=mode in ('raw360','gain360','limited360');is360st=mode.endswith('360s');is360swap=mode.endswith('360x')
    text='---\ntitle: '+yaml_string(mode)+'\ndescription: "B&K 5128 correction'+('; H5 360 mono' if is360 else '; H5 360 stereo' if is360st else '; H5 360 swapped stereo' if is360swap else '; stereo')+'"\n'+devices_text(playback)
    if is360:text+=mono_mixer_text()
    elif is360swap:text+=channel_swap_mixer_text()
    text+='filters:\n'
    names=[]
    for prefix,path in chain_paths:
        text+=conv_text(prefix+'_left',path,0)+conv_text(prefix+'_right',path,1);names.append(prefix)
    if mode.startswith(('gain','raw')):text+=gain_text(preamp_db)
    text+='pipeline:\n'
    if is360:text+='  - type: Mixer\n    name: mono\n'
    elif is360swap:text+='  - type: Mixer\n    name: swap\n'
    for prefix in names:
        text+=f'  - type: Filter\n    channels: [0]\n    names: [{prefix}_left]\n  - type: Filter\n    channels: [1]\n    names: [{prefix}_right]\n'
    if mode.startswith(('gain','raw')):text+='  - type: Filter\n    channels: [0, 1]\n    names: [auto_gain]\n'
    return device_dir/(mode+'.yml'),text
def configured_path(item):
    path=Path(str(item.get('file',''))).expanduser();return (path if path.is_absolute() else HPCF_ANECHOIC/path).resolve()
def smoothed_path(source):
    source=Path(source);return HPCF_ANECHOIC/(source.stem.removesuffix(' - Raw')+source.suffix)
def selected_variants(item,source):
    is_raw=source.stem.endswith(' - Raw');variants=[(not is_raw,source)]
    if item.get('include_smoothed',False) and is_raw:
        sibling=smoothed_path(source)
        if sibling.is_file():variants.append((True,sibling))
        else:print(f'No matching smoothed HpCF found: {sibling}; continuing with Raw only.')
    return variants

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
        result.append({'file':wav_name,'label':name,'include_smoothed':True,'type':'in-ear'});configured.add(key(source))
    return result

def build_profile_candidates(all_measurements=False):
    if not HPCF_ANECHOIC.is_dir():raise SystemExit(f'Missing B&K 5128 HpCF folder: {HPCF_ANECHOIC}')
    if not SOFA_AVERAGE_WAV.is_file():raise SystemExit(f'Missing SOFA average WAV: {SOFA_AVERAGE_WAV}')
    devices=[];labels=set();directory_names=set();sources=set();generated=[]
    for item in (measurement_devices() if all_measurements else HPCFS):
        label=profile_label(item);source=configured_path(item);kind=str(item.get('type','')).strip().lower()
        if kind not in ('in-ear','over-ear'):raise SystemExit(f'{label}: type must be in-ear or over-ear')
        if not source.is_file():raise SystemExit(f'Missing Raw HpCF WAV: {source}')
        smooth=smoothed_path(source)
        if not smooth.is_file():raise SystemExit(f'Missing smoothed HpCF WAV: {smooth}')
        directory_name=profile_directory_name(label);directory_key=os.path.normcase(directory_name)
        if label.casefold() in labels or directory_key in directory_names or os.path.normcase(str(source)) in sources:raise SystemExit(f'Duplicate HpCF device or profile directory: {label} / {source}')
        labels.add(label.casefold());directory_names.add(directory_key);sources.add(os.path.normcase(str(source)));devices.append((item,source))
        common=[]
        if kind=='in-ear':
            if not COMPENSATION_WAV.is_file():raise SystemExit(f'Missing in-ear compensation WAV: {COMPENSATION_WAV}')
            common.append(('fixture',COMPENSATION_WAV))
        specs=[('raw',source,False),('gain',smooth,False),('limited',smooth,False),('raw360',source,True),('gain360',smooth,True),('limited360',smooth,True),('raw360s',source,True),('gain360s',smooth,True),('limited360s',smooth,True),('raw360x',source,True),('gain360x',smooth,True),('limited360x',smooth,True)]
        for mode,hpcf,is360 in specs:
            chain=([('sofa',SOFA_AVERAGE_WAV)] if is360 else [])+common+[('hpcf',hpcf)]
            gain=0.0 if mode.startswith('limited') else chain_gain([path for _,path in chain],raw=mode.startswith('raw'));generated.append(make_profile(item,mode,hpcf,chain,gain))
    standard_profiles={(path.parent.resolve(),path.stem):text for path,text in generated}
    rebuilt_custom=[]
    for item,_source in devices:
        custom=CAMILLA_ROOT/profile_directory_name(profile_label(item))/'custom'
        if custom.is_dir():
            for path in sorted(custom.glob('*.y*ml')):
                if path.is_file():rebuilt_custom.append((path,rebuild_custom_profile(path,path.read_text(encoding='utf-8'),standard_profiles)))
    return [(CAMILLA_ROOT/'00-filterless.yml',filterless_text()),*generated,*rebuilt_custom],devices

def custom_raw_gain(config,points):
    """Include the custom HRIR mix, with independent full-scale stereo inputs."""
    base=[]
    for name,definition in config['filters'].items():
        if definition.get('type')=='Conv' and not name.startswith('custom_hrir_') and name.endswith('_left'):
            base.append(read_fir(Path(definition['parameters']['filename']),2))
    hrir=[read_fir(filename,2) for filename,source in points]
    nfft=dense_response_nfft(*(len(data) for data in base+hrir))
    frequencies=np.fft.rfftfreq(nfft,1.0/SAMPLE_RATE)
    selected=(frequencies>=20.0)&(frequencies<=20000.0)
    def response(data):
        spectrum=np.fft.rfft(data,n=nfft,axis=0)[selected]
        kernel=np.exp(-2j*np.pi*np.outer([20.0,20000.0],np.arange(len(data)))/SAMPLE_RATE)
        return np.concatenate((spectrum,kernel@data),axis=0)
    baseline=np.ones((int(selected.sum())+2,2),dtype=np.complex128)
    for data in base:baseline*=response(data)
    transfer=np.zeros((len(baseline),2,2),dtype=np.complex128)
    for data,(_filename,source) in zip(hrir,points):
        h=response(data)/len(points)
        weights={'average':((0.5,0.5),(0.5,0.5)),
                 'left':((1.0,0.0),(1.0,0.0)),
                 'right':((0.0,1.0),(0.0,1.0)),
                 'stereo':((1.0,0.0),(0.0,1.0)),
                 'swap':((0.0,1.0),(1.0,0.0))}[source]
        for ear in (0,1):
            for channel in (0,1):transfer[:,ear,channel]+=h[:,ear]*weights[ear][channel]*baseline[:,channel]
    peak=float(np.max(np.sum(np.abs(transfer),axis=2)))
    if not np.isfinite(peak) or peak<=0.0:raise RuntimeError('Invalid custom Raw frequency response')
    return -20.0*np.log10(peak)
def rebuild_custom_profile(path,text,standard_profiles):
    """Rebase one generated custom HRIR profile onto its newly built baseline."""
    current=yaml.safe_load(text)
    if not isinstance(current,dict):raise RuntimeError(f'{path}: custom profile is not a YAML object')
    match=re.search(r'Custom binaural HRIR filter based on (limited|gain|raw)\.yml;',str(current.get('description','')))
    if not match:raise RuntimeError(f'{path}: cannot identify custom filter baseline')
    mode=match.group(1);baseline_text=standard_profiles.get((path.parent.parent.resolve(),mode))
    if baseline_text is None:raise RuntimeError(f'{path}: newly generated {mode}.yml baseline is unavailable')
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
    pipeline.append({'type':'Mixer','name':'custom_hrir_merge'});name=path.stem;config['title']=name;config['description']=f'Custom binaural HRIR filter based on {mode}.yml; {count} point(s)'
    if mode=='raw':
        new_filters['auto_gain']['parameters']['gain']=float(custom_raw_gain(config,points))
        pipeline[:]=[stage for stage in pipeline if not (stage.get('type')=='Filter' and stage.get('names')==['auto_gain'])]
        pipeline.append({'type':'Filter','channels':[0,1],'names':['auto_gain']})
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
    changed=[path for path,text in expected.items() if path not in before_yaml]
    removed_yaml=sorted(before_yaml-set(expected))
    removed_dirs=sorted(before_dirs-expected_device_dirs)
    return changed,removed_yaml,removed_dirs
def rebuild_all_filters(all_measurements=False):
    candidates,devices=build_profile_candidates(all_measurements)
    uses_limiter=any(f'device: "{CAMILLA_SMOOTHED_PLAYBACK_DEVICE}"' in text for path,text in candidates)
    if uses_limiter and not Path('/etc/camilladsp-peak-limiter-v11').is_file():raise SystemExit('Limited profiles require the installed NixOS peak-limiter-v11 module.')
    camilladsp=Path('/run/current-system/sw/bin/camilladsp')
    if not camilladsp.is_file():raise SystemExit(f'Missing CamillaDSP executable: {camilladsp}')
    with tempfile.TemporaryDirectory() as validation_name:
        for number,(path,text) in enumerate(candidates):
            candidate=Path(validation_name)/f'{number:04d}-{path.name}';candidate.write_text(text,encoding='utf-8')
            check=subprocess.run([str(camilladsp),'--check',str(candidate)],text=True,capture_output=True)
            if check.returncode:raise SystemExit(f'Invalid generated profile {path.name}: '+(check.stderr.strip() or check.stdout.strip()))
    existed={path for path,text in candidates if path.is_file()};changed,removed,removed_dirs=commit_profiles(candidates)
    for path,text in candidates:
        print(f'{"Replaced existing" if path in existed else "Created"}: {path}')
        match=re.search(r'  auto_gain:\n    type: Gain\n    parameters:\n      gain: ([^\n]+)',text)
        if match:print(f'{path.name}: applied calculated gain {match.group(1)} dB')
        elif f'device: "{CAMILLA_SMOOTHED_PLAYBACK_DEVICE}"' in text:print(f'{path.name}: NO static gain stage; routed through external ALSA lookahead limiter')
        elif f'device: "{CAMILLA_RAW_PLAYBACK_DEVICE}"' in text:print(f'{path.name}: limiter bypassed; routed directly to FLOAT-to-S32 loopback conversion')
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
def ask_smoothed(source):
    if not Path(source).stem.endswith(' - Raw'):print('Selected unlabelled file is the smoothed variant; adding it only.');return False
    enabled=choose('Search the main HpCF folder for the matching unlabelled smoothed file and add its filters?',('No','Yes'))==1
    if enabled:
        sibling=smoothed_path(source);print(f'Found smoothed HpCF: {sibling}' if sibling.is_file() else f'No matching smoothed HpCF found: {sibling}; continuing with Raw only.')
    return enabled
def load(text):
    node=next((n for n in ast.parse(text).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='HPCFS' for t in n.targets)),None)
    if node is None:raise RuntimeError('HPCFS tuple was not found in add-audio-device.py')
    entries=ast.literal_eval(node.value)
    if not isinstance(entries,tuple) or not all(isinstance(item,dict) for item in entries):raise RuntimeError('HPCFS must be a tuple of dictionaries')
    return node,list(entries)
def render(entries):
    lines=['HPCFS = (']
    for item in entries:lines+=['    {',f"        'file': {str(item['file'])!r},",f"        'label': {str(item['label'])!r},",f"        'include_smoothed': True,",f"        'type': {str(item.get('type','in-ear'))!r},",'    },']
    return '\n'.join([*lines,')'])
def replace_block(text,node,entries):
    lines=text.splitlines(keepends=True);lines[node.lineno-1:node.end_lineno]=[render(entries)+'\n'];result=''.join(lines);ast.parse(result);return result
def new_entry():
    value=ask('Raw HpCF WAV filename from hpcf anechoic, or absolute path');candidate=Path(value).expanduser();path=(candidate if candidate.is_absolute() else HPCF_ANECHOIC/candidate).resolve()
    if not path.is_file():raise RuntimeError(f'HpCF file does not exist: {path}')
    if path.suffix.casefold()!='.wav':raise RuntimeError('HpCF must be a WAV file')
    label=ask('Profile name');kind=('in-ear','over-ear')[choose('Device type',('In-ear','Over-ear'))]
    return {'file':path.name if path.parent==HPCF_ANECHOIC.resolve() else str(path),'label':label,'resolved':path,'include_smoothed':True,'type':kind}
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
    if CAMILLA_ROOT.is_dir():
        for custom in sorted(CAMILLA_ROOT.glob('*/custom')):
            if custom.is_dir():shutil.rmtree(custom);removed.append(custom)
    print(f"Removed {len(removed)} custom filter director{'y' if len(removed)==1 else 'ies'}.")
def clean_devices(text):
    node,entries=load(text);print('\nClean devices review');row(1,f'Configured devices to remove: {len(entries)}');row(2,f'Anechoic HpCF folder: {HPCF_ANECHOIC}');row(3,f'CamillaDSP YAML folder: {CAMILLA_ROOT}');row(4,'The filters folder is generated state; unconfigured device directories and YAMLs are removed')
    if choose('Remove every configured device and generated filter?',('No','Yes'))==0:print('No files changed.');return
    backup=save(text,replace_block(text,node,[]));rebuild_after_change([],backup);print(f'Removed {len(entries)} configured devices. Backup: {backup}')
def add(text):
    node,entries=load(text);entry=new_entry();trial=[*entries,{key:entry[key] for key in ('file','label','include_smoothed','type')}];validate_unique(trial)
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
    kind=('in-ear','over-ear')[choose('Device type',('In-ear','Over-ear'))];enabled=True;stored=path.name if path.parent==HPCF_ANECHOIC.resolve() else str(path);trial=list(entries);trial[index]={'file':stored,'label':label,'include_smoothed':enabled,'type':kind};validate_unique(trial)
    print('\nEdit review');row(1,f'HpCF: {path}');row(2,f'Profile name: {label}');row(3,f'Search/include unlabelled smoothed filters: {enabled}')
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
