#!/usr/bin/env python3
"""Safe multi-server Fabric lifecycle manager for NixOS/nix-minecraft."""
from __future__ import annotations
import argparse, contextlib, datetime as dt, fcntl, hashlib, json, os, re, shutil, signal, socket, subprocess, sys, tarfile, time, uuid
from pathlib import Path
sys.dont_write_bytecode=True
from urllib import request, parse
import ipaddress

HOME=Path.home(); ROOT=HOME/"minecraft-server"; RUNTIME_ROOT=Path("/srv/minecraft")
BACKUPS=Path(os.environ.get("MINECRAFT_BACKUP_ROOT",str(Path.home()/"minecraft-backups"))).expanduser(); COORD=ROOT/"coordination"; LOCK=COORD/"local.lock"
LOCAL_STATE=HOME/".local/state/minecraft-server"; PEERS=HOME/".config/minecraft-server/peers"; NETWORK_STATE=Path("/run/user")/str(os.getuid())/"minecraft-server-network.json"
FLOATING_IP=os.environ.get("MINECRAFT_FLOATING_IP","192.168.0.240")
DDNS_ENV=HOME/".config/minecraft-server/ddns.env"
CRITICAL=False
RECOVERING=False
RUNTIME_BACKUP_INTERVAL=int(os.environ.get("MINECRAFT_RUNTIME_BACKUP_INTERVAL","300"))
RUNTIME_BACKUP_RETRY=int(os.environ.get("MINECRAFT_RUNTIME_BACKUP_RETRY","60"))
RUNTIME_BACKUP_KEEP=int(os.environ.get("MINECRAFT_RUNTIME_BACKUP_KEEP","12"))
TRANSITION_BACKUP_KEEP=int(os.environ.get("MINECRAFT_TRANSITION_BACKUP_KEEP","2"))
GENERATION_KEEP=int(os.environ.get("MINECRAFT_GENERATION_KEEP","3"))

def run(cmd,check=True,capture=False,timeout=None):
    return subprocess.run(cmd,check=check,text=True,stdout=subprocess.PIPE if capture else None,stderr=subprocess.STDOUT if capture else None,timeout=timeout)

def acquire_file_lock(handle,label):
    """Wait for an in-process file lock without an arbitrary wall-clock cutoff.

    Kernel flock ownership is released automatically if the holder exits. Emit a
    heartbeat so long legitimate backup/archive work is visible to the caller.
    """
    announced=False;last_report=0.0
    while True:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);return
        except BlockingIOError:
            now=time.monotonic()
            if not announced or now-last_report>=15:
                print(f"Waiting for {label}; the owning process is still holding the lock...",flush=True)
                announced=True;last_report=now
            time.sleep(1)

def unit(name): return f"minecraft-server-{name}.service"
def valid_name(name): return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*",name or ""))
def parse_names(value):
    if not value: return []
    names=[]
    for item in value.split(","):
        name=item.strip()
        if not valid_name(name): raise RuntimeError(f"Invalid server name: {name!r}")
        if name not in names: names.append(name)
    return names
def active(name): return run(["systemctl","is-active","--quiet",unit(name)],check=False).returncode==0

def available_servers():
    r=run(["systemctl","list-unit-files","--no-legend","--no-pager","minecraft-server-*.service"],check=False,capture=True)
    names=[]
    for line in r.stdout.splitlines():
        first=line.split()[0] if line.split() else ""
        m=re.fullmatch(r"minecraft-server-(.+)\.service",first)
        if m and valid_name(m.group(1)): names.append(m.group(1))
    return sorted(set(names))
def running_servers(): return [x for x in available_servers() if active(x)]
def select_server(requested,purpose="start"):
    names=available_servers()
    if requested:
        if requested not in names: raise RuntimeError(f"Unknown server '{requested}'. Available: {', '.join(names) or 'none'}")
        return requested
    candidates=running_servers() if purpose=="stop" else names
    if len(candidates)==1:return candidates[0]
    if not candidates: raise RuntimeError("No Minecraft servers are declared in NixOS")
    if not sys.stdin.isatty(): raise RuntimeError("Multiple servers exist; specify the server name")
    print(f"Choose a server to {purpose}:")
    print("  [E] Exit")
    for i,name in enumerate(candidates,1): print(f"  {i}) {name}{' (running)' if active(name) else ''}")
    while True:
        raw=input("Selection: ").strip()
        if raw.casefold()=="e":raise SystemExit(0)
        if raw.isdigit() and 1<=int(raw)<=len(candidates): return candidates[int(raw)-1]
        if raw in candidates:return raw
        print("Invalid selection.")

def peer_groups():
    groups=[]
    if not PEERS.exists():return groups
    for number,raw in enumerate(PEERS.read_text().splitlines(),1):
        fields=[field.strip("|,;") for field in raw.split("#",1)[0].split()]
        fields=[field for field in fields if field]
        if not fields:continue
        if len(fields)<2:raise RuntimeError(f"Invalid peers line {number}: expected GROUP ADDRESS [ADDRESS ...]")
        groups.append((fields[0],fields[1:]))
    names=[x[0] for x in groups]
    if len(names)!=len(set(names)):raise RuntimeError("Duplicate peer group name")
    return groups
def resolved_peer_groups():
    """Resolve configured peer labels to live full MagicDNS names."""
    groups=peer_groups()
    try:data=json.loads(run(["tailscale","status","--json"],capture=True,timeout=10).stdout or "{}")
    except Exception as error:raise RuntimeError(f"Cannot read Tailscale peer map: {error}")
    peers={}
    for peer in (data.get("Peer") or {}).values():
        dns=(peer.get("DNSName") or "").rstrip(".")
        if dns:peers[dns.split(".",1)[0]]=dns
    resolved=[]
    for label,addresses in groups:
        dns=peers.get(label)
        if not dns:raise RuntimeError(f"Peer {label!r} is not present in the live Tailscale MagicDNS peer map")
        users=[]
        for address in addresses:
            user=address.rsplit("@",1)[0] if "@" in address else os.environ.get("USER","joel")
            target=f"{user}@{dns}"
            if target not in users:users.append(target)
        resolved.append((label,users))
    return resolved

def ssh(address,args):
    return run(["ssh","-i",str(HOME/".ssh/minecraft_peers"),"-o","IdentitiesOnly=yes","-o","StrictHostKeyChecking=accept-new","-o","BatchMode=yes","-o","ConnectTimeout=4",address,"/home/joel/.config/sway/scripts/servers/minecraft-server.py",*args],check=False,capture=True)
def query_group(addresses,args):
    failures=[]
    for address in addresses:
        try:r=ssh(address,args)
        except subprocess.TimeoutExpired:
            failures.append(f"{address}: timed out");continue
        except OSError as error:
            failures.append(f"{address}: {error}");continue
        lines=[line.strip() for line in (r.stdout or "").splitlines() if line.strip()]
        status=next((line for line in reversed(lines)
                     if line=="transitioning" or
                        line=="stopped" or line.startswith("stopped ") or
                        line=="running" or line.startswith("running ")),"")
        # The peer protocol is the machine-readable status line, not a generic
        # assumption that every useful remote command must return zero. Accept
        # both the current zero status and the historical stopped=3 status.
        if (status=="transitioning" or status=="stopped" or status.startswith("stopped ") or
                status=="running" or status.startswith("running ")):
            return address,r,status,failures
        # run(capture=True) merges stderr into stdout, so CompletedProcess.stderr
        # is None. Never call strip() on it.
        detail=((r.stderr or "").strip() or (r.stdout or "").strip() or f"exit {r.returncode}")
        failures.append(f"{address}: {detail}")
    return None,None,"",failures
def validate_peer_identities():
    local=local_node_name()
    for label,addresses in resolved_peer_groups():
        verified=False;failures=[]
        for address in addresses:
            try:
                result=run(["ssh","-i",str(HOME/".ssh/minecraft_peers"),"-o","IdentitiesOnly=yes","-o","StrictHostKeyChecking=accept-new","-o","BatchMode=yes","-o","ConnectTimeout=4",address,
                            "tailscale status --json"],check=False,capture=True,timeout=15)
            except (subprocess.TimeoutExpired,OSError) as error:
                failures.append(f"{address}: {error}");continue
            if result.returncode:
                failures.append(f"{address}: {(result.stdout or '').strip() or 'identity query failed'}");continue
            try:
                data=json.loads(result.stdout or "{}")
                dns=((data.get("Self") or {}).get("DNSName") or "").rstrip(".")
                remote=dns.split(".",1)[0] if dns else ""
            except (ValueError,TypeError):remote=""
            if not remote:
                failures.append(f"{address}: peer MagicDNS identity unavailable");continue
            if label!=remote:
                raise RuntimeError(f"Peer label {label!r} does not match live MagicDNS identity {remote!r}")
            if remote==local:
                raise RuntimeError(f"Peer {label!r} resolves to this laptop instead of the other device")
            verified=True;break
        if not verified:
            raise RuntimeError(f"Cannot validate peer {label}: "+"; ".join(failures))

def remote_state():
    found=[];unreachable=[]
    for label,addresses in resolved_peer_groups():
        address,r,text,failures=query_group(addresses,["status","--machine"])
        if not address:
            detail="; ".join(failures) if failures else "no address attempted"
            unreachable.append(f"{label} ({detail})");continue
        fields=text.split()
        names=["transitioning"] if text=="transitioning" else (fields[1:] if fields else [])
        found.append((label,address,names))
    return found,unreachable

def syncthing_config():
    import xml.etree.ElementTree as ET
    cfg=HOME/".local/state/syncthing/config.xml"
    if not cfg.exists():return None
    root=ET.parse(cfg).getroot(); key=root.findtext("gui/apikey")
    for folder in root.findall("folder"):
        try:path=Path(os.path.expandvars(os.path.expanduser(folder.get("path","")))).resolve()
        except OSError:continue
        if path==ROOT.resolve():
            devices=[item.get("id") for item in folder.findall("device") if item.get("id")]
            return key,folder.get("id"),devices
    return None
def syncthing_request(path,params=None,method="GET",idle_timeout=300):
    item=syncthing_config()
    if not item:raise RuntimeError("Configure ~/minecraft-server as its own Syncthing folder")
    key,folder,_=item;headers={"X-API-Key":key};query=dict(params or {})
    query.setdefault("folder",folder)
    url="http://127.0.0.1:8384"+path
    if query:url+="?"+parse.urlencode(query)
    try:
        return request.urlopen(request.Request(url,method=method,headers=headers),timeout=idle_timeout)
    except TimeoutError as error:
        raise RuntimeError(f"Syncthing API made no socket progress for {idle_timeout}s during {method} {path}") from error

def syncthing_device_id():
    """Return this machine's Syncthing device ID without folder-wide inference."""
    item=syncthing_config()
    if not item:raise RuntimeError("Configure ~/minecraft-server as its own Syncthing folder")
    key,_,_=item
    url="http://127.0.0.1:8384/rest/system/status"
    data=json.load(request.urlopen(request.Request(url,headers={"X-API-Key":key}),timeout=30))
    device_id=(data.get("myID") or "").strip()
    if not device_id:raise RuntimeError("Syncthing did not report this peer's device ID")
    return device_id

