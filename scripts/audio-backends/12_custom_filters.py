# Shared custom binaural HRIR filter builder.
import yaml
HRIR_ROOT=HOME/'Documents/prefs/audio/H5/H5_HRIR_WAV/96K_24bit'
CUSTOM_FILTER_LOCK=HOME/'.local/state/sway/custom-audio-filter.lock'
_HRIR_RE=re.compile(r'^azi_(-?\d+),(\d+)_ele_(-?\d+),(\d+)\.wav$',re.I)
def _coord_value(value):
    text=str(value).strip().replace(',','.')
    if not re.fullmatch(r'-?\d+(?:\.\d+)?',text):raise ValueError('Coordinates must be ordinary numbers, for example 20.5 or -15')
    number=float(text)
    if not math.isfinite(number):raise ValueError('Coordinates must be finite')
    return format(number,'.10g')

def _filename_coord(whole,fraction):return _coord_value(str(whole)+'.'+str(fraction))

def hrir_catalog():
    if not HRIR_ROOT.is_dir():raise FileNotFoundError('HRIR directory not found: '+str(HRIR_ROOT))
    result={}
    for path in HRIR_ROOT.iterdir():
        m=_HRIR_RE.match(path.name)
        if m:result[(_filename_coord(m[1],m[2]),_filename_coord(m[3],m[4]))]=path.resolve()
    if not result:raise RuntimeError('No HRIR WAV files found in '+str(HRIR_ROOT))
    return result
def custom_filter_schema():
    cat=hrir_catalog();az=sorted({float(a) for a,_ in cat});el=sorted({float(e) for _,e in cat});devices=[]
    if PROFILES.is_dir():
        for folder in sorted((p for p in PROFILES.iterdir() if p.is_dir() and not p.name.startswith('.')),key=lambda p:p.name.casefold()):
            modes=[x for x in ('limited','gain','raw') if (folder/(x+'.yml')).is_file()]
            if modes:devices.append({'id':folder.name,'label':folder.name,'modes':modes})
    return {'devices':devices,'azimuth':{'min':min(az),'max':max(az)},'elevation':{'min':min(el),'max':max(el)},'coordinates':[{'azimuth':float(a),'elevation':float(e)} for a,e in sorted(cat,key=lambda pair:(float(pair[0]),float(pair[1])))],'pointModes':['average','left','right']}
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
def create_custom_filter(request):
    if not isinstance(request,dict):raise ValueError('Request must be an object')
    device,name,target=custom_filter_target(request.get('device'),request.get('name'))
    mode=str(request.get('mode','')).lower()
    if mode not in ('limited','gain','raw'):raise ValueError('Mode must be limited, gain, or raw')
    baseline=device/(mode+'.yml')
    if not baseline.is_file():raise FileNotFoundError('Baseline not found: '+str(baseline))
    raw=request.get('points');cat=hrir_catalog()
    if not isinstance(raw,list) or not raw:raise ValueError('Add at least one HRIR')
    points=[]
    for item in raw:
        if not isinstance(item,dict):raise ValueError('Invalid HRIR row')
        az=_coord_value(item.get('azimuth'));el=_coord_value(item.get('elevation'));source=str(item.get('source') or '').lower()
        if source not in ('average','left','right'):raise ValueError('Source must be average, left, or right')
        path=cat.get((az,el))
        if path is None:raise ValueError('No HRIR exists for azimuth '+az+', elevation '+el)
        points.append((path,source,az,el))
    config=yaml.safe_load(baseline.read_text(encoding='utf-8'))
    filters=config.setdefault('filters',{});mixers=config.setdefault('mixers',{});pipeline=config.setdefault('pipeline',[]);count=len(points);spread=[]
    for i,(path,source,az,el) in enumerate(points):
        sources={'average':[{'channel':0,'gain':-6.02059991328},{'channel':1,'gain':-6.02059991328}],'left':[{'channel':0,'gain':0.0}],'right':[{'channel':1,'gain':0.0}]}[source]
        spread.extend(({'dest':2*i,'sources':sources},{'dest':2*i+1,'sources':sources}))
        filters[f'custom_hrir_{i}_left']={'type':'Conv','parameters':{'type':'Wav','filename':str(path),'channel':0}}
        filters[f'custom_hrir_{i}_right']={'type':'Conv','parameters':{'type':'Wav','filename':str(path),'channel':1}}
    mixers['custom_hrir_spread']={'channels':{'in':2,'out':2*count},'mapping':spread};gain=-20*math.log10(count)
    mixers['custom_hrir_merge']={'channels':{'in':2*count,'out':2},'mapping':[{'dest':ear,'sources':[{'channel':2*i+ear,'gain':gain} for i in range(count)]} for ear in (0,1)]}
    pipeline.append({'type':'Mixer','name':'custom_hrir_spread'})
    for i in range(count):pipeline.extend(({'type':'Filter','channels':[2*i],'names':[f'custom_hrir_{i}_left']},{'type':'Filter','channels':[2*i+1],'names':[f'custom_hrir_{i}_right']}))
    pipeline.append({'type':'Mixer','name':'custom_hrir_merge'});config['title']=name;config['description']=f'Custom binaural HRIR filter based on {mode}.yml; {count} point(s)'
    custom=target.parent;CUSTOM_FILTER_LOCK.parent.mkdir(parents=True,exist_ok=True)
    with CUSTOM_FILTER_LOCK.open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);custom.mkdir(parents=True,exist_ok=True)
        if target.exists():raise FileExistsError('Custom filter already exists: '+name)
        temp=target.with_name('.'+target.name+'.new');temp.write_text(yaml.safe_dump(config,sort_keys=False),encoding='utf-8');temp.replace(target)
    with CACHELOCK:CACHE['profiles']=(0.0,[])
    return {'profile':_filter_relative(target),'points':count}
