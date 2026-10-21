# Shared custom binaural filter builder using measured points directly from SOFA.
import yaml
SOFA_PATH=HOME/'Documents/prefs/audio/P0135/HRTF/HRTF/96kHz/P0135_FreeFieldCompMinPhase_96kHz.sofa'
CUSTOM_FILTER_LOCK=HOME/'.local/state/sway/custom-audio-filter.lock'
SOFA_SAMPLE_RATE=96000

def _coord_value(value):
    text=str(value).strip().replace(',','.')
    if not re.fullmatch(r'-?\d+(?:\.\d+)?',text):raise ValueError('Coordinates must be ordinary numbers, for example 20.5 or -15')
    number=float(text)
    if not math.isfinite(number):raise ValueError('Coordinates must be finite')
    return format(number,'.10g')

def _sofa_text(value,default=''):
    import numpy as np
    if value is None:return default
    if isinstance(value,bytes):return value.decode('utf-8','replace')
    if isinstance(value,np.ndarray) and value.size==1:return _sofa_text(value.reshape(-1)[0],default)
    return str(value)

def _read_sofa():
    try:import numpy as np
    except ImportError as error:raise RuntimeError('Custom SOFA filters require Python numpy') from error
    if not SOFA_PATH.is_file():raise FileNotFoundError('SOFA file not found: '+str(SOFA_PATH))
    try:
        import h5py
        with h5py.File(SOFA_PATH,'r') as sofa:
            ir=np.asarray(sofa['Data.IR'],dtype=np.float64)
            rate=int(round(float(np.asarray(sofa['Data.SamplingRate']).reshape(-1)[0])))
            positions=np.asarray(sofa['SourcePosition'],dtype=np.float64)
            pds=sofa['SourcePosition'];ptype=_sofa_text(pds.attrs.get('Type',sofa.attrs.get('SourcePosition_Type')),'spherical').lower();units=_sofa_text(pds.attrs.get('Units',sofa.attrs.get('SourcePosition_Units')),'degree, degree, metre').lower()
            delay=np.asarray(sofa['Data.Delay'],dtype=np.float64) if 'Data.Delay' in sofa else None
            delay_units=_sofa_text(sofa['Data.Delay'].attrs.get('Units') if 'Data.Delay' in sofa else None,'second').lower()
            convention=_sofa_text(sofa.attrs.get('SOFAConventions'));room=_sofa_text(sofa.attrs.get('RoomType'))
    except ImportError:
        try:from netCDF4 import Dataset
        except ImportError as error:raise RuntimeError('Custom SOFA filters require Python h5py or netCDF4') from error
        with Dataset(SOFA_PATH) as sofa:
            ir=np.asarray(sofa.variables['Data.IR'][:],dtype=np.float64);rate=int(round(float(np.asarray(sofa.variables['Data.SamplingRate'][:]).reshape(-1)[0])));positions=np.asarray(sofa.variables['SourcePosition'][:],dtype=np.float64)
            pds=sofa.variables['SourcePosition'];ptype=_sofa_text(getattr(pds,'Type','spherical')).lower();units=_sofa_text(getattr(pds,'Units','degree, degree, metre')).lower()
            delay=np.asarray(sofa.variables['Data.Delay'][:],dtype=np.float64) if 'Data.Delay' in sofa.variables else None;delay_units=_sofa_text(getattr(sofa.variables.get('Data.Delay'),'Units','second')).lower() if delay is not None else 'second'
            convention=_sofa_text(getattr(sofa,'SOFAConventions',''));room=_sofa_text(getattr(sofa,'RoomType',''))
    if convention!='SimpleFreeFieldHRIR':raise RuntimeError('Custom points require SimpleFreeFieldHRIR SOFA, found '+convention)
    if room.lower()!='free field':raise RuntimeError('Custom points require a free-field SOFA, found '+room)
    if rate!=SOFA_SAMPLE_RATE:raise RuntimeError(f'Custom SOFA must be {SOFA_SAMPLE_RATE} Hz, found {rate}')
    if ir.ndim==4 and ir.shape[2]==1:ir=ir[:,:,0,:]
    if ir.ndim!=3 or ir.shape[1]!=2:raise RuntimeError(f'Expected Mx2xN Data.IR, found {ir.shape}')
    if positions.shape[0]!=ir.shape[0]:raise RuntimeError('SourcePosition and Data.IR count differ')
    if 'cartesian' in ptype:
        xyz=positions[:,:3];radius=np.linalg.norm(xyz,axis=1);az=np.degrees(np.arctan2(xyz[:,1],xyz[:,0]))%360;el=np.degrees(np.arcsin(np.divide(xyz[:,2],radius,out=np.zeros_like(radius),where=radius>0)))
    else:
        az=positions[:,0]%360;el=positions[:,1]
        if 'radian' in units:az=np.degrees(az)%360;el=np.degrees(el)
    if delay is None:delay_seconds=np.zeros((len(ir),2))
    else:
        delay=np.squeeze(delay)
        if delay.ndim==0:delay=np.full((len(ir),2),float(delay))
        elif delay.ndim==1:
            if len(delay)==2:delay=np.tile(delay,(len(ir),1))
            elif len(delay)==len(ir):delay=np.repeat(delay[:,None],2,axis=1)
        if delay.shape!=(len(ir),2):raise RuntimeError(f'Unsupported Data.Delay shape {delay.shape}')
        delay_seconds=delay/rate if 'sample' in delay_units else delay
    if not all(np.isfinite(value).all() for value in (ir,az,el,delay_seconds)):raise RuntimeError('SOFA contains non-finite data')
    return ir,rate,az,el,delay_seconds