def minecraft_peer_device_ids():
    """Resolve only configured MagicDNS Minecraft peers to Syncthing IDs."""
    ids=[]
    for label,addresses in resolved_peer_groups():
        device_id=None;failures=[]
        for address in addresses:
            try:result=ssh(address,["syncthing-id"])
            except (subprocess.TimeoutExpired,OSError) as error:
                failures.append(f"{address}: {error}");continue
            text=(result.stdout or "").strip()
            candidate=next((line.strip() for line in reversed(text.splitlines())
                            if re.fullmatch(r"[A-Z0-9-]{20,}",line.strip())),None)
            if result.returncode==0 and candidate:
                device_id=candidate;break
            failures.append(f"{address}: {text or 'exit '+str(result.returncode)}")
        if not device_id:
            detail="; ".join(failures) or f"no usable address for {label}"
            raise RuntimeError(f"Cannot obtain Syncthing ID for Minecraft peer {label}: {detail}")
        if device_id not in ids:ids.append(device_id)
    return ids

def syncthing_set_paused(paused,idle_timeout=300):
    item=syncthing_config()
    if not item:raise RuntimeError("Configure ~/minecraft-server as its own Syncthing folder")
    key,folder,_=item;headers={"X-API-Key":key,"Content-Type":"application/json"}
    url="http://127.0.0.1:8384/rest/config/folders/"+parse.quote(folder,safe="")
    payload=json.dumps({"paused":bool(paused)}).encode()
    request.urlopen(request.Request(url,data=payload,method="PATCH",headers=headers),timeout=idle_timeout).read()
    last=None;last_progress=time.monotonic()
    while True:
        current=json.load(request.urlopen(request.Request(url,headers={"X-API-Key":key}),timeout=idle_timeout))
        value=bool(current.get("paused",False))
        if value==bool(paused):return
        signature=(value,current.get("stateChanged"))
        if signature!=last:last=signature;last_progress=time.monotonic()
        elif time.monotonic()-last_progress>=idle_timeout:
            raise RuntimeError("Syncthing folder made no pause/resume progress for "+str(idle_timeout)+"s")
        time.sleep(.25)

def syncthing_pause():
    syncthing_set_paused(True)
    print("Syncthing folder paused for atomic restore.")

def syncthing_resume():
    syncthing_set_paused(False)
    print("Syncthing folder resumed.")

def syncthing_status_clean():
    data=json.load(syncthing_request("/rest/db/status"))
    clean=(data.get("state")=="idle" and int(data.get("needTotalItems",0))==0 and
           int(data.get("needBytes",0))==0 and int(data.get("pullErrors",0))==0)
    return clean,data

def syncthing_wait_local(idle_timeout=120,stable_seconds=4):
    """Wait for local convergence; reset only when outstanding work decreases."""
    syncthing_request("/rest/db/scan",method="POST").read()
    stable=None;previous=None;last={};last_progress=last_report=time.monotonic()
    while True:
        clean,last=syncthing_status_clean()
        need=int(last.get("needTotalItems",0) or 0);need_bytes=int(last.get("needBytes",0) or 0)
        state=(last.get("state"),need,need_bytes,int(last.get("pullErrors",0) or 0))
        if previous is None or state != previous:
            # Any coherent state transition proves the API and scanner are alive.
            # Completion still requires idle/zero/error-free below; this watchdog
            # detects a truly frozen state rather than comparing unrelated minima.
            previous=state;last_progress=time.monotonic()
        if clean:
            if stable is None:stable=time.monotonic()
            if time.monotonic()-stable>=stable_seconds:return
        else:stable=None
        now=time.monotonic()
        if now-last_report>=15:
            print(f"Waiting for local Syncthing: state={last.get('state')} items={need} bytes={need_bytes}",flush=True);last_report=now
        if now-last_progress>=idle_timeout:
            raise RuntimeError(f"Local Syncthing made no forward progress for {idle_timeout}s: {last}")
        time.sleep(2)

def syncthing_wait_cluster(idle_timeout=120,stable_seconds=4):
    """Wait for all peers, resetting the watchdog only on directional progress.

    Syncthing counters can churn or oscillate without transferring the pending
    data. Only fewer required items/bytes or higher peer completion qualifies.
    """
    item=syncthing_config()
    if not item:raise RuntimeError("Configure ~/minecraft-server as its own Syncthing folder")
    _,_,folder_devices=item
    devices=minecraft_peer_device_ids()
    missing=[device for device in devices if device not in folder_devices]
    if missing:
        raise RuntimeError("Configured Minecraft peer is not a member of the Minecraft Syncthing folder: "+", ".join(x[:7] for x in missing))
    syncthing_request("/rest/db/scan",method="POST").read()
    stable=None;last_reason="waiting";previous={};last_progress=last_report=time.monotonic()
    while True:
        clean,data=syncthing_status_clean();incomplete=[];peers=[];progressed=False
        for device in devices:
            completion=json.load(syncthing_request("/rest/db/completion",{"device":device}))
            value=float(completion.get("completion",0.0) or 0.0)
            need=int(completion.get("needItems",0) or 0)
            need_bytes=int(completion.get("needBytes",0) or 0)
            peers.append((device,value,need,need_bytes))
            sample=(round(value,9),need,need_bytes)
            if previous.get(device) != sample:progressed=True
            previous[device]=sample
            if value<100.0 or need or need_bytes:
                incomplete.append(f"{device[:7]}={value:.2f}%/{need} items/{need_bytes} bytes")
        if progressed:last_progress=time.monotonic()
        if clean and not incomplete:
            if stable is None:stable=time.monotonic()
            if time.monotonic()-stable>=stable_seconds:return
        else:
            stable=None
            last_reason=(f"local={data.get('state')} need={data.get('needTotalItems',0)} items/"
                         f"{data.get('needBytes',0)} bytes, peers={incomplete}")
        now=time.monotonic()
        if now-last_report>=15:
            print(f"Waiting for Syncthing replication: {last_reason}",flush=True);last_report=now
        if now-last_progress>=idle_timeout:
            raise RuntimeError(f"Syncthing peer replication made no forward progress for {idle_timeout}s: {last_reason}")
        time.sleep(2)


def ensure_dependencies(name=None):
    if name and run(["systemctl","cat",unit(name)],check=False,capture=True).returncode!=0:raise RuntimeError(f"{unit(name)} is not installed; rebuild NixOS")
    if run(["systemctl","is-active","--quiet","NetworkManager.service"],check=False).returncode!=0:raise RuntimeError("NetworkManager is not active")
    run([str(HOME/".config/sway/scripts/systemd-userctl.sh"),"start","syncthing.service"],check=False)
    last=None;progress=time.monotonic()
    while True:
      state=run([str(HOME/".config/sway/scripts/systemd-userctl.sh"),"show","syncthing.service","-p","ActiveState","-p","SubState","--value"],check=False,capture=True).stdout.strip()
      try:request.urlopen("http://127.0.0.1:8384/rest/noauth/health",timeout=10).read();break
      except Exception:pass
      if state!=last:last=state;progress=time.monotonic()
      elif time.monotonic()-progress>=300:raise RuntimeError(f"Syncthing made no startup progress for 300s; state={state!r}")
      time.sleep(1)
def active_lan_route():
    routes=json.loads(run(["ip","-j","-4","route","show","default"],capture=True).stdout or "[]")
    candidates=[]
    for route in routes:
        dev=route.get("dev");gateway=route.get("gateway")
        if not dev or dev in ("lo","tailscale0") or not gateway:continue
        addr_data=json.loads(run(["ip","-j","-4","addr","show","dev",dev],capture=True).stdout or "[]")
        addresses=[]
        for item in addr_data:
            for info in item.get("addr_info",[]):
                if info.get("family")=="inet" and info.get("scope")=="global":addresses.append(info.get("local"))
        if not addresses:continue
        kind=Path(f"/sys/class/net/{dev}/wireless").exists()
        metric=int(route.get("metric",0));candidates.append((1 if kind else 0,metric,dev,gateway,addresses[0]))
    if not candidates:raise RuntimeError("No usable Ethernet or Wi-Fi route to the home LAN")
    _,_,dev,gateway,address=sorted(candidates)[0]
    network=ipaddress.ip_network(f"{address}/24",strict=False)
    if ipaddress.ip_address(FLOATING_IP) not in network:
        raise RuntimeError(f"Floating address {FLOATING_IP} is not on active LAN {network}")
    return dev,gateway,address

def floating_present():
    result=run(["ip","-j","-4","addr","show"],check=False,capture=True)
    if result.returncode:return None
    for item in json.loads(result.stdout or "[]"):
        for info in item.get("addr_info",[]):
            if info.get("local")==FLOATING_IP:return item.get("ifname")
    return None

def floating_claim():
    current=floating_present()
    dev,gateway,address=active_lan_route()
    if current and current!=dev:
        run(["sudo","ip","addr","del",f"{FLOATING_IP}/32","dev",current],check=False,capture=True)
    if current!=dev:
        # Ownership is coordinated through the authenticated Tailscale peers.
        # Do not use arping -D as an ownership test: some Wi-Fi access points and
        # routers proxy-answer ARP probes for a recently used forwarded address,
        # producing a false conflict on one interface/laptop but not the other.
        # Gratuitous ARP below still updates the LAN after ownership moves.
        run(["sudo","ip","addr","add",f"{FLOATING_IP}/32","dev",dev])
    run(["sudo","arping","-U","-c","3","-I",dev,FLOATING_IP],check=False,capture=True)
    NETWORK_STATE.parent.mkdir(parents=True,exist_ok=True)
    NETWORK_STATE.write_text(json.dumps({"device":dev,"gateway":gateway,"primary":address,"floating":FLOATING_IP})+"\n")
    print(f"Minecraft service address {FLOATING_IP} claimed on {dev} ({address})")
    return dev

def floating_release():
    dev=floating_present()
    if dev:run(["sudo","ip","addr","del",f"{FLOATING_IP}/32","dev",dev],check=False,capture=True)
    try:NETWORK_STATE.unlink()
    except FileNotFoundError:pass

def network_prepare():
    floating_claim()

def network_restore():
    floating_release()

def public_ipv4():
    for url in ("https://api.ipify.org", "https://ifconfig.co/ip"):
        try:
            value=request.urlopen(url,timeout=8).read().decode().strip()
            address=ipaddress.ip_address(value)
            if address.version==4 and address.is_global:return value
        except Exception:pass
    return None

def menu_status():
    for name in available_servers():
        if active(name):print(f"{name}\tlocal")
    found,unreachable=remote_state()
    for label,address,names in found:
        for name in names:
            if name.startswith("floating="):continue
            print(f"{name}\tpeer:{label}")
    for detail in unreachable:print(f"!peer\tunreachable:{detail}")
    return 0

def ddns_update():
    if not DDNS_ENV.exists():return None
    env={}
    for raw in DDNS_ENV.read_text().splitlines():
        raw=raw.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            key,value=raw.split("=",1);env[key.strip()]=value.strip()
    domain=env.get("DUCKDNS_DOMAIN");token=env.get("DUCKDNS_TOKEN")
    if not domain or not token:return None
    url="https://www.duckdns.org/update?"+parse.urlencode({"domains":domain,"token":token,"ip":""})
    try:
        result=request.urlopen(url,timeout=15).read().decode().strip()
    except Exception as error:
        print(f"DuckDNS update unavailable: {error}",file=sys.stderr);return None
    if result!="OK":
        print("DuckDNS update rejected: "+result,file=sys.stderr);return None
    return domain