def custom_filter_cli():
    schema=custom_filter_schema();devices=schema['devices']
    def pick(title,items):
        while True:
            print('\n'+title)
            for i,x in enumerate(items,1):print(f'[{i}] {x}')
            a=input('Select (0 cancels): ').strip()
            if a=='0':raise KeyboardInterrupt
            if a.isdecimal() and 1<=int(a)<=len(items):return int(a)-1
            print('Enter a listed number.')
    if not devices:raise RuntimeError('No compatible device baselines found')
    device=devices[pick('Device',[x['label'] for x in devices])];points=[]
    print(f"Azimuth range {schema['azimuth']['min']} to {schema['azimuth']['max']}; elevation range {schema['elevation']['min']} to {schema['elevation']['max']}")
    cat=hrir_catalog()
    while True:
        raw=input('Azimuth elevation, separated by a space (0 cancels; example 20.5 -15): ').strip()
        if raw=='0':raise KeyboardInterrupt
        parts=raw.split()
        if len(parts)!=2:print('Enter two decimal numbers separated by a space.');continue
        try:az=_coord_value(parts[0]);el=_coord_value(parts[1])
        except ValueError as e:print(e);continue
        if (az,el) not in cat:
            nearby=sorted(cat,key=lambda pair:(float(pair[0])-float(az))**2+(float(pair[1])-float(el))**2)[:5]
            print('That exact coordinate does not exist. Nearby valid options: '+', '.join('az '+a+' / el '+e for a,e in nearby));continue
        source=('average','left','right')[pick('Audio for this point',['Average','Left','Right'])]
        points.append({'azimuth':az,'elevation':el,'source':source})
        if pick('Next',['Create filter','Add another HRIR'])==0:break
    mode=device['modes'][pick('Mode',device['modes'])]
    while True:
        name=input('Filter name: ').strip()
        try:
            result=create_custom_filter({'device':device['id'],'name':name,'mode':mode,'points':points})
            break
        except FileExistsError:
            print('A custom filter with that name already exists for this device. Enter another name.')
    print('Created '+result['profile'])
