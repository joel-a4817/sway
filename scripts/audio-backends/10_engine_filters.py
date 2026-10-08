HPCF_CONFIG=ADD_AUDIO_DEVICE
_FOLDER_RE=re.compile(r'^(?:\((\d+)ms(?:-(IE|OE))?\)|(\d+)ms(?:-(IE|OE))?(?:$|[-_ ]))(.*)$',re.I)
_PROFILE_RE=re.compile(r'^\d+-(?:(ie|oe)-)?(.+?)-(\d+)ms-(.+)$',re.I)

def _folder_parts(name):
    match=_FOLDER_RE.match(name)
    if not match:return None
    delay=match.group(1) or match.group(3)
    kind=(match.group(2) or match.group(4) or '').upper()
    label=match.group(5).strip(' -_')
    return delay,kind,label

def profiles():
    def load():
        found=[]
        if not PROFILES.is_dir():return found
        for pattern in ('*.yml','*.yaml'):
            for item in PROFILES.rglob(pattern):
                if not item.is_file():continue
                if item.parent==PROFILES or _folder_parts(item.parent.name):found.append(item)
        return sorted(found,key=lambda item:(item.name.casefold(),str(item).casefold()))
    return cached('profiles',5.0,load)

BRIR_DIR=PROFILES

def _filter_key(value):
    return re.sub(r'[^a-z0-9]+','',value.casefold())

def _device_labels():
    """Read configured HpCF labels without executing the rebuild script."""
    try:
        import ast
        tree=ast.parse(HPCF_CONFIG.read_text(encoding='utf-8'),str(HPCF_CONFIG))
        assignment=next((node for node in tree.body if isinstance(node,(ast.Assign,ast.AnnAssign))
                         and ((isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='HPCFS' for target in node.targets))
                              or (isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name) and node.target.id=='HPCFS'))),None)
        value=assignment.value if assignment is not None else None
        rows=ast.literal_eval(value) if value is not None else ()
    except (OSError,SyntaxError,ValueError,TypeError):
        return {}
    labels={}
    for row in rows if isinstance(rows,(list,tuple)) else ():
        if not isinstance(row,dict):continue
        label=str(row.get('label') or '').strip();kind=str(row.get('kind') or '').upper()
        if label and kind in ('IE','OE'):
            slug=re.sub(r'[^a-z0-9]+','-',label.casefold()).strip('-')
            labels[(kind,slug)]={'label':label}
    return labels

def listening_filters():
    """Resolve current IE/OE profile labels while retaining YAML filenames as IDs."""
    folders={}
    if BRIR_DIR.is_dir():
        for folder in BRIR_DIR.iterdir():
            if not folder.is_dir():continue
            parts=_folder_parts(folder.name)
            if not parts:continue
            delay,kind,label=parts
            folders.setdefault((delay,kind),[]).append((folder.name,_filter_key(label)))
    exact_labels=_device_labels()
    result={NO_FILTER:{'group':'Other','label':'No filter (bypass CamillaDSP)','mediaControl':True}}
    for item in profiles():
        stem=item.stem
        if stem.casefold()=='00-filterless':
            result[item.name]={'group':'Other','label':'Filterless','mediaControl':True}
            continue
        match=_PROFILE_RE.match(stem)
        if not match:
            result[item.name]={'group':'Other','label':stem,'mediaControl':True}
            continue
        kind,device,delay,slug=match.groups();kind=(kind or '').upper()
        device_slug=device.casefold()
        configured=exact_labels.get((kind,device_slug)) if kind else None
        group=(configured or {}).get('label') or device.replace('-',' ').strip().title()
        if kind:group+=' ('+kind+')'
        candidates=folders.get((delay,kind),[]) or folders.get((delay,''),[])
        suffix=_filter_key(slug)
        matches=[name for name,key in candidates if key==suffix]
        if not matches and delay=='0000':matches=[name for name,key in candidates if key.endswith(suffix)]
        label=item.parent.name if item.parent!=PROFILES else (matches[0] if len(matches)==1 else stem)
        result[item.name]={'group':group,'label':label,
                           'mediaControl':True}
    return result

def profile(name):
    if not isinstance(name,str) or Path(name).name!=name:raise ValueError('Invalid profile')
    matches=[item.resolve() for item in profiles() if item.name==name]
    if len(matches)!=1:raise FileNotFoundError('Missing or ambiguous profile: '+name)
    return matches[0]

def selected_filter():
    try:value=ACTIVE.read_text().strip()
    except OSError:return NO_FILTER
    if not value or value==NO_FILTER:return NO_FILTER
    try:profile(value)
    except (ValueError,FileNotFoundError):return NO_FILTER
    return value