def atomic_mirror(src: Path, dst: Path):
    # Block a live-runtime write before creating or changing any destination
    # path. This guard must precede even parent-directory setup.
    is_runtime = str(dst).startswith("/srv/minecraft/")
    if is_runtime and valid_name(dst.name) and active(dst.name):
        raise RuntimeError(f"Refusing to overwrite active Minecraft runtime: {dst}")

    dst.parent.mkdir(parents=True, exist_ok=True)

    # Keep the nix-minecraft WorkingDirectory root in place. The service and
    # lifecycle manager both run as joel:users, so all world data remains
    # directly readable for backup and Syncthing publication.
    if str(dst).startswith("/srv/minecraft/"):
        # Destructive rsync into a live world is never permitted, even if a
        # caller regresses or a recovery journal is replayed unexpectedly.
        # This is the final write barrier immediately before --delete.
        if not dst.exists():
            dst.mkdir(mode=0o2770)
        if not os.access(dst, os.W_OK | os.X_OK):
            raise RuntimeError(f"Runtime directory is not writable: {dst}")
        if src.exists():
            run(["rsync", "-rltD", "--delete", "--chmod=Du=rwx,Dg=rwx,Do=,Fu=rw,Fg=rw,Fo=", f"{src}/", f"{dst}/"])
        else:
            # Empty first start: retain declarative files/symlinks generated by
            # nix-minecraft rather than deleting the service's launch inputs.
            print(f"No stopped snapshot exists for {dst.name}; using the generated server directory.")
        return

    # Synced stopped snapshots remain atomically published outside /srv.
    temp = dst.with_name(dst.name + ".incoming")
    old = dst.with_name(dst.name + ".old")
    shutil.rmtree(temp, ignore_errors=True)
    temp.mkdir()
    if src.exists():
        run(["rsync", "-a", "--delete", f"{src}/", f"{temp}/"])
    shutil.rmtree(old, ignore_errors=True)
    if dst.exists():
        dst.rename(old)
    temp.rename(dst)
    shutil.rmtree(old, ignore_errors=True)

def archive_transaction(name,source,kind):
    if not source.is_dir() or not any(source.iterdir()):return None
    folder=BACKUPS/local_node_name()/name/kind;folder.mkdir(parents=True,exist_ok=True)
    generation=uuid.uuid4().hex;root=folder/generation;root.mkdir(parents=True,exist_ok=False)
    incoming=root/".backup.tar.gz.incoming";archive=root/"backup.tar.gz";manifest=root/"manifest.json"
    try:
        with incoming.open("wb") as raw:
            with tarfile.open(fileobj=raw,mode="w:gz") as handle:handle.add(source,arcname=name,recursive=True)
            raw.flush();os.fsync(raw.fileno())
        validate_runtime_archive(incoming,name);digest=archive_sha256(incoming)
        os.replace(incoming,archive);fsync_directory(root)
        data={"schema":1,"complete":True,"server":name,"peer":local_node_name(),"kind":kind,
              "generation":generation,"created":dt.datetime.now(dt.timezone.utc).isoformat(),
              "archive":"backup.tar.gz","archive_sha256":digest,"source":str(source)}
        write_json_atomic(manifest,data);return archive
    except Exception:
        incoming.unlink(missing_ok=True);raise

def accepted_server_names(name):
    aliases_file=ROOT/"rename-aliases.json"
    names={name}
    try:names.update(json.loads(aliases_file.read_text()).get("aliases",{}).get(name,[]))
    except Exception:pass
    return names

def validated_backup(path,name):
    manifest=path.parent/"manifest.json"
    if not manifest.is_file():return False
    try:data=json.loads(manifest.read_text())
    except Exception:return False
    return bool(data.get("complete") and data.get("server") in accepted_server_names(name) and data.get("archive_sha256")==archive_sha256(path))

def active_generation_path(name):return LOCAL_STATE/f"{name}.active-generation.json"

def recover_save_guard(name):
    guard=save_guard(name)
    if not guard.exists():return
    runtime=unit_workdir(name)
    if active(name):rcon(runtime,"save-on")
    guard.unlink(missing_ok=True);fsync_directory(guard.parent)

def generation_verified(name,generation,digest,idle_timeout=300):
    root=generation_path(name,generation);last=None;progress=time.monotonic()
    while True:
      archive=root/"snapshot.tar.gz";manifest=root/"manifest.json";sig=(archive.exists(),archive.stat().st_size if archive.exists() else 0,manifest.exists(),manifest.stat().st_size if manifest.exists() else 0)
      if sig!=last:last=sig;progress=time.monotonic()
      if archive.is_file() and manifest.is_file():
        data,_=read_generation(name,generation)
        if data.get("archive_sha256")==digest:print(f"GENERATION-VERIFIED {name} {generation} {digest}");return
      if time.monotonic()-progress>=idle_timeout:raise RuntimeError(f"Generation replication made no progress for {idle_timeout}s: {name}/{generation}")
      time.sleep(2)

def verify_generation_on_peers(name,record):
    failures=[];expected=f"GENERATION-VERIFIED {name} {record['generation']} {record['archive_sha256']}"
    for label,addresses in resolved_peer_groups():
        ok=False;attempts=[]
        for address in addresses:
            r=ssh(address,["verify-generation",name,"--generation",record["generation"],"--digest",record["archive_sha256"]])
            out=(r.stdout or "").strip()
            if r.returncode==0 and expected in out:ok=True;break
            attempts.append(f"{address}: {out or r.returncode}")
        if not ok:failures.append(f"{label}: {'; '.join(attempts)}")
    if failures:raise RuntimeError("Generation replication incomplete: "+"; ".join(failures))

def syncthing_backup_folder_audit():
    """Use Tailscale for network identity and Syncthing metadata for folder/device checks.

    Linux hostname is never used. Backup ownership on disk is keyed by Tailscale
    MagicDNS. Syncthing folder labels and device display names may differ and are
    used only for diagnostics and validation within Syncthing.
    """
    import xml.etree.ElementTree as ET
    cfg=HOME/".local/state/syncthing/config.xml"
    if not cfg.is_file():raise RuntimeError("Syncthing config missing")
    root=ET.parse(cfg).getroot();local=local_node_name()
    owners={local};owners.update(label for label,_ in resolved_peer_groups())
    expected={owner:((BACKUPS/owner).resolve(),"sendonly" if owner==local else "receiveonly") for owner in owners}

    # Syncthing device display names are valid Syncthing-local metadata. They are
    # not required to equal Tailscale MagicDNS and are never used for SSH routing.
    device_names={device.get("id"):device.get("name") or device.get("id") for device in root.findall("device")}
    problems=[]
    for owner,(wanted,kind) in sorted(expected.items()):
      matches=[]
      for folder in root.findall("folder"):
        try:path=Path(os.path.expanduser(folder.get("path",""))).resolve()
        except OSError:continue
        if path==wanted:matches.append(folder)
      if len(matches)!=1:
        problems.append(f"Tailscale owner {owner}: expected one Syncthing folder at {wanted}, found {len(matches)}")
        continue
      folder=matches[0];label=folder.get("label") or folder.get("id") or str(wanted)
      actual=folder.get("type","sendreceive")
      shared=[device_names.get(item.get("id"),item.get("id")) for item in folder.findall("device") if item.get("id")]
      if actual!=kind:problems.append(f"Syncthing folder {label!r} for Tailscale owner {owner}: expected {kind}, got {actual}")
      if not shared:problems.append(f"Syncthing folder {label!r} is shared with no Syncthing devices")
      if kind=="receiveonly" and folder.find("versioning") is None:
        problems.append(f"Syncthing folder {label!r} for Tailscale owner {owner}: receive-only copy has no versioning")
      wanted.mkdir(parents=True,exist_ok=True)
      if not os.access(wanted,os.R_OK|os.W_OK|os.X_OK):problems.append(f"Syncthing folder {label!r}: unusable path permissions on {wanted}")
    if problems:raise RuntimeError("Backup Syncthing audit failed for local Tailscale identity "+local+": "+"; ".join(problems))

def backup_health_path(name):return LOCAL_STATE/f"{name}.backup-health.json"
def backup_attempt_path(name):return LOCAL_STATE/f"{name}.backup-attempt.json"
def restore_journal_path(name):return LOCAL_STATE/f"{name}.restore-transaction.json"

def write_restore_phase(name,phase,**values):
    payload={"schema":1,"server":name,"phase":phase,"updated":dt.datetime.now(dt.timezone.utc).isoformat(),**values}
    write_json_atomic(restore_journal_path(name),payload)

def recover_restore_transaction(name):
    journal=restore_journal_path(name)
    if not journal.is_file():return
    try:data=json.loads(journal.read_text())
    except (OSError,ValueError) as error:raise RuntimeError(f"Invalid restore journal {journal}: {error}") from error
    expected_stage=LOCAL_STATE/f".{name}.restore-stage";expected_rollback=LOCAL_STATE/f".{name}.restore-rollback"
    if data.get("schema")!=1 or data.get("server") not in accepted_server_names(name):raise RuntimeError(f"Restore journal identity mismatch: {journal}")
    stage=Path(data.get("stage",""));rollback=Path(data.get("rollback",""));phase=data.get("phase")
    if stage!=expected_stage or rollback!=expected_rollback:raise RuntimeError(f"Restore journal contains unsafe paths: {journal}")
    snapshot=ROOT/"servers"/name/"snapshot";runtime=unit_workdir(name)
    if active(name):raise RuntimeError(f"Restore recovery required for {name}, but service is running")
    if phase not in ("committed","replicated"):
        if rollback.is_dir():replace_snapshot_from_backup(rollback,snapshot);atomic_mirror(snapshot,runtime);verify_identical(snapshot,runtime,"restore crash rollback")
        journal.unlink(missing_ok=True);fsync_directory(journal.parent)
        shutil.rmtree(stage,ignore_errors=True);shutil.rmtree(rollback,ignore_errors=True);return
    generation=data.get("generation")
    if not generation:raise RuntimeError(f"Committed restore journal lacks generation: {journal}")
    generation_data,_=read_generation(name,generation)
    record=read_handover(name)
    if not record or record.get("generation")!=generation or record.get("generation_archive_sha256")!=generation_data.get("archive_sha256"):
        raise RuntimeError(f"Committed restore metadata is inconsistent: {journal}")
    validate_handover(name,snapshot,generation);atomic_mirror(snapshot,runtime);verify_identical(snapshot,runtime,"committed restore recovery")
    if phase=="committed":
        syncthing_resume();syncthing_wait_cluster();verify_generation_on_peers(name,generation_data)
    journal.unlink(missing_ok=True);fsync_directory(journal.parent)
    shutil.rmtree(stage,ignore_errors=True);shutil.rmtree(rollback,ignore_errors=True)

