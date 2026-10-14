# Custom binaural filter creation shared by the web server and Media Control.
import yaml

HRIR_ROOT=HOME/'Documents/prefs/audio/H5/H5_HRIR_WAV/96K_24bit'
CUSTOM_FILTER_LOCK=HOME/'.local/state/sway/custom-audio-filter.lock'
_HRIR_RE=re.compile(r'^azi_(-?\d+(?:,\d+)?)_ele_(-?\d+(?:,\d+)?)\.wav$',re.I)

def _coord_text(value):
    number=float(str(value).strip().replace(',','.'))
    if not math.isfinite(number):raise ValueError('Coordinate must be finite')
    return format(number,'.10g')

def hrir_catalog():
    if not HRIR_ROOT.is_dir():raise FileNotFoundError('HRIR directory not found: '+str(HRIR_ROOT))
    result={}
    for path in HRIR_ROOT.iterdir():
        match=_HRIR_RE.match(path.name)
        if match:
            az=_coord_text(match.group(1));el=_coord_text(match.group(2));result[(az,el)]=path.resolve()
    if not result:raise RuntimeError('No azi_*_ele_*.wav HRIR files found in '+str(HRIR_ROOT))
    return result

def custom_filter_schema():
    catalog=hrir_catalog();az=sorted({float(a) for a,_ in catalog});el=sorted({float(e) for _,e in catalog})
    devices=[]
    if PROFILES.is_dir():
        for folder in sorted((p for p in PROFILES.iterdir() if p.is_dir() and not p.name.startswith('.')),key=lambda p:p.name.casefold()):
            modes=[mode for mode in ('gain','limited','raw') if (folder/(mode+'.yml')).is_file()]
            if modes:devices.append({'id':folder.name,'label':folder.name,'modes':modes})
    return {'devices':devices,'azimuth':{'min':min(az),'max':max(az),'values':az},'elevation':{'min':min(el),'max':max(el),'values':el},'pointModes':['average','left','right']}

def _safe_name(value):
    value=str(value or '').strip()
    if not value or value in ('.','..') or '/' in value or '\\' in value or any(ord(c)<32 for c in value):raise ValueError('Filter name must be a nonempty filename without path separators')
    if value.casefold().endswith(('.yml','.yaml')):value=Path(value).stem
    return value

def _safe_device(value):
    value=str(value or '')
    if not value or value in ('.','..') or '/' in value or '\\' in value:raise ValueError('Invalid device')
    folder=(PROFILES/value).resolve();base=PROFILES.resolve()
    if base not in folder.parents or not folder.is_dir():raise FileNotFoundError('Device filter directory not found: '+value)
    return folder

def _point(value,catalog):
    if not isinstance(value,dict):raise ValueError('Each HRIR point must be an object')
    az=_coord_text(value.get('azimuth'));el=_coord_text(value.get('elevation'));mode=str(value.get('source','average')).strip().lower()
    if mode not in ('average','left','right'):raise ValueError('Point source must be average, left, or right')
    path=catalog.get((az,el))
    if path is None:raise ValueError('No HRIR exists for azimuth '+az+', elevation '+el)
    return {'azimuth':az,'elevation':el,'source':mode,'path':path}