def hrir_catalog():
    ir,rate,az,el,delay=_read_sofa();result={}
    for index,(a,e) in enumerate(zip(az,el)):
        key=(_coord_value(a),_coord_value(e))
        if key in result:raise RuntimeError('SOFA contains duplicate coordinate azimuth '+key[0]+', elevation '+key[1])
        result[key]={'index':index,'azimuth':float(a),'elevation':float(e)}
    return result

def custom_filter_schema():
    cat=hrir_catalog();az=sorted({float(a) for a,_ in cat});el=sorted({float(e) for _,e in cat});devices=[]
    if PROFILES.is_dir():
        for folder in sorted((p for p in PROFILES.iterdir() if p.is_dir() and not p.name.startswith('.')),key=lambda p:p.name.casefold()):
            if (folder/'stereo.yml').is_file():devices.append({'id':folder.name,'label':folder.name})
    return {'devices':devices,'azimuth':{'min':min(az),'max':max(az)},'elevation':{'min':min(el),'max':max(el)},'coordinates':[{'azimuth':float(a),'elevation':float(e)} for a,e in sorted(cat,key=lambda pair:(float(pair[0]),float(pair[1])))],'pointModes':[{'id':'left','label':'Left channel to both ears'},{'id':'right','label':'Right channel to both ears'},{'id':'stereo','label':'Left to left, right to right'},{'id':'swap','label':'Left to right, right to left'},{'id':'average','label':'Averaged mono to both ears'}],'source':'SOFA','freeField':True}

def _safe_component(value,label):
    value=str(value or '').strip()
    if not value or value in ('.','..') or '/' in value or '\\' in value or any(ord(c)<32 for c in value):raise ValueError('Invalid '+label)
    return value

def custom_filter_target(device_name,name):
    device_name=_safe_component(device_name,'device');name=_safe_component(name,'filter name')
    if name.casefold().endswith(('.yml','.yaml')):name=Path(name).stem
    device=(PROFILES/device_name).resolve();base=PROFILES.resolve()
    if base not in device.parents or not device.is_dir():raise FileNotFoundError('Device not found: '+device_name)
    return device,name,device/'custom'/(name+'.yml')

