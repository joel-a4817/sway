#!/usr/bin/env python3
"""Safe multi-server Fabric lifecycle manager for NixOS/nix-minecraft."""
from __future__ import annotations
import argparse, datetime as dt, fcntl, json, os, re, shutil, signal, socket, subprocess, sys, tarfile, time
from pathlib import Path
from urllib import request, parse
import ipaddress

HOME=Path.home(); ROOT=HOME/"minecraft-server"; RUNTIME_ROOT=Path("/srv/minecraft")
BACKUPS=ROOT/"backups"; COORD=ROOT/"coordination"; LOCK=COORD/"local.lock"
PEERS=HOME/".config/minecraft-server/peers"; NETWORK_STATE=Path("/run/user")/str(os.getuid())/"minecraft-server-network.json"
FLOATING_IP=os.environ.get("MINECRAFT_FLOATING_IP","192.168.0.240")
DDNS_ENV=HOME/".config/minecraft-server/ddns.env"
CRITICAL=False

def run(cmd,check=True,capture=False,timeout=None):
    return subprocess.run(cmd,check=check,text=True,stdout=subprocess.PIPE if capture else None,stderr=subprocess.STDOUT if capture else None,timeout=timeout)
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
    for i,name in enumerate(candidates,1): print(f"  {i}) {name}{' (running)' if active(name) else ''}")
    while True:
        raw=input("Selection: ").strip()
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
def ssh(address,args):
    return run(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=4",address,"/home/joel/.config/sway/scripts/network/minecraft-server.py",*args],check=False,capture=True,timeout=300)
def query_group(addresses,args):
    for address in addresses:
        try:r=ssh(address,args)
        except (subprocess.TimeoutExpired,OSError):continue
        if r.returncode in (0,3):return address,r
    return None,None
def remote_state():
    found=[]; unreachable=[]
    for label,addresses in peer_groups():
        address,r=query_group(addresses,["status","--machine"])
        if not address:unreachable.append(label);continue
        text=r.stdout.strip()
        if text.startswith("running"):
            names=text.split()[1:]
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
        if path==ROOT.resolve():return key,folder.get("id")
    return None
def syncthing_wait(timeout=300):
    item=syncthing_config()
    if not item:raise RuntimeError("Configure ~/minecraft-server as its own Syncthing folder")
    key,folder=item; headers={"X-API-Key":key}
    request.urlopen(request.Request("http://127.0.0.1:8384/rest/db/scan?"+parse.urlencode({"folder":folder}),method="POST",headers=headers),timeout=10).read()
    end=time.time()+timeout
    while time.time()<end:
        data=json.load(request.urlopen(request.Request("http://127.0.0.1:8384/rest/db/status?"+parse.urlencode({"folder":folder}),headers=headers),timeout=10))
        if data.get("state")=="idle" and int(data.get("needTotalItems",0))==0 and int(data.get("needBytes",0))==0 and int(data.get("pullErrors",0))==0:return
        time.sleep(2)
    raise RuntimeError("Syncthing folder did not become idle and fully local within 5 minutes")
def ensure_dependencies(name=None):
    if name and run(["systemctl","cat",unit(name)],check=False,capture=True).returncode!=0:raise RuntimeError(f"{unit(name)} is not installed; rebuild NixOS")
    if run(["systemctl","is-active","--quiet","NetworkManager.service"],check=False).returncode!=0:raise RuntimeError("NetworkManager is not active")
    run(["systemctl","--user","start","syncthing.service"],check=False)
    for _ in range(30):
        try:request.urlopen("http://127.0.0.1:8384/rest/noauth/health",timeout=2).read();return
        except Exception:time.sleep(1)
    raise RuntimeError("Syncthing API did not become available")
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
        probe=run(["sudo","arping","-D","-c","2","-w","2","-I",dev,FLOATING_IP],check=False,capture=True)
        if probe.returncode not in (0,):raise RuntimeError(f"Floating LAN address {FLOATING_IP} is already in use")
        # Do not add an IPv4 label here. Linux interface labels share IFNAMSIZ
        # (15 bytes) with the base device name; names such as wlp0s20f3:minecraft
        # are rejected with "Attribute failed policy validation".
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
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Keep the nix-minecraft WorkingDirectory root in place. The service and
    # lifecycle manager both run as joel:users, so all world data remains
    # directly readable for backup and Syncthing publication.
    if str(dst).startswith("/srv/minecraft/"):
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

def server_paths(name):return unit_workdir(name),ROOT/"servers"/name/"snapshot",BACKUPS/name,COORD/f"{name}.owner.json"
def backup(name,runtime):
    folder=BACKUPS/name;folder.mkdir(parents=True,exist_ok=True); path=folder/dt.datetime.now().strftime(f"{name}-%Y%m%d-%H%M%S.tar.gz")
    with tarfile.open(path,"w:gz") as t:t.add(runtime,arcname=name)
    for old in sorted(folder.glob(f"{name}-*.tar.gz"),key=lambda p:p.stat().st_mtime,reverse=True)[10:]:old.unlink()
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
    known={"modded-server":25565,"latest-server":25566}
    if name in known:return known[name]
    raise RuntimeError(f"Cannot determine TCP port for {name}; server.properties has not been generated")

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

def start(name=None,force=False):
    global CRITICAL
    name=select_server(name,"start");ensure_dependencies(name)
    if active(name):print(f"{name} is already running locally.");return
    found,unreachable=remote_state()
    if unreachable and not force:raise RuntimeError("Cannot verify peer(s): "+", ".join(unreachable))
    for label,address,names in found:
        if any(item==f"floating={FLOATING_IP}" for item in names):
            raise RuntimeError(f"Peer {label} already owns Minecraft service address {FLOATING_IP}")
        if name in names:
            print(f"Stopping remote server {name} on {label} through {address}...")
            r=ssh(address,["stop",name,"--remote"])
            if r.returncode:raise RuntimeError(r.stdout.strip() or "remote stop failed")
    print("Waiting for the synced snapshot...");syncthing_wait();network_prepare()
    runtime,snapshot,_,owner=server_paths(name); CRITICAL=True;atomic_mirror(snapshot,runtime)
    owner.parent.mkdir(parents=True,exist_ok=True);owner.write_text(json.dumps({"server":name,"host":socket.gethostname(),"started":dt.datetime.now(dt.timezone.utc).isoformat()})+"\n")
    try:
        run(["sudo","systemctl","reset-failed",unit(name)],check=False,capture=True)
        result=run(["sudo","systemctl","start",unit(name)],check=False,capture=True)
        if result.returncode:
            log=run(["journalctl","-u",unit(name),"-n","80","--no-pager"],check=False,capture=True).stdout.strip()
            raise RuntimeError((result.stdout.strip()+"\n"+log).strip())
        port=port_for(name)
        print(f"Waiting for '{name}' to listen on TCP {port}...")
        for elapsed in range(180):
            failure=unit_failure(name)
            if failure:raise RuntimeError(failure)
            if active(name) and port_busy(port):
                domain=ddns_update()
                CRITICAL=False
                print(f"Minecraft server '{name}' started on port {port}")
                print(f"Router target: {FLOATING_IP}:{port}")
                if domain:print(f"Friend address: {domain}.duckdns.org:{port}")
                return
            if elapsed and elapsed%15==0:
                print(f"Still starting '{name}' ({elapsed}s); follow with: journalctl -u {unit(name)} -f")
            time.sleep(1)
        log=run(["journalctl","-u",unit(name),"-n","120","--no-pager"],check=False,capture=True).stdout.strip()
        run(["sudo","systemctl","stop",unit(name)],check=False)
        raise RuntimeError(f"{name} did not listen on TCP {port} within 180 seconds\n{log}".strip())
    except Exception:
        try:owner.unlink()
        except FileNotFoundError:pass
        network_restore();CRITICAL=False;raise
def stop(name=None,remote=False):
    global CRITICAL
    name=select_server(name,"stop")
    if not active(name):print(f"{name} is already stopped locally.");return
    ensure_dependencies(name);runtime,snapshot,_,owner=server_paths(name);CRITICAL=True
    print(f"Stopping '{name}' cleanly...");run(["sudo","systemctl","stop",unit(name)])
    end=time.time()+180
    while active(name) and time.time()<end:time.sleep(1)
    if active(name):raise RuntimeError(f"{name} did not stop within 180 seconds")
    backup(name,runtime);atomic_mirror(runtime,snapshot);network_restore()
    try:owner.unlink()
    except FileNotFoundError:pass
    print("Publishing stopped snapshot through Syncthing...");syncthing_wait();CRITICAL=False;print(f"'{name}' stopped, backed up, and published.")
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
def on_signal(sig,frame):
    if CRITICAL:print("\nCritical save/transfer continues; interrupt ignored.",file=sys.stderr);return
    raise KeyboardInterrupt
def main():
    p=argparse.ArgumentParser();p.add_argument("action",nargs="?",default="toggle",choices=["toggle","start","stop","status","logs","takeover","list"]);p.add_argument("server",nargs="?");p.add_argument("--machine",action="store_true");p.add_argument("--remote",action="store_true");a=p.parse_args()
    for d in (ROOT,RUNTIME_ROOT,BACKUPS,COORD):d.mkdir(parents=True,exist_ok=True)
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):signal.signal(sig,on_signal)
    with LOCK.open("w") as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError("Another Minecraft transition is in progress")
        if a.action=="list":print("\n".join(available_servers()));return 0
        if a.action=="status":return status(a.machine)
        if a.action=="logs":
            name=select_server(a.server,"view logs for");return run(["journalctl","-u",unit(name),"-f"],check=False).returncode
        requested=parse_names(a.server)
        if a.action in ("start","takeover"):
            if not requested: requested=[select_server(None,"start")]
            available=set(available_servers())
            unknown=[x for x in requested if x not in available]
            if unknown: raise RuntimeError("Unknown server(s): "+", ".join(unknown))
            for name in requested: start(name,a.action=="takeover")
        elif a.action=="stop":
            if not requested: requested=running_servers()
            for name in requested:
                if active(name): stop(name,a.remote)
        elif a.action=="toggle":
            if requested:
                for name in requested:
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
