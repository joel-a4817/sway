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

def _profile_title(path):
    """Read the user-facing YAML title without loading or executing the config."""
    try:
        for line in path.read_text(encoding='utf-8').splitlines()[:40]:
            match=re.match(r'^\s*title\s*:\s*(.*?)\s*$',line,re.I)
            if not match:continue
            value=match.group(1).strip()
            if len(value)>=2 and value[0]==value[-1] and value[0] in ('"',"'"):
                value=value[1:-1]
            return value.replace('\\"','"').replace('\\\\','\\').strip()
    except OSError:pass
    return ''

def listening_filters():
    """Return flat user-facing labels while retaining YAML filenames as IDs."""
    result={NO_FILTER:{'group':'','label':'No filter (bypass CamillaDSP)','mediaControl':True}}
    for item in profiles():
        title=_profile_title(item)
        if item.stem.casefold()=='00-filterless':
            label=title or 'Filterless'
        else:
            label=title or item.stem
        result[item.name]={'group':'','label':label,'mediaControl':True}
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