def _apply_sofa_delay(stereo,delay_seconds,rate):
    import numpy as np
    samples=np.asarray(delay_seconds,dtype=np.float64)*rate
    if np.all(np.abs(samples)<1e-12):return stereo.copy()
    if np.min(samples)<0:samples=samples-np.min(samples)
    length=len(stereo)+int(math.ceil(np.max(samples)))+16;nfft=1<<(length-1).bit_length();frequency=np.fft.rfftfreq(nfft,1/rate)
    spectrum=np.fft.rfft(stereo,n=nfft,axis=0)
    spectrum*=np.exp(-2j*np.pi*frequency[:,None]*(samples[None,:]/rate))
    return np.fft.irfft(spectrum,nfft,axis=0)[:length]

def _write_sofa_point(path,index,ir,rate,delay):
    try:import soundfile as sf
    except ImportError as error:raise RuntimeError('Custom SOFA filters require Python soundfile') from error
    data=_apply_sofa_delay(ir[index].T,delay[index],rate)
    path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_name('.'+path.stem+'.new'+path.suffix)
    sf.write(temporary,data,rate,subtype='DOUBLE');check,written_rate=sf.read(temporary,always_2d=True,dtype='float64')
    if written_rate!=rate or not np.array_equal(check,data):temporary.unlink(missing_ok=True);raise RuntimeError('Extracted SOFA HRIR verification failed')
    temporary.replace(path)

def create_custom_filter(request):
    if not isinstance(request,dict):raise ValueError('Request must be an object')
    device,name,target=custom_filter_target(request.get('device'),request.get('name'));baseline=device/'stereo.yml'
    if not baseline.is_file():raise FileNotFoundError('Baseline not found: '+str(baseline))
    raw=request.get('points');cat=hrir_catalog();ir,rate,azimuth,elevation,delay=_read_sofa()
    if not isinstance(raw,list) or not raw:raise ValueError('Add at least one SOFA point')
    points=[]
    for item in raw:
        if not isinstance(item,dict):raise ValueError('Invalid SOFA point row')
        az=_coord_value(item.get('azimuth'));el=_coord_value(item.get('elevation'));source=str(item.get('source') or '').lower()
        if source not in ('average','left','right','stereo','swap'):raise ValueError('Source must be average, left, right, stereo, or swapped stereo')
        row=cat.get((az,el))
        if row is None:raise ValueError('No SOFA measurement exists for azimuth '+az+', elevation '+el)
        points.append((row['index'],source,az,el))
    config=yaml.safe_load(baseline.read_text(encoding='utf-8'));filters=config.setdefault('filters',{});mixers=config.setdefault('mixers',{});pipeline=config.setdefault('pipeline',[]);count=len(points);spread=[]
    custom=target.parent;assets=custom/'.assets'/name
    for i,(index,source,az,el) in enumerate(points):
        asset=assets/f'point-{i:03d}-az-{az}-el-{el}.wav'
        mono={'average':[{'channel':0,'gain':-6.02059991328},{'channel':1,'gain':-6.02059991328}],'left':[{'channel':0,'gain':0.0}],'right':[{'channel':1,'gain':0.0}]}
        if source in mono:left_sources=right_sources=mono[source]
        elif source=='stereo':left_sources=[{'channel':0,'gain':0.0}];right_sources=[{'channel':1,'gain':0.0}]
        else:left_sources=[{'channel':1,'gain':0.0}];right_sources=[{'channel':0,'gain':0.0}]
        spread.extend(({'dest':2*i,'sources':left_sources},{'dest':2*i+1,'sources':right_sources}))
        filters[f'custom_sofa_{i}_left']={'type':'Conv','parameters':{'type':'Wav','filename':str(asset),'channel':0}}
        filters[f'custom_sofa_{i}_right']={'type':'Conv','parameters':{'type':'Wav','filename':str(asset),'channel':1}}
    mixers['custom_sofa_spread']={'channels':{'in':2,'out':2*count},'mapping':spread};gain=-20*math.log10(count)
    mixers['custom_sofa_merge']={'channels':{'in':2*count,'out':2},'mapping':[{'dest':ear,'sources':[{'channel':2*i+ear,'gain':gain} for i in range(count)]} for ear in (0,1)]}
    pipeline.append({'type':'Mixer','name':'custom_sofa_spread'})
    for i in range(count):pipeline.extend(({'type':'Filter','channels':[2*i],'names':[f'custom_sofa_{i}_left']},{'type':'Filter','channels':[2*i+1],'names':[f'custom_sofa_{i}_right']}))
    pipeline.append({'type':'Mixer','name':'custom_sofa_merge'});config['title']=name;config['description']=f'Custom free-field SOFA spatial filter based on stereo.yml; {count} measured point(s), full HRIR timing retained'
    import runpy
    updater=runpy.run_path(str(ADD_AUDIO_DEVICE),run_name='audio_device_helpers');baseline_map={device.resolve():baseline.read_text(encoding='utf-8')}
    config=yaml.safe_load(updater['rebuild_custom_profile'](target,yaml.safe_dump(config,sort_keys=False),baseline_map))
    CUSTOM_FILTER_LOCK.parent.mkdir(parents=True,exist_ok=True)
    with CUSTOM_FILTER_LOCK.open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if target.exists():raise FileExistsError('Custom filter already exists: '+name)
        custom.mkdir(parents=True,exist_ok=True)
        try:
            for i,(index,source,az,el) in enumerate(points):_write_sofa_point(assets/f'point-{i:03d}-az-{az}-el-{el}.wav',index,ir,rate,delay)
            temp=target.with_name('.'+target.name+'.new');temp.write_text(yaml.safe_dump(config,sort_keys=False),encoding='utf-8');temp.replace(target)
        except BaseException:
            shutil.rmtree(assets,ignore_errors=True);raise
    with CACHELOCK:CACHE['profiles']=(0.0,[])
    return {'profile':_filter_relative(target),'points':count,'source':'SOFA','freeField':True}