def verify_backup_receiver(owner,name,generation,digest,idle_timeout=300):
    archive=BACKUPS/owner/name/"runtime"/"generations"/generation/"backup.tar.gz"
    manifest=archive.parent/"manifest.json";last=None;progress=time.monotonic()
    while True:
        signature=(archive.exists(),archive.stat().st_size if archive.exists() else 0,manifest.exists(),manifest.stat().st_size if manifest.exists() else 0)
        if signature!=last:last=signature;progress=time.monotonic()
        if archive.is_file() and manifest.is_file():
            data=json.loads(manifest.read_text())
            if data.get("archive_sha256")==digest and archive_sha256(archive)==digest:
                print(f"BACKUP-VERIFIED {owner} {name} {generation} {digest}");return
        if time.monotonic()-progress>=idle_timeout:raise RuntimeError(f"Backup replication made no progress for {idle_timeout}s: {owner}/{name}/{generation}")
        time.sleep(2)


def prune_runtime_backups(name,keep=RUNTIME_BACKUP_KEEP):
    root=runtime_backup_folder(name)/"generations"
    valid=[]
    if root.is_dir():
      for folder in root.iterdir():
        if not folder.is_dir():continue
        manifest=folder/"manifest.json";archive=folder/"backup.tar.gz"
        try:
          data=json.loads(manifest.read_text())
          if archive.is_file() and data.get("complete") and data.get("archive_sha256")==archive_sha256(archive):
            valid.append((data.get("created",""),folder));continue
        except Exception:pass
        # No incomplete or corrupt generation is a restore point. Remove it while
        # holding the per-server backup lock so failed attempts cannot accumulate.
        shutil.rmtree(folder,ignore_errors=True)
    for _,folder in sorted(valid,reverse=True)[keep:]:shutil.rmtree(folder,ignore_errors=True)
def prune_transition_backups(name,keep=TRANSITION_BACKUP_KEEP):
    root=BACKUPS/local_node_name()/name
    if not root.is_dir():return
    for kind in ("stopped-runtime","pre-handover-migration"):
        folder=root/kind
        valid=[]
        if folder.is_dir():
            for manifest in folder.glob("*/manifest.json"):
                try:
                    data=json.loads(manifest.read_text());archive=manifest.parent/"backup.tar.gz"
                    if archive.is_file() and data.get("complete") and data.get("archive_sha256")==archive_sha256(archive):
                        valid.append((data.get("created",""),manifest.parent))
                except Exception:continue
        for _,old in sorted(valid,reverse=True)[keep:]:shutil.rmtree(old,ignore_errors=True)

def prune_generations(name,keep=GENERATION_KEEP):
    root=generation_root(name)
    if not root.is_dir():return
    protected={(read_handover(name) or {}).get("generation")}
    valid=[]
    for manifest in root.glob("*/manifest.json"):
        try:
            data,archive=read_generation(name,manifest.parent.name)
            valid.append((data.get("created",""),manifest.parent))
        except Exception:continue
    retained={folder for _,folder in sorted(valid,reverse=True)[:keep]}
    retained.update(folder for _,folder in valid if folder.name in protected)
    for _,folder in valid:
        if folder not in retained:shutil.rmtree(folder,ignore_errors=True)

def prune_backup_storage(name):
    prune_runtime_backups(name)
    prune_transition_backups(name)
    prune_generations(name)

def require_backup_capacity(name):
    usage=shutil.disk_usage(BACKUPS);root=runtime_backup_folder(name)/"generations";sizes=[]
    if root.is_dir():sizes=[p.stat().st_size for p in root.glob("*/backup.tar.gz") if p.is_file()]
    required=max(10*1024**3,(max(sizes) if sizes else 512*1024**2)*2)
    if usage.free<required:raise RuntimeError(f"Insufficient backup free space: {usage.free/1024**3:.1f} GiB free, {required/1024**3:.1f} GiB required")

def runtime_backup_unit(name):
    return f"minecraft-runtime-backup-{name}.service"

def runtime_backup_folder(name):
    return BACKUPS/local_node_name()/name/"runtime"

def ensure_runtime_rcon(name,runtime):
    """Repair stale synchronized server.properties before Minecraft starts."""
    props=runtime/"server.properties"
    values={}
    order=[]
    if props.is_file():
        for raw in props.read_text(errors="replace").splitlines():
            if raw and not raw.startswith("#") and "=" in raw:
                key=raw.split("=",1)[0].strip()
                if key not in values:order.append(key)
                values[key]=raw.split("=",1)[1]
    wanted={
        "enable-rcon":"true",
        "rcon.password":f"minecraft-backup-{name}-4817",
        "rcon.port":str(port_for(name)+1000),
        "broadcast-rcon-to-ops":"false",
    }
    if all(values.get(key)==value for key,value in wanted.items()):return
    for key in wanted:
        if key not in order:order.append(key)
    values.update(wanted)
    props.parent.mkdir(parents=True,exist_ok=True)
    temporary=props.with_suffix(".properties.incoming")
    with temporary.open("w") as handle:
        for key in order:handle.write(f"{key}={values[key]}\n")
        handle.flush();os.fsync(handle.fileno())
    os.replace(temporary,props);fsync_directory(props.parent)
    print(f"Repaired stale RCON settings in {props}.",flush=True)

def rcon_settings(runtime):
    props=runtime/"server.properties"
    values={}
    if props.is_file():
        for raw in props.read_text(errors="replace").splitlines():
            raw=raw.strip()
            if raw and not raw.startswith("#") and "=" in raw:
                key,value=raw.split("=",1);values[key.strip()]=value.strip()
    if values.get("enable-rcon","").casefold()!="true":
        raise RuntimeError(f"Reliable online backup requires enable-rcon=true in {props}")
    password=values.get("rcon.password","")
    if not password:raise RuntimeError(f"Reliable online backup requires rcon.password in {props}")
    try:port=int(values.get("rcon.port","25575"))
    except ValueError:raise RuntimeError(f"Invalid rcon.port in {props}")
    return password,port

def rcon(runtime,command):
    password,port=rcon_settings(runtime)
    executable=shutil.which("mcrcon")
    if not executable:raise RuntimeError("Reliable online backup requires mcrcon in PATH")
    result=run([executable,"-H","127.0.0.1","-P",str(port),"-p",password,command],check=False,capture=True)
    if result.returncode:raise RuntimeError((result.stdout or "").strip() or f"RCON command failed: {command}")
    return (result.stdout or "").strip()

def wait_for_rcon_ready(name,runtime,idle_timeout=300):
    """Require a successful authenticated RCON command before online backup.

    The game socket can listen well before Minecraft has started its RCON
    listener. Journal growth and service state changes count as startup
    progress; repeated connection refusal by itself does not.
    """
    password,port=rcon_settings(runtime)
    executable=shutil.which("mcrcon")
    if not executable:raise RuntimeError("Reliable online backup requires mcrcon in PATH")
    cursor=None;last_state=None;last_error="RCON not attempted";last_progress=last_report=time.monotonic()
    while True:
        failure=unit_failure(name)
        if failure:raise RuntimeError(failure)
        state=run(["systemctl","show",unit(name),"-p","ActiveState","-p","SubState","-p","MainPID","--value"],check=False,capture=True).stdout.strip()
        journal=run(["journalctl","-u",unit(name),"-n","1","--show-cursor","--no-pager","-o","cat"],check=False,capture=True).stdout or ""
        match=re.search(r"-- cursor: (.+)$",journal,re.M);new_cursor=match.group(1) if match else None
        if state!=last_state or (new_cursor and new_cursor!=cursor):
            last_state=state;cursor=new_cursor;last_progress=time.monotonic()
        result=run([executable,"-H","127.0.0.1","-P",str(port),"-p",password,"list"],check=False,capture=True)
        if result.returncode==0:
            print(f"RCON ready on TCP {port}.",flush=True);return
        last_error=(result.stdout or "").strip() or f"mcrcon exited {result.returncode}"
        now=time.monotonic()
        if now-last_report>=15:
            print(f"Waiting for authenticated RCON on TCP {port}: {last_error}",flush=True);last_report=now
        if now-last_progress>=idle_timeout:
            raise RuntimeError(f"{name} made no startup progress for {idle_timeout}s while waiting for RCON TCP {port}: {last_error}")
        time.sleep(1)

def fsync_directory(path):
    descriptor=os.open(path,os.O_RDONLY|getattr(os,"O_DIRECTORY",0))
    try:os.fsync(descriptor)
    finally:os.close(descriptor)

def validate_runtime_archive(archive,name):
    with tarfile.open(archive,"r:gz") as handle:
        members=handle.getmembers()
        regular=[member for member in members if member.isfile()]
        if not regular:raise RuntimeError(f"Runtime backup contains no files: {archive}")
        roots={member.name.split("/",1)[0] for member in members if member.name}
        allowed=accepted_server_names(name)
        if len(roots)!=1 or not roots.issubset(allowed):
            raise RuntimeError(f"Runtime backup root {sorted(roots)} is not valid for {name}; accepted={sorted(allowed)}")
        archive_name=next(iter(roots));prefix=archive_name.rstrip("/")+"/"
        relative=[member.name[len(prefix):] for member in regular if member.name.startswith(prefix)]
        if not any(path=="level.dat" or re.fullmatch(r"world[^/]*/level\.dat",path) for path in relative):
            raise RuntimeError(f"Runtime backup contains no level.dat: {archive}")
        if sum(member.size for member in regular)<=0:raise RuntimeError(f"Runtime backup contains no data: {archive}")

def runtime_backup_once(name):
    LOCAL_STATE.mkdir(parents=True,exist_ok=True)
    with server_backup_lock(name).open("a+") as lock:
      acquire_file_lock(lock,f"{name} runtime backup lock");recover_save_guard(name)
      prune_runtime_backups(name)
      attempt=backup_attempt_path(name);now=time.time()
      try:last=float(json.loads(attempt.read_text()).get("started",0))
      except Exception:last=0.0
      minimum=max(30,min(RUNTIME_BACKUP_INTERVAL,RUNTIME_BACKUP_RETRY))
      elapsed=now-last
      if last and 0 <= elapsed < minimum:
        raise RuntimeError(f"Backup attempt rate-limited; last attempt began {elapsed:.1f}s ago")
      write_json_atomic(attempt,{"server":name,"started":now,"started_at":dt.datetime.now(dt.timezone.utc).isoformat()})
      require_backup_capacity(name)
      if not active(name):raise RuntimeError(f"Cannot back up {name}: service is not running")
      runtime=unit_workdir(name);folder=runtime_backup_folder(name)/"generations";folder.mkdir(parents=True,exist_ok=True)
      generation=uuid.uuid4().hex;root=folder/generation;root.mkdir();incoming=root/".backup.tar.gz.incoming";archive=root/"backup.tar.gz";guard=save_guard(name)
      try:
        write_json_atomic(guard,{"server":name,"created":dt.datetime.now(dt.timezone.utc).isoformat()});rcon(runtime,"save-off");rcon(runtime,"save-all flush")
        with incoming.open("wb") as raw:
          with tarfile.open(fileobj=raw,mode="w:gz") as handle:handle.add(runtime,arcname=name,recursive=True)
          raw.flush();os.fsync(raw.fileno())
        validate_runtime_archive(incoming,name);digest=archive_sha256(incoming);os.replace(incoming,archive);fsync_directory(root)
        data={"schema":1,"complete":True,"server":name,"peer":local_node_name(),"kind":"running","generation":generation,
              "created":dt.datetime.now(dt.timezone.utc).isoformat(),"archive":"backup.tar.gz","archive_sha256":digest,"source":str(runtime)}
        write_json_atomic(root/"manifest.json",data)
        rcon(runtime,"save-on");guard.unlink(missing_ok=True);fsync_directory(guard.parent)
        data["local_verified"]=True;data["verified_at"]=dt.datetime.now(dt.timezone.utc).isoformat()
        write_json_atomic(root/"manifest.json",data);write_json_atomic(backup_health_path(name),data)
        prune_backup_storage(name);print(f"Runtime backup protected locally: {archive}",flush=True);return archive
      except Exception:
        incoming.unlink(missing_ok=True)
        try:
          rcon(runtime,"save-on")
        except Exception as save_error:
          print(f"Could not re-enable Minecraft saving; recovery guard retained: {save_error}",file=sys.stderr,flush=True)
        else:
          guard.unlink(missing_ok=True);fsync_directory(guard.parent)
        raise

