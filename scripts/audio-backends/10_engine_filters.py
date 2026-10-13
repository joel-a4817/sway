HPCF_CONFIG=ADD_AUDIO_DEVICE

def _filter_relative(path):
    path=Path(path).resolve();base=PROFILES.resolve()
    if path==base or base not in path.parents:raise ValueError('Filter path escapes the audio filters directory')
    return path.relative_to(base).as_posix()

def _filter_directory(value=''):
    if not isinstance(value,str) or '\0' in value:raise ValueError('Invalid filter directory')
    relative=Path(value or '.')
    if relative.is_absolute() or any(part in ('','..') for part in relative.parts if part!='.'):
        raise ValueError('Invalid filter directory')
    target=(PROFILES/relative).resolve();base=PROFILES.resolve()
    if target!=base and base not in target.parents:raise ValueError('Filter directory escapes the audio filters directory')
    if not target.is_dir():raise FileNotFoundError('Filter directory not found: '+value)
    return target

def profiles():
    def load():
        if not PROFILES.is_dir():return []
        found=[]
        for item in PROFILES.rglob('*'):
            try:
                if item.is_file() and item.suffix.casefold() in ('.yml','.yaml') and not item.name.startswith('.'):
                    _filter_relative(item);found.append(item)
            except OSError:pass
        return sorted(found,key=lambda item:tuple(part.casefold() for part in item.relative_to(PROFILES).parts))
    return cached('profiles',5.0,load)

BRIR_DIR=PROFILES

def profile_id(path):return _filter_relative(path)

def filter_directory(value=''):
    directory=_filter_directory(value)
    relative='' if directory==PROFILES.resolve() else directory.relative_to(PROFILES.resolve()).as_posix()
    entries=[]
    try:children=list(directory.iterdir())
    except OSError as error:raise RuntimeError('Could not read filter directory: '+str(error)) from error
    folders=sorted((item for item in children if item.is_dir() and not item.name.startswith('.')),key=lambda x:x.name.casefold())
    yaml_files=sorted((item for item in children if item.is_file() and item.suffix.casefold() in ('.yml','.yaml') and not item.name.startswith('.')),key=lambda x:x.name.casefold())
    for item in folders:
        child=_filter_relative(item)
        entries.append({'type':'directory','id':child,'name':item.name,'label':item.name})
    for item in yaml_files:
        key=_filter_relative(item)
        entries.append({'type':'profile','id':key,'name':item.name,'label':item.stem})
    return {'path':relative,'label':directory.name if relative else 'Listening profiles','parent':relative.rsplit('/',1)[0] if '/' in relative else ('' if relative else None),'entries':entries}

def listening_filters():
    result={NO_FILTER:{'group':'','label':'No filter (bypass CamillaDSP)','mediaControl':True}}
    for item in profiles():
        key=_filter_relative(item)
        result[key]={'group':str(Path(key).parent) if '/' in key else '',
                     'label':item.stem,
                     'mediaControl':True}
    return result

def profile(name):
    if not isinstance(name,str) or not name or '\0' in name:raise ValueError('Invalid profile')
    relative=Path(name)
    if relative.is_absolute() or any(part in ('','.', '..') for part in relative.parts):raise ValueError('Invalid profile')
    target=(PROFILES/relative).resolve();base=PROFILES.resolve()
    if base not in target.parents or target.suffix.casefold() not in ('.yml','.yaml') or not target.is_file():
        raise FileNotFoundError('Missing profile: '+name)
    return target

def selected_filter():
    try:value=ACTIVE.read_text().strip()
    except OSError:return NO_FILTER
    if not value or value==NO_FILTER:return NO_FILTER
    try:return profile_id(profile(value))
    except (ValueError,FileNotFoundError):return NO_FILTER