def custom_filter_cli():
    schema=custom_filter_schema();devices=schema['devices']
    def pick(title,items):
        while True:
            print('\n'+title)
            for i,x in enumerate(items,1):print(f'[{i}] {x}')
            answer=input('Select (0 cancels): ').strip()
            if answer=='0':raise KeyboardInterrupt
            if answer.isdecimal() and 1<=int(answer)<=len(items):return int(answer)-1
            print('Enter a listed number.')
    if not devices:raise RuntimeError('No compatible device baselines found')
    device=devices[pick('Device',[x['label'] for x in devices])];points=[];cat=hrir_catalog()
    print(f"Azimuth range {schema['azimuth']['min']} to {schema['azimuth']['max']}; elevation range {schema['elevation']['min']} to {schema['elevation']['max']}")
    while True:
        raw=input('Azimuth elevation, separated by a space (0 cancels; example 20.5 -15): ').strip()
        if raw=='0':raise KeyboardInterrupt
        parts=raw.split()
        if len(parts)!=2:print('Enter two decimal numbers separated by a space.');continue
        try:az=_coord_value(parts[0]);el=_coord_value(parts[1])
        except ValueError as error:print(error);continue
        if (az,el) not in cat:
            nearby=sorted(cat,key=lambda pair:(float(pair[0])-float(az))**2+(float(pair[1])-float(el))**2)[:5]
            print('That exact coordinate does not exist. Nearby valid options: '+', '.join('az '+a+' / el '+e for a,e in nearby));continue
        source=('average','left','right','stereo','swap')[pick('Audio for this point',['Averaged mono to both ears','Left channel to both ears','Right channel to both ears','Left to left, right to right','Left to right, right to left'])]
        points.append({'azimuth':az,'elevation':el,'source':source})
        if pick('Next',['Create filter','Add another SOFA point'])==0:break
    while True:
        name=input('Filter name: ').strip()
        try:result=create_custom_filter({'device':device['id'],'name':name,'points':points});break
        except FileExistsError:print('A custom filter with that name already exists for this device. Enter another name.')
    print('Created '+result['profile'])