def protected_backup_age(name):
    path=backup_health_path(name)
    if not path.is_file():return float("inf")
    try:
      data=json.loads(path.read_text())
      created=data.get("verified_at") or data.get("created")
      return max(0,(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(created)).total_seconds())
    except Exception:return float("inf")

def runtime_backup_loop(name):
    # start() already creates and peer-verifies the first backup. Wait one full
    # interval before the recurring worker, preventing a duplicate startup pair.
    delay=RUNTIME_BACKUP_INTERVAL
    while active(name):
        for _ in range(delay):
            if not active(name):return
            time.sleep(1)
        recover_save_guard(name)
        try:
            runtime_backup_once(name)
            delay=RUNTIME_BACKUP_INTERVAL
        except Exception as error:
            print(f"Runtime backup failed; previous protected backup retained: {error}",file=sys.stderr,flush=True)
            delay=RUNTIME_BACKUP_RETRY

def runtime_backup_watchdog_unit(name):return f"minecraft-runtime-backup-watchdog-{name}.service"
def runtime_backup_watchdog(name):
    stale_limit=max(900,RUNTIME_BACKUP_INTERVAL*3)
    while active(name):
      if protected_backup_age(name)<stale_limit:
        time.sleep(1);continue
      should_stop=False
      with server_backup_lock(name).open("a+") as lock:
        acquire_file_lock(lock,f"{name} watchdog backup lock")
        if protected_backup_age(name)<stale_limit:continue
        runtime=unit_workdir(name)
        try:
          rcon(runtime,"save-all flush");rcon(runtime,"save-on")
          save_guard(name).unlink(missing_ok=True);fsync_directory(save_guard(name).parent)
        except Exception as error:
          print(f"Backup watchdog could not flush through RCON: {error}",file=sys.stderr,flush=True)
        should_stop=True
      # The stop command must acquire this same backup lock. Never invoke it
      # while the watchdog still owns the lock.
      if should_stop:
        print(f"No valid local runtime backup within {stale_limit}s; stopping {name} safely.",file=sys.stderr,flush=True)
        try:
          runtime=unit_workdir(name);run(["sudo","systemctl","stop",unit(name)],check=False)
          deadline=time.monotonic()+300
          while active(name) and time.monotonic()<deadline:time.sleep(1)
          if active(name):raise RuntimeError("systemd service did not stop within 300 seconds")
          archive=archive_transaction(name,runtime,"backup-failure-stop")
          if not archive:raise RuntimeError("stopped runtime was empty")
          print(f"Emergency local stop backup created: {archive}",file=sys.stderr,flush=True)
        except Exception as error:
          print(f"Emergency local stop could not complete a final archive: {error}",file=sys.stderr,flush=True)
          run(["sudo","systemctl","stop",unit(name)],check=False)
        return
      time.sleep(1)

def start_runtime_backup_loop(name):
    stop_runtime_backup_loop(name)
    command=[str(HOME/".config/sway/scripts/systemd-userctl.sh"),"--systemd-run","--unit",runtime_backup_unit(name),"--property=Restart=on-failure","--property=RestartSec=60s",
             "--collect",sys.executable,"-B",str(Path(__file__).resolve()),"runtime-backup-loop",name]
    result=run(command,check=False,capture=True)
    if result.returncode:raise RuntimeError(result.stdout.strip() or "Could not start supervised runtime backup loop")
    watchdog=[str(HOME/".config/sway/scripts/systemd-userctl.sh"),"--systemd-run","--unit",runtime_backup_watchdog_unit(name),"--property=Restart=on-failure","--property=RestartSec=5s",
              "--collect",sys.executable,"-B",str(Path(__file__).resolve()),"runtime-backup-watchdog",name]
    result=run(watchdog,check=False,capture=True)
    if result.returncode:
      run([str(HOME/".config/sway/scripts/systemd-userctl.sh"),"stop",runtime_backup_unit(name)],check=False);raise RuntimeError(result.stdout.strip() or "Could not start backup watchdog")

def stop_runtime_backup_loop(name):
    run([str(HOME/".config/sway/scripts/systemd-userctl.sh"),"stop",runtime_backup_watchdog_unit(name)],check=False,capture=True)
    run([str(HOME/".config/sway/scripts/systemd-userctl.sh"),"stop",runtime_backup_unit(name)],check=False,capture=True)

def generation_root(name):return ROOT/"generations"/name

def generation_path(name,generation):return generation_root(name)/generation


def write_json_atomic(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    incoming=path.with_name("."+path.name+f".incoming-{os.getpid()}")
    with incoming.open("w") as handle:
        json.dump(payload,handle,sort_keys=True);handle.write("\n");handle.flush();os.fsync(handle.fileno())
    os.replace(incoming,path);fsync_directory(path.parent)

def archive_sha256(path):
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""):digest.update(chunk)
    return digest.hexdigest()

def publish_generation(name,runtime,reason):
    generation=uuid.uuid4().hex
    root=generation_path(name,generation);root.mkdir(parents=True,exist_ok=False)
    incoming=root/".snapshot.tar.gz.incoming";archive=root/"snapshot.tar.gz";manifest=root/"manifest.json"
    try:
        with incoming.open("wb") as raw:
            with tarfile.open(fileobj=raw,mode="w:gz") as tar:tar.add(runtime,arcname=name,recursive=True)
            raw.flush();os.fsync(raw.fileno())
        validate_runtime_archive(incoming,name)
        digest=archive_sha256(incoming);runtime_digest=manifest_digest(runtime)
        os.replace(incoming,archive);fsync_directory(root)
        payload={"schema":1,"complete":True,"server":name,"generation":generation,
                 "publisher":local_node_name(),"created":dt.datetime.now(dt.timezone.utc).isoformat(),
                 "reason":reason,"archive":"snapshot.tar.gz","archive_sha256":digest,
                 "runtime_manifest_sha256":runtime_digest}
        write_json_atomic(manifest,payload)
        return payload
    except Exception:
        incoming.unlink(missing_ok=True)
        # An incomplete generation has no manifest and is never selectable.
        raise

def read_generation(name,generation):
    root=generation_path(name,generation);manifest=root/"manifest.json";archive=root/"snapshot.tar.gz"
    if not manifest.is_file() or not archive.is_file():raise RuntimeError(f"Generation is incomplete: {name}/{generation}")
    data=json.loads(manifest.read_text())
    if not data.get("complete") or data.get("generation")!=generation or data.get("server") not in accepted_server_names(name):
        raise RuntimeError(f"Invalid generation manifest: {manifest}")
    actual=archive_sha256(archive)
    if actual!=data.get("archive_sha256"):raise RuntimeError(f"Generation archive checksum mismatch: {archive}")
    return data,archive

def wait_for_generation(name,generation,expected_digest=None,idle_timeout=300):
    root=generation_path(name,generation);archive=root/"snapshot.tar.gz";manifest=root/"manifest.json"
    last=None;last_progress=time.monotonic();last_error=None
    while True:
        syncthing_request("/rest/db/scan",method="POST").read()
        status=json.load(syncthing_request("/rest/db/status"))
        signature=(archive.exists(),archive.stat().st_size if archive.exists() else 0,
                   manifest.exists(),manifest.stat().st_size if manifest.exists() else 0,
                   status.get("state"),int(status.get("needTotalItems",0) or 0),int(status.get("needBytes",0) or 0))
        if signature!=last:last=signature;last_progress=time.monotonic()
        if archive.is_file() and manifest.is_file():
            try:
                data,found=read_generation(name,generation)
                if expected_digest and data.get("archive_sha256")!=expected_digest:
                    raise RuntimeError(f"Generation digest differs from handover: {name}/{generation}")
                return data,found
            except (OSError,ValueError,tarfile.TarError,RuntimeError) as error:last_error=error
        if time.monotonic()-last_progress>=idle_timeout:
            raise RuntimeError(f"Generation {name}/{generation} made no observable Syncthing/file progress for {idle_timeout}s; last_error={last_error}")
        time.sleep(2)

def activate_generation(name,generation,snapshot,expected_digest=None):
    data,archive=wait_for_generation(name,generation,expected_digest)
    parent=snapshot.parent;extract=parent/f".{snapshot.name}.generation-{generation}.incoming"
    shutil.rmtree(extract,ignore_errors=True)
    try:
        # A generation can legitimately retain the previous top-level server
        # name after a transactional rename. safe_extract_backup validates that
        # root against accepted_server_names(name) and returns the actual tree.
        restored=safe_extract_backup(archive,extract,name)
        if not restored.is_dir():raise RuntimeError(f"Generation archive has no usable server tree for {name}")
        if manifest_digest(restored)!=data.get("runtime_manifest_sha256"):
            raise RuntimeError(f"Extracted generation manifest mismatch: {name}/{generation}")
        replace_snapshot_from_backup(restored,snapshot)
        write_json_atomic(active_generation_path(name),data)
        return data
    finally:shutil.rmtree(extract,ignore_errors=True)


def ensure_syncthing_versioning():
    item=syncthing_config()
    if not item:return
    key,folder,_=item;headers={"X-API-Key":key,"Content-Type":"application/json"}
    url="http://127.0.0.1:8384/rest/config/folders/"+parse.quote(folder,safe="")
    current=json.load(request.urlopen(request.Request(url,headers={"X-API-Key":key}),timeout=300))
    versioning=current.get("versioning") or {}
    wanted={"type":"staggered","params":{"cleanInterval":"3600","maxAge":"604800"},"cleanupIntervalS":3600}
    if versioning.get("type")==wanted["type"] and versioning.get("params")==wanted["params"] and int(versioning.get("cleanupIntervalS",0) or 0)==3600:return
    payload=json.dumps({"versioning":wanted}).encode()
    request.urlopen(request.Request(url,data=payload,method="PATCH",headers=headers),timeout=300).read()

def server_paths(name):return unit_workdir(name),ROOT/"servers"/name/"snapshot",BACKUPS/local_node_name()/name,COORD/f"{name}.owner.json"
def server_backup_lock(name):return LOCAL_STATE/f"{name}.backup.lock"
def save_guard(name):return LOCAL_STATE/f"{name}.save-disabled"
def backup(name,runtime):
    return archive_transaction(name,runtime,"stopped-runtime")
def unit_workdir(name):
    result=run(["systemctl","show",unit(name),"-p","WorkingDirectory","--value"],check=False,capture=True)
    value=result.stdout.strip()
    return Path(value) if value.startswith("/") else RUNTIME_ROOT/name