def create_custom_filter(request):
    if not isinstance(request,dict):raise ValueError('Custom filter request must be an object')
    device=_safe_device(request.get('device'));name=_safe_name(request.get('name'));mode=str(request.get('mode','')).lower()
    if mode not in ('gain','limited','raw'):raise ValueError('Mode must be gain, limited, or raw')
    baseline=device/(mode+'.yml')
    if not baseline.is_file():raise FileNotFoundError('Baseline does not exist: '+str(baseline))
    catalog=hrir_catalog();raw=request.get('points')
    if not isinstance(raw,list) or not raw:raise ValueError('Select at least one HRIR point')
    points=[_point(item,catalog) for item in raw]
    config=yaml.safe_load(baseline.read_text(encoding='utf-8'))
    if not isinstance(config,dict):raise ValueError('Baseline YAML is not an object')
    filters=config.setdefault('filters',{});mixers=config.setdefault('mixers',{});pipeline=config.setdefault('pipeline',[])
    if not isinstance(filters,dict) or not isinstance(mixers,dict) or not isinstance(pipeline,list):raise ValueError('Baseline has unsupported filters, mixers, or pipeline structure')
    count=len(points);spread='custom_hrir_spread';merge='custom_hrir_merge'
    spread_map=[]
    for index,point in enumerate(points):
        gains={'average':[(0,-6.02059991328),(1,-6.02059991328)],'left':[(0,0.0)],'right':[(1,0.0)]}[point['source']]
        for output in (index*2,index*2+1):spread_map.append({'dest':output,'sources':[{'channel':channel,'gain':gain} for channel,gain in gains]})
        for ear,channel in (('left',0),('right',1)):
            filters[f'custom_hrir_{index}_{ear}']={'type':'Conv','parameters':{'type':'Wav','filename':str(point['path']),'channel':channel}}
    mixers[spread]={'channels':{'in':2,'out':count*2},'mapping':spread_map}
    average_gain=-20.0*math.log10(count)
    mixers[merge]={'channels':{'in':count*2,'out':2},'mapping':[{'dest':ear,'sources':[{'channel':index*2+ear,'gain':average_gain} for index in range(count)]} for ear in (0,1)]}
    pipeline.append({'type':'Mixer','name':spread})
    for index in range(count):
        pipeline.extend(({'type':'Filter','channels':[index*2],'names':[f'custom_hrir_{index}_left']},{'type':'Filter','channels':[index*2+1],'names':[f'custom_hrir_{index}_right']}))
    pipeline.append({'type':'Mixer','name':merge})
    config['title']=name
    config['description']='Custom binaural HRIR filter based on '+mode+'.yml; '+str(count)+' spatial point(s)'
    custom=device/'custom';target=custom/(name+'.yml');meta=custom/(name+'.json')
    CUSTOM_FILTER_LOCK.parent.mkdir(parents=True,exist_ok=True)
    with CUSTOM_FILTER_LOCK.open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);custom.mkdir(parents=True,exist_ok=True)
        if target.exists() and not bool(request.get('overwrite')):raise FileExistsError('Custom filter already exists: '+name)
        temp=target.with_name('.'+target.name+'.new');temp.write_text(yaml.safe_dump(config,sort_keys=False,allow_unicode=True),encoding='utf-8');temp.replace(target)
        meta_temp=meta.with_name('.'+meta.name+'.new');meta_temp.write_text(json.dumps({'device':device.name,'name':name,'mode':mode,'points':[{k:v for k,v in p.items() if k!='path'} for p in points]},indent=2)+'\n',encoding='utf-8');meta_temp.replace(meta)
    with CACHELOCK:CACHE['profiles']=(0.0,[])
    return {'profile':_filter_relative(target),'device':device.name,'name':name,'mode':mode,'points':len(points)}

def custom_filter_cli():
    schema=custom_filter_schema();devices=schema['devices']
    if not devices:raise RuntimeError('No device directories with gain, limited, or raw baselines were found')
    def pick(title,items):
        while True:
            print('\n'+title)
            for i,item in enumerate(items,1):print(f'[{i}] {item}')
            answer=input('Select (0 cancels): ').strip()
            if answer=='0':raise KeyboardInterrupt
            if answer.isdecimal() and 1<=int(answer)<=len(items):return int(answer)-1
            print('Enter a listed number.')
    device=devices[pick('Custom filter device',[d['label'] for d in devices])];points=[]
    print(f"Azimuth range: {schema['azimuth']['min']} to {schema['azimuth']['max']}; elevation range: {schema['elevation']['min']} to {schema['elevation']['max']}")
    catalog=hrir_catalog()
    while True:
        raw=input('Azimuth,elevation (0 cancels): ').strip()
        if raw=='0':raise KeyboardInterrupt
        values=[part.strip() for part in raw.split(',')]
        if len(values)!=2:print('Enter exactly two values, for example 0, 90.');continue
        try:az=_coord_text(values[0]);el=_coord_text(values[1])
        except ValueError as error:print(error);continue
        if (az,el) not in catalog:print('That azimuth/elevation combination does not exist; try again.');continue
        source=('average','left','right')[pick('Audio sent to this point',['Average of left and right','Left only','Right only'])]
        points.append({'azimuth':az,'elevation':el,'source':source})
        if pick('Next step',['Create filter','Add another HRIR'])==0:break
    mode=device['modes'][pick('Baseline mode',device['modes'])]
    name=input('Custom filter name: ').strip()
    result=create_custom_filter({'device':device['id'],'name':name,'mode':mode,'points':points})
    print('Created '+result['profile'])