def port_for(name):
    # nix-minecraft writes server.properties in the unit WorkingDirectory.
    # Query systemd instead of assuming the runtime layout, then fall back to
    # the configured ports used by this installation while the first start is
    # still creating the file.
    candidates=[unit_workdir(name)/"server.properties",RUNTIME_ROOT/name/"server.properties"]
    for prop in candidates:
        if not prop.exists():continue
        for line in prop.read_text(errors="replace").splitlines():
            if line.startswith("server-port="):
                try:return int(line.split("=",1)[1].strip())
                except ValueError:pass
    # First start: derive the declared port from minecraft.nix so renames and
    # port edits remain declarative and never require another hardcoded map.
    nix_file=Path(os.environ.get("MINECRAFT_NIX", str(Path.home()/"nixos/rt4817/minecraft.nix")))
    if nix_file.is_file():
        text=nix_file.read_text(errors="replace")
        block=re.search(r"\{\s*name\s*=\s*\""+re.escape(name)+r"\";(?P<body>.*?)\n\s*\}",text,re.S)
        if block:
            match=re.search(r"\bport\s*=\s*([0-9]+)\s*;",block.group("body"))
            if match:return int(match.group(1))
    raise RuntimeError(f"Cannot determine TCP port for {name}; server.properties is absent and minecraft.nix has no matching declaration")

def port_busy(port):
    with socket.socket() as s:
        s.settimeout(.3)
        return s.connect_ex(("127.0.0.1",port))==0


def unit_failure(name):
    state=run(["systemctl","show",unit(name),"-p","ActiveState","-p","SubState","-p","Result","--value"],check=False,capture=True).stdout.splitlines()
    active_state=state[0].strip() if len(state)>0 else "unknown"
    sub_state=state[1].strip() if len(state)>1 else "unknown"
    result=state[2].strip() if len(state)>2 else "unknown"
    if active_state=="failed" or result not in ("","success"):
        log=run(["journalctl","-u",unit(name),"-n","120","--no-pager"],check=False,capture=True).stdout.strip()
        return f"{unit(name)} failed ({active_state}/{sub_state}, result={result})\n{log}".strip()
    return None

def local_node_name():
    # Resolve this machine's identity from Tailscale, not a hardcoded hostname.
    try:
        data=json.loads(run(["tailscale","status","--json"],capture=True,timeout=10).stdout or "{}")
        dns=((data.get("Self") or {}).get("DNSName") or "").rstrip(".")
        if dns:return dns.split(".",1)[0]
    except Exception as error:
        raise RuntimeError(f"Cannot determine this device's Tailscale MagicDNS identity: {error}")
    raise RuntimeError("This device has no Tailscale MagicDNS identity")

@contextlib.contextmanager
def cluster_transition_lock(remote_action=False):
    """One cross-laptop mutex, hosted by the lowest live MagicDNS name."""
    if remote_action:
        yield
        return
    validate_peer_identities()
    local=local_node_name()
    groups=resolved_peer_groups()
    labels=[label for label,addresses in groups]
    coordinator=min([local]+labels)
    if coordinator==local:
        path=COORD/"cluster.lock"
        with path.open("a+") as handle:
            acquire_file_lock(handle,f"the active Minecraft cluster transition through {coordinator} to finish")
            try:yield
            finally:fcntl.flock(handle,fcntl.LOCK_UN)
        return
    addresses=next((addresses for label,addresses in groups if label==coordinator),None)
    if not addresses:raise RuntimeError(f"No SSH address for Minecraft coordinator {coordinator}")
    process=None;failure=[]
    for address in addresses:
      command=["ssh","-i",str(HOME/".ssh/minecraft_peers"),"-o","IdentitiesOnly=yes","-o","StrictHostKeyChecking=accept-new","-o","BatchMode=yes","-o","ConnectTimeout=4",address,
               "mkdir -p ~/minecraft-server/coordination && exec flock ~/minecraft-server/coordination/cluster.lock sh -c 'echo LOCKED; cat >/dev/null'"]
      try:
          candidate=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
          line=candidate.stdout.readline().strip()
      except OSError as error:
          failure.append(f"{address}: {error}");continue
      if line=="LOCKED":process=candidate;break
      try:output=line+candidate.communicate(timeout=5)[0]
      except subprocess.TimeoutExpired:
          candidate.terminate();output=line+candidate.communicate(timeout=5)[0]
      failure.append(f"{address}: {output.strip() or 'cluster lock busy'}")
    if process is None:
        raise RuntimeError("Minecraft cluster transition is busy or coordinator is unavailable: "+"; ".join(failure))
    try:yield
    finally:
        try:process.stdin.close()
        except Exception:pass
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.terminate()

def tree_manifest(root):
    root=Path(root)
    if not root.is_dir():raise RuntimeError(f"Missing tree: {root}")
    result={}
    for path in sorted(root.rglob("*"),key=lambda item:item.as_posix()):
        rel=path.relative_to(root).as_posix()
        if path.is_symlink():result[rel]=("link",os.readlink(path));continue
        if path.is_dir():continue
        h=hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda:f.read(1024*1024),b""):h.update(block)
        result[rel]=("file",path.stat().st_size,h.hexdigest())
    return result

def verify_identical(source,destination,label):
    left=tree_manifest(source);right=tree_manifest(destination)
    if left!=right:
        missing=sorted(set(left)-set(right))[:5];extra=sorted(set(right)-set(left))[:5]
        changed=sorted(k for k in set(left)&set(right) if left[k]!=right[k])[:5]
        raise RuntimeError(f"{label} verification failed; missing={missing}, extra={extra}, changed={changed}")

def manifest_digest(root):
    payload=json.dumps(tree_manifest(root),sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()

def handover_path(name):return ROOT/"servers"/name/"handover.json"

def read_handover(name):
    marker=handover_path(name)
    if not marker.is_file():return None
    try:return json.loads(marker.read_text())
    except (OSError,ValueError) as error:raise RuntimeError(f"Invalid handover marker {marker}: {error}")

def write_handover(name,snapshot,reason):
    marker=handover_path(name);marker.parent.mkdir(parents=True,exist_ok=True)
    record={"schema":1,"server":name,"generation":os.urandom(16).hex(),
            "publisher":local_node_name(),"reason":reason,
            "created":dt.datetime.now(dt.timezone.utc).isoformat(),
            "snapshot_sha256":manifest_digest(snapshot)}
    temporary=marker.with_suffix(".json.tmp")
    with temporary.open("w") as handle:
        json.dump(record,handle,sort_keys=True);handle.write("\n");handle.flush();os.fsync(handle.fileno())
    os.replace(temporary,marker);fsync_directory(marker.parent);return record

def validate_handover(name,snapshot,expected_generation=None):
    record=read_handover(name)
    if not record:raise RuntimeError(f"Missing synchronized handover marker for {name}")
    if record.get("schema")!=1 or record.get("server")!=name:raise RuntimeError("Invalid handover identity")
    if expected_generation and record.get("generation")!=expected_generation:
        raise RuntimeError("Received a different handover generation than the remote stop published")
    if record.get("snapshot_sha256")!=manifest_digest(snapshot):
        raise RuntimeError("Snapshot content does not match its handover digest")
    return record


def replace_snapshot_from_backup(restored,snapshot):
    incoming=snapshot.with_name(snapshot.name+".restore-incoming")
    old=snapshot.with_name(snapshot.name+".restore-old")
    # Recover a crash that occurred after snapshot -> old but before incoming -> snapshot.
    if not snapshot.exists() and old.exists():old.rename(snapshot)
    shutil.rmtree(incoming,ignore_errors=True)
    if old.exists() and snapshot.exists():shutil.rmtree(old,ignore_errors=True)
    shutil.copytree(restored,incoming,symlinks=True)
    fsync_directory(incoming.parent)
    if snapshot.exists():snapshot.rename(old);fsync_directory(snapshot.parent)
    try:incoming.rename(snapshot);fsync_directory(snapshot.parent)
    except Exception:
        if old.exists() and not snapshot.exists():old.rename(snapshot);fsync_directory(snapshot.parent)
        raise
    # Old active tree is deleted only after the new tree is durably named and the
    # immutable generation/safety backup already exists.
    shutil.rmtree(old,ignore_errors=True);fsync_directory(snapshot.parent)



def safety_backup(name,source,kind):
    path=archive_transaction(name,source,kind)
    if path:print(f"Safety backup created: {path}")
    return path

def ensure_syncthing_resumed_and_current(cluster=False):
    if running_servers():raise RuntimeError("Refusing to resume Syncthing while Minecraft is running")
    syncthing_resume()
    (syncthing_wait_cluster if cluster else syncthing_wait_local)()

def prepare_sync_receiver():
    """Put a stopped peer into a verified receiving state for handover."""
    if running_servers():raise RuntimeError("Cannot prepare sync while a local Minecraft server is running")
    ensure_dependencies()
    ensure_syncthing_resumed_and_current(cluster=False)
    print("Minecraft Syncthing receiver is resumed and locally current.")
    return 0

def start(name=None):
    global CRITICAL
    name=select_server(name,"start");ensure_dependencies(name)
    local=running_servers()
    _,_,_,local_owner=server_paths(name)
    if local:
        if name in local:print(f"{name} is already running locally.");return
        raise RuntimeError("Another local Minecraft server is already running: "+", ".join(local))
    if local_owner.exists():
        raise RuntimeError(f"{name} stopped unexpectedly while this laptop still owns its runtime. The runtime and latest local backup were preserved. Choose Stop for {name} to publish a clean handover before starting it again.")
    if not peer_groups():raise RuntimeError(f"No peers configured in {PEERS}; refusing an uncoordinated start")
    # Resume the receiving folder before requesting a remote stop. Otherwise the
    # publisher and receiver can wait on one another forever.
    ensure_syncthing_resumed_and_current(cluster=False)
    found,unreachable=remote_state()
    if unreachable:raise RuntimeError("Cannot verify peer(s): "+", ".join(unreachable))
    expected={}
    for label,address,names in found:
        remote_servers=[item for item in names if not item.startswith("floating=") and item!="transitioning"]
        if "transitioning" in names:raise RuntimeError(f"Peer transition already in progress on {label}")
        for remote_name in remote_servers:
            old=(read_handover(remote_name) or {}).get("generation")
            print(f"Stopping remote server {remote_name} on {label}...")
            r=ssh(address,["stop",remote_name,"--remote"])
            if r.returncode:raise RuntimeError((r.stdout or "").strip() or "remote stop failed")
            match=re.search(r"HANDOVER ([0-9a-f]{32})",r.stdout or "")
            generation=match.group(1) if match else None
            expected[remote_name]=(old,generation)
        if remote_servers:
            last_text=None;last_progress=time.monotonic();idle_timeout=300
            while True:
                _,_,text,_=query_group([address],["status","--machine"])
                if text=="stopped":break
                if text!=last_text:last_text=text;last_progress=time.monotonic()
                elif time.monotonic()-last_progress>=idle_timeout:
                    raise RuntimeError(f"Peer {label} made no stop/handover progress for {idle_timeout}s; status={text!r}")
                time.sleep(1)
    # The initial clean wait above covers an ordinary local start. A remote stop
    # does not return until it has resumed this peer, completed cluster sync and
    # verified the immutable generation here. Do one final bounded local check
    # only when a remote handover actually occurred; repeatedly nesting full
    # Syncthing-settle waits made normal starts appear hung.
    old,wanted=expected.get(name,((read_handover(name) or {}).get("generation"),None))
    if name in expected:
        print("Verifying the received handover snapshot...")
        syncthing_wait_local(idle_timeout=120,stable_seconds=2)
    runtime,snapshot,_,owner=server_paths(name)
    snapshot.mkdir(parents=True,exist_ok=True)
    if not any(snapshot.iterdir()):
        if handover_path(name).is_file():
            raise RuntimeError(f"Snapshot is empty but has a handover marker: {snapshot}")
        print(f"Initializing first-run snapshot for {name}.")
        # Preserve declarative files already materialized by nix-minecraft.
        # Publishing an empty snapshot and then rsync --delete would otherwise
        # erase those inputs immediately before the first service start.
        if runtime.is_dir() and any(runtime.iterdir()):
            atomic_mirror(runtime,snapshot)
            verify_identical(runtime,snapshot,"first-run snapshot")
        write_handover(name,snapshot,"first-run-bootstrap")
    elif not handover_path(name).is_file():
        safety_backup(name,snapshot,"pre-handover-migration");write_handover(name,snapshot,"format-migration")
        syncthing_wait_local(idle_timeout=120,stable_seconds=2)
    record=read_handover(name)
    if not record:raise RuntimeError(f"Missing synchronized handover marker for {name}")
    if wanted and record.get("generation")!=wanted:raise RuntimeError("Received a different handover generation")
    generation=record.get("generation")
    generation_digest=record.get("generation_archive_sha256")
    try:
        record=validate_handover(name,snapshot,wanted)
    except RuntimeError as error:
        # The synchronized snapshot is normally already exact. Extract the
        # immutable generation only as corruption/conflict recovery, not on
        # every ordinary start.
        if not (generation and generation_digest):raise
        print(f"Snapshot verification failed ({error}); recovering immutable generation...")
        activate_generation(name,generation,snapshot,generation_digest)
        record=validate_handover(name,snapshot,wanted)
    print("Pausing Syncthing before acquiring runtime ownership...");syncthing_pause();CRITICAL=True
    try:
        paused=validate_handover(name,snapshot,wanted)
        if paused.get("generation")!=record.get("generation"):raise RuntimeError("Handover changed while acquiring ownership")
        atomic_mirror(snapshot,runtime);verify_identical(snapshot,runtime,"pre-start runtime")
        ensure_runtime_rcon(name,runtime)
        network_prepare();owner.parent.mkdir(parents=True,exist_ok=True)
        write_json_atomic(owner,{"server":name,"host":local_node_name(),"started":dt.datetime.now(dt.timezone.utc).isoformat()})
        run(["sudo","systemctl","reset-failed",unit(name)],check=False,capture=True)
        r=run(["sudo","systemctl","start",unit(name)],check=False,capture=True)
        if r.returncode:raise RuntimeError(r.stdout.strip() or f"Failed to start {unit(name)}")
        port=port_for(name);print(f"Waiting for '{name}' to listen on TCP {port}...")
        last_signature=None;last_progress=time.monotonic();idle_timeout=300
        while True:
            failure=unit_failure(name)
            if failure:raise RuntimeError(failure)
            is_active=active(name);is_busy=port_busy(port);signature=(is_active,is_busy)
            if signature!=last_signature:last_signature=signature;last_progress=time.monotonic()
            if is_active and is_busy:
                domain=ddns_update();public=public_ipv4()
                print(f"Minecraft server '{name}' started on port {port}")
                print(f"Router target: {FLOATING_IP}:{port}")
                if public:print(f"Public address: {public}:{port}")
                if domain:print(f"Friend address: {domain}.duckdns.org:{port}")
                wait_for_rcon_ready(name,runtime)
                runtime_backup_once(name)
                start_runtime_backup_loop(name)
                CRITICAL=False
                print(f"Live local runtime backup: protected immediately, then every {RUNTIME_BACKUP_INTERVAL//60} minutes.")
                print("Syncthing is paused until the server is stopped and snapshotted.");return
            if time.monotonic()-last_progress>=idle_timeout:
                raise RuntimeError(f"{name} made no startup progress for {idle_timeout}s (active={is_active}, port_busy={is_busy})")
            time.sleep(1)
    except Exception:
        run(["sudo","systemctl","stop",unit(name)],check=False)
        try:owner.unlink()
        except FileNotFoundError:pass
        network_restore();CRITICAL=False
        if not running_servers():
            try:ensure_syncthing_resumed_and_current()
            except Exception as error:print(f"Could not restore Syncthing after failed start: {error}",file=sys.stderr)
        raise

def stop(name=None,remote=False):
    global CRITICAL
    name=select_server(name,"stop")
    runtime,snapshot,_,owner=server_paths(name)
    if not active(name) and not owner.exists():print(f"{name} is already stopped locally.");return
    if not active(name):
        print(f"{name} is stopped but still has a local ownership record; finalizing its interrupted handover.")
    ensure_dependencies(name);CRITICAL=False
    LOCAL_STATE.mkdir(parents=True,exist_ok=True)
    print("Stopping live-backup workers and waiting for any active backup to finish...")
    stop_runtime_backup_loop(name)
    with server_backup_lock(name).open("a+") as backup_lock:
      acquire_file_lock(backup_lock,f"the active {name} backup to finish")
      CRITICAL=True
      print("Pausing only this peer's Minecraft Syncthing folder while stopping and snapshotting...");syncthing_pause()
      try:
        recover_save_guard(name);run(["sudo","systemctl","stop",unit(name)])
        last_state=None;last_progress=time.monotonic()
        while active(name):
          state=run(["systemctl","show",unit(name),"-p","ActiveState","-p","SubState","--value"],check=False,capture=True).stdout.strip()
          if state!=last_state:last_state=state;last_progress=time.monotonic()
          elif time.monotonic()-last_progress>=300:raise RuntimeError(f"{name} made no shutdown progress for 300s; state={state!r}")
          time.sleep(1)
        stopped_archive=backup(name,runtime)
        if not stopped_archive:raise RuntimeError(f"Refusing handover: stopped runtime for {name} is empty")
        generation=publish_generation(name,runtime,"clean-stop");atomic_mirror(runtime,snapshot);verify_identical(runtime,snapshot,"stopped snapshot")
        record=write_handover(name,snapshot,"clean-stop");record.update({"generation":generation["generation"],"generation_archive_sha256":generation["archive_sha256"],"publisher":generation["publisher"]})
        write_json_atomic(handover_path(name),record)
        if not running_servers():network_restore()
        owner.unlink(missing_ok=True);fsync_directory(owner.parent);ensure_syncthing_versioning();syncthing_resume();syncthing_wait_cluster();verify_generation_on_peers(name,generation)
        prune_backup_storage(name);CRITICAL=False;print(f"HANDOVER {record['generation']}");print(f"'{name}' stopped, backed up, verified, and replicated.")
      except Exception:
        CRITICAL=False
        try:syncthing_resume()
        except Exception as resume_error:print(f"Additionally failed to resume Syncthing: {resume_error}",file=sys.stderr,flush=True)
        raise

@contextlib.contextmanager
def recovery_output_to_stderr():
    """Reserve stdout for machine-readable command results, including child output."""
    sys.stdout.flush();saved_stdout=os.dup(1)
    try:
        os.dup2(2,1)
        with contextlib.redirect_stdout(sys.stderr):yield
    finally:
        sys.stdout.flush();os.dup2(saved_stdout,1);os.close(saved_stdout)


def recover_incomplete_operations():
    """Finish durable Minecraft transactions before accepting a new command.

    Recovery diagnostics and lower-level command output are always sent to stderr.
    Commands such as ``list`` and ``status --machine`` reserve stdout for their
    parseable result and must never acquire a fake server name from recovery text.
    """
    global RECOVERING
    if RECOVERING:return
    RECOVERING=True
    try:
      with recovery_output_to_stderr():
        names=available_servers()
        for name in names:
            LOCAL_STATE.mkdir(parents=True,exist_ok=True)
            if save_guard(name).exists():
                print(f"Recovering interrupted save guard for {name}...",flush=True)
                recover_save_guard(name)
            if restore_journal_path(name).is_file():
                print(f"Recovering interrupted restore for {name}...",flush=True)
                recover_restore_transaction(name)
            owner=server_paths(name)[3]
            if owner.exists() and not active(name):
                print(f"{name} is stopped with local ownership preserved; no automatic handover was performed.",flush=True)
    finally:
        RECOVERING=False

def backup_choices(name):
    choices=[]
    if BACKUPS.is_dir():
      for archive in BACKUPS.glob(f"*/{name}/**/*.tar.gz"):
        if validated_backup(archive,name):choices.append(archive)
    root=generation_root(name)
    if root.is_dir():
      for manifest in root.glob("*/manifest.json"):
        try:_,archive=read_generation(name,manifest.parent.name);choices.append(archive)
        except Exception:continue
    return sorted(set(choices),key=lambda item:item.stat().st_mtime,reverse=True)

def select_backup(name,requested=None):
    choices=backup_choices(name)
    if requested:
        candidate=Path(requested).expanduser().resolve()
        if candidate not in [item.resolve() for item in choices]:
            raise RuntimeError(f"Backup/generation is not a validated restore point for {name}: {candidate}")
        return candidate
    if not choices:raise RuntimeError(f"No validated backups or generations exist for {name}")
    if not sys.stdin.isatty():raise RuntimeError("Specify --backup when restore is non-interactive")
    print(f"Choose a backup or immutable generation to restore for {name}:")
    print("  [E] Exit")
    for index,item in enumerate(choices,1):
        stamp=dt.datetime.fromtimestamp(item.stat().st_mtime).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        try:label=str(item.relative_to(ROOT))
        except ValueError:label=str(item)
        print(f"  [{index}] {label}  ({stamp}, {item.stat().st_size/1024/1024:.1f} MiB)")
    while True:
        answer=input("Selection: ").strip()
        if answer.casefold()=="e":raise SystemExit(0)
        if answer.isdigit() and 1<=int(answer)<=len(choices):return choices[int(answer)-1]
        print("Invalid selection.")

def safe_extract_backup(archive,destination,name):
    destination.mkdir(parents=True,exist_ok=False)
    root=destination.resolve()
    with tarfile.open(archive,"r:gz") as handle:
        members=handle.getmembers()
        if not members:raise RuntimeError(f"Backup is empty: {archive}")
        for member in members:
            target=(destination/member.name).resolve()
            if target!=root and root not in target.parents:
                raise RuntimeError(f"Unsafe path in backup: {member.name}")
            if member.issym() or member.islnk():
                link=(target.parent/member.linkname).resolve()
                if link!=root and root not in link.parents:
                    raise RuntimeError(f"Unsafe link in backup: {member.name} -> {member.linkname}")
        handle.extractall(destination,filter="data")
    preferred=destination/name
    if preferred.is_dir():return preferred
    top=[item for item in destination.iterdir() if item.is_dir()]
    if len(top)==1:return top[0]
    raise RuntimeError(f"Backup does not contain one {name} server directory")

def restore_backup(name=None,requested=None):
    global CRITICAL
    name=select_server(name,"restore");archive=select_backup(name,requested)
    if sys.stdin.isatty() and input(f"Restore {name} from {archive.name}? [y/N]: ").strip().casefold() not in ("y","yes"):raise SystemExit(0)
    ensure_dependencies(name)
    found,unreachable=remote_state()
    if unreachable:raise RuntimeError("Cannot verify peer(s): "+", ".join(unreachable))
    # Restore only the server selected by the user. Stop that server through
    # the normal lifecycle so RCON save, backup, generation and handover all run.
    # Never silently stop an unrelated server.
    local_running=running_servers()
    unrelated=[x for x in local_running if x!=name]
    remote_selected=[];remote_unrelated=[]
    for _,address,names in found:
      clean=[x for x in names if not x.startswith("floating=") and x!="transitioning"]
      if name in clean:remote_selected.append(address)
      remote_unrelated.extend((address,x) for x in clean if x!=name)
    if unrelated or remote_unrelated:
      details=[f"local:{x}" for x in unrelated]+[f"{address}:{x}" for address,x in remote_unrelated]
      raise RuntimeError("Refusing restore while another server is running: "+", ".join(details))
    for address in remote_selected:
      result=ssh(address,["stop",name,"--remote"])
      if result.returncode:raise RuntimeError((result.stdout or "").strip() or f"remote stop failed for {name}")
    stop(name)
    # A selected running server is stopped through the full verified handover.
    # Only now is it safe to require a resumed, current receive folder.
    ensure_syncthing_resumed_and_current(cluster=False)
    runtime,snapshot,_,owner=server_paths(name);stage=LOCAL_STATE/f".{name}.restore-stage";rollback=LOCAL_STATE/f".{name}.restore-rollback";CRITICAL=True
    LOCAL_STATE.mkdir(parents=True,exist_ok=True)
    with server_backup_lock(name).open("a+") as backup_lock:
      acquire_file_lock(backup_lock,f"the active {name} backup to finish before restore")
      try:
        syncthing_pause();write_restore_phase(name,"prepared",stage=str(stage),rollback=str(rollback),archive=str(archive))
        shutil.rmtree(stage,ignore_errors=True);shutil.rmtree(rollback,ignore_errors=True)
        if snapshot.is_dir():shutil.copytree(snapshot,rollback,symlinks=True)
        write_restore_phase(name,"staged",stage=str(stage),rollback=str(rollback),archive=str(archive))
        restored=safe_extract_backup(archive,stage,name)
        replace_snapshot_from_backup(restored,snapshot);atomic_mirror(snapshot,runtime);verify_identical(snapshot,runtime,"restored runtime")
        write_restore_phase(name,"mutated",stage=str(stage),rollback=str(rollback),archive=str(archive))
        generation=publish_generation(name,runtime,"backup-restore")
        record=write_handover(name,snapshot,"backup-restore");record.update({"generation":generation["generation"],"generation_archive_sha256":generation["archive_sha256"],"publisher":generation["publisher"]});write_json_atomic(handover_path(name),record)
        write_restore_phase(name,"committed",stage=str(stage),rollback=str(rollback),generation=generation["generation"])
        owner.unlink(missing_ok=True);fsync_directory(owner.parent);network_restore();syncthing_resume();syncthing_wait_cluster();verify_generation_on_peers(name,generation)
        write_restore_phase(name,"replicated",stage=str(stage),rollback=str(rollback),generation=generation["generation"]);prune_backup_storage(name)
      except Exception:
        phase="unknown"
        try:
          if restore_journal_path(name).is_file():phase=json.loads(restore_journal_path(name).read_text()).get("phase","unknown")
        except Exception:pass
        if phase not in ("committed","replicated"):
          rollback_ok=False
          try:
            if rollback.is_dir():replace_snapshot_from_backup(rollback,snapshot);atomic_mirror(snapshot,runtime);verify_identical(snapshot,runtime,"restore rollback");rollback_ok=True
          finally:
            if rollback_ok:restore_journal_path(name).unlink(missing_ok=True);fsync_directory(restore_journal_path(name).parent)
        else:
          print(f"Restore committed locally; preserving generation and recovery journal because peer replication is incomplete (phase={phase}).",file=sys.stderr,flush=True)
        raise
      else:
        restore_journal_path(name).unlink(missing_ok=True)
      finally:
        if not restore_journal_path(name).exists():
          shutil.rmtree(stage,ignore_errors=True);shutil.rmtree(rollback,ignore_errors=True)
        try:syncthing_resume()
        except Exception:pass
        CRITICAL=False
    print(f"Restored '{name}' from {archive.name}; immutable generation verified on peers; server remains stopped.")

def status(machine=False):
    running=running_servers()
    if machine:
        suffix=f" floating={FLOATING_IP}" if floating_present() else ""
        print(("running "+" ".join(running) if running else "stopped")+suffix);return 0
    names=available_servers();print("Available servers: "+(", ".join(names) or "none"));print("Running locally: "+(", ".join(running) or "none"))
    found,unreachable=remote_state()
    for label,address,servers in found:print(f"Remote {label} via {address}: {', '.join(servers)}")
    if unreachable:print("Unreachable peers: "+", ".join(unreachable))
    return 0 if running else 3
def machine_status():
    # A separate status process must report that start/stop currently owns the
    # lock. Reporting plain "stopped" here allowed both laptops to start at once.
    with LOCK.open("a+") as probe:
        try:fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print("transitioning");return 0
        try:return status(True)
        finally:fcntl.flock(probe,fcntl.LOCK_UN)

def on_signal(sig,frame):
    if CRITICAL:print("\nCritical save/transfer continues; interrupt ignored.",file=sys.stderr);return
    raise KeyboardInterrupt
def interactive_menu():
    print("Minecraft server manager\n\n[E] Exit\n[1] Start a server\n[2] Stop a running/local-owned server\n[3] Status\n[4] Logs")
    while True:
        choice=input("\nSelection: ").strip().casefold()
        if choice=="e":raise SystemExit(0)
        if choice=="1":return "start",select_server(None,"start")
        if choice=="2":return "stop",select_server(None,"stop")
        if choice=="3":return "status",None
        if choice=="4":return "logs",select_server(None,"view logs for")
        print("Invalid selection.")

def main():
    p=argparse.ArgumentParser();p.add_argument("action",nargs="?",default="menu",choices=["menu","toggle","start","stop","restore","status","logs","list","release-network","menu-status","prepare-sync","prune-storage","runtime-backup-once","runtime-backup-loop","runtime-backup-watchdog","verify-generation","verify-backup","syncthing-id","recover"]);p.add_argument("server",nargs="?");p.add_argument("--machine",action="store_true");p.add_argument("--remote",action="store_true");p.add_argument("--backup");p.add_argument("--generation");p.add_argument("--digest");p.add_argument("--owner");a=p.parse_args()
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):signal.signal(sig,on_signal)
    if a.action=="menu":
        if not sys.stdin.isatty():raise RuntimeError("Interactive menu requires a terminal")
        a.action,a.server=interactive_menu()
    for d in (ROOT,RUNTIME_ROOT,BACKUPS,COORD,LOCAL_STATE):d.mkdir(parents=True,exist_ok=True)
    # Machine status is a read-only peer probe. It must remain available while
    # start/stop owns the transition lock; otherwise a normal handover is
    # misreported as an unreachable peer.
    if a.action=="status" and a.machine:return machine_status()
    if a.action=="syncthing-id":print(syncthing_device_id());return 0
    if a.action=="runtime-backup-loop":
        name=select_server(a.server,"back up");runtime_backup_loop(name);return 0
    if a.action=="runtime-backup-watchdog":
        name=select_server(a.server,"watch backups for");runtime_backup_watchdog(name);return 0
    if a.action=="verify-generation":
        if not a.server or not a.generation or not a.digest:raise RuntimeError("verify-generation requires server, --generation and --digest")
        generation_verified(a.server,a.generation,a.digest);return 0
    if a.action=="verify-backup":
        if not a.owner or not a.server or not a.generation or not a.digest:raise RuntimeError("verify-backup requires server, --owner, --generation and --digest")
        verify_backup_receiver(a.owner,a.server,a.generation,a.digest);return 0
    user_command=a.action in ("toggle","start","stop","restore","status","logs","list","release-network","menu-status","prepare-sync","prune-storage","runtime-backup-once","recover")
    mutating=a.action in ("toggle","start","stop","restore","release-network","recover")
    if mutating:syncthing_backup_folder_audit()
    cluster=contextlib.nullcontext() if not mutating else cluster_transition_lock(a.remote)
    with cluster:
      with LOCK.open("w") as lock:
        if user_command:
            acquire_file_lock(lock,"the active local Minecraft transition to finish")
        else:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError("Another Minecraft transition is in progress")
        if a.action=="recover":
            recover_incomplete_operations();print("Minecraft recovery complete.");return 0
        if a.action=="menu-status":return menu_status()
        if a.action=="prepare-sync":return prepare_sync_receiver()
        if a.action=="prune-storage":
            name=select_server(a.server,"prune storage for")
            with server_backup_lock(name).open("a+") as backup_lock:
                acquire_file_lock(backup_lock,f"{name} prune backup lock");prune_backup_storage(name)
            print(f"Pruned retained backup storage for {name}.");return 0
        if a.action=="runtime-backup-once":
            name=select_server(a.server,"back up");runtime_backup_once(name);return 0
        if a.action=="list":print("\n".join(available_servers()));return 0
        if a.action=="status":return status(a.machine)
        if a.action=="logs":
            name=select_server(a.server,"view logs for");return run(["journalctl","-u",unit(name),"-f"],check=False).returncode
        if a.action=="release-network":
            if running_servers():raise RuntimeError("Cannot release Minecraft service address while a local server is running")
            network_restore();return 0
        requested=parse_names(a.server)
        if a.action=="start":
            if not requested: requested=[select_server(None,"start")]
            if len(requested)!=1: raise RuntimeError("Exactly one Minecraft server may be started at a time")
            available=set(available_servers())
            unknown=[x for x in requested if x not in available]
            if unknown: raise RuntimeError("Unknown server(s): "+", ".join(unknown))
            start(requested[0])
        elif a.action=="stop":
            if not requested:
                requested=running_servers()
                for candidate in available_servers():
                    if server_paths(candidate)[3].exists() and candidate not in requested:requested.append(candidate)
            for name in requested:stop(name,a.remote)
        elif a.action=="restore":
            if len(requested)>1:raise RuntimeError("Restore accepts exactly one server")
            restore_backup(requested[0] if requested else None,a.backup)
        elif a.action=="toggle":
            if requested:
                if len(requested)!=1: raise RuntimeError("Toggle accepts exactly one Minecraft server")
                name=requested[0]
                stop(name) if active(name) else start(name)
            else:
                running=running_servers()
                if running:
                    for name in running: stop(name)
                else:start(select_server(None,"start"))
    return 0
if __name__=="__main__":
    try:raise SystemExit(main())
    except KeyboardInterrupt:print("Cancelled safely.",file=sys.stderr);raise SystemExit(130)
    except Exception as e:print(f"minecraft-server: {e}",file=sys.stderr);raise SystemExit(1)
