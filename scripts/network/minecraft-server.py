#!/usr/bin/env python3
"""Safe, terminal-independent Minecraft server lifecycle manager."""
from __future__ import annotations
SERVICE = "minecraft-server-main.service"
import argparse, datetime as dt, fcntl, json, os, shutil, signal, socket, subprocess, sys, tarfile, tempfile, time
from pathlib import Path
from urllib import request, parse

HOME = Path.home()
ROOT = HOME / "minecraft-server"
RUNTIME = HOME / ".local/state/minecraft-server/runtime"
SNAPSHOT = ROOT / "snapshot"
BACKUPS = ROOT / "backups"
COORD = ROOT / "coordination"
LOCK = COORD / "local.lock"
NETWORK_STATE = Path("/run/user") / str(os.getuid()) / "minecraft-server-network.json"
OWNER = COORD / "owner.json"
PEERS = HOME / ".config/minecraft-server/peers"
WEBREMOTE = HOME / "start-ipad-webremote.sh"
PORT = 25565
CRITICAL = False
DETACHED = False

def run(cmd, *, check=True, capture=False, timeout=None):
    return subprocess.run(cmd, check=check, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.STDOUT if capture else None,
                          timeout=timeout)

def active() -> bool:
    return run(["systemctl", "is-active", "--quiet", SERVICE], check=False).returncode == 0

def port_busy() -> bool:
    with socket.socket() as s:
        s.settimeout(.3)
        return s.connect_ex(("127.0.0.1", PORT)) == 0

def peer_groups():
    """Format: group-name address1 address2 ...; addresses may include user@host."""
    groups=[]
    if not PEERS.exists(): return groups
    for number, raw in enumerate(PEERS.read_text().splitlines(), 1):
        line=raw.split("#",1)[0].strip()
        if not line: continue
        fields=line.split()
        if len(fields) < 2:
            raise RuntimeError(f"Invalid peers line {number}: expected GROUP ADDRESS [ADDRESS ...]")
        groups.append((fields[0], fields[1:]))
    names=[name for name,_ in groups]
    if len(names) != len(set(names)):
        raise RuntimeError("Duplicate peer group name in peers file")
    return groups

def ssh_address(address, args, check=False):
    return run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", address,
                "/home/joel/.config/sway/scripts/network/minecraft-server.py", *args], check=check, capture=True, timeout=240)

def query_group(name, addresses, args):
    failures=[]
    for address in addresses:
        try:
            result=ssh_address(address,args)
        except (subprocess.TimeoutExpired, OSError) as exc:
            failures.append(f"{address}: {exc}")
            continue
        # Exit 0=running, 3=stopped. Either proves this physical peer was reached.
        if result.returncode in (0,3): return address,result
        failures.append(f"{address}: {result.stdout.strip() or 'SSH failed'}")
    return None, failures

def syncthing_config():
    import xml.etree.ElementTree as ET
    cfg = HOME / ".config/syncthing/config.xml"
    if not cfg.exists(): return None
    root = ET.parse(cfg).getroot()
    key = root.findtext("gui/apikey")
    for folder in root.findall("folder"):
        try: path = Path(os.path.expandvars(os.path.expanduser(folder.get("path", "")))).resolve()
        except OSError: continue
        if path == ROOT.resolve():
            return key, folder.get("id")
    return None

def syncthing_wait(timeout=300):
    item = syncthing_config()
    if not item:
        raise RuntimeError("Configure ~/minecraft-server as its own Syncthing folder before starting")
    key, folder = item
    headers = {"X-API-Key": key}
    request.urlopen(request.Request("http://127.0.0.1:8384/rest/db/scan?" + parse.urlencode({"folder":folder}), method="POST", headers=headers), timeout=10).read()
    end = time.time() + timeout
    while time.time() < end:
        data = json.load(request.urlopen(request.Request("http://127.0.0.1:8384/rest/db/completion?" + parse.urlencode({"folder":folder}), headers=headers), timeout=10))
        if float(data.get("completion", 0)) >= 100 and int(data.get("needBytes",0)) == 0 and int(data.get("needItems",0)) == 0:
            return
        time.sleep(2)
    raise RuntimeError("Syncthing did not reach 100% completion within 5 minutes")

def atomic_mirror(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    temp = dst.with_name(dst.name + ".incoming")
    shutil.rmtree(temp, ignore_errors=True); temp.mkdir()
    if src.exists(): run(["rsync", "-a", "--delete", f"{src}/", f"{temp}/"])
    old = dst.with_name(dst.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if dst.exists(): dst.rename(old)
    temp.rename(dst); shutil.rmtree(old, ignore_errors=True)

def backup():
    BACKUPS.mkdir(parents=True, exist_ok=True)
    name = dt.datetime.now().strftime("minecraft-%Y%m%d-%H%M%S.tar.gz")
    with tarfile.open(BACKUPS / name, "w:gz") as t: t.add(RUNTIME, arcname="runtime")
    items = sorted(BACKUPS.glob("minecraft-*.tar.gz"), key=lambda p:p.stat().st_mtime, reverse=True)
    for old in items[10:]: old.unlink()

def write_owner():
    COORD.mkdir(parents=True, exist_ok=True)
    temp = OWNER.with_suffix(".tmp")
    temp.write_text(json.dumps({
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "started": dt.datetime.now(dt.timezone.utc).isoformat(),
    }, indent=2) + "\n")
    os.replace(temp, OWNER)

def ensure_dependencies():
    # NetworkManager must be active. Tailscale is intentionally neither started nor stopped here.
    if run(["systemctl", "is-active", "--quiet", "NetworkManager.service"], check=False).returncode != 0:
        raise RuntimeError("NetworkManager.service is not active")
    # Syncthing may be a user or system service. Start the known unit if present.
    user = run(["systemctl", "--user", "status", "syncthing.service"], check=False, capture=True)
    if user.returncode in (0, 3):
        run(["systemctl", "--user", "start", "syncthing.service"])
    else:
        system = run(["systemctl", "status", "syncthing.service"], check=False, capture=True)
        if system.returncode in (0, 3):
            run(["sudo", "systemctl", "start", "syncthing.service"])
    # Require the API used for safe snapshot coordination.
    for _ in range(30):
        try:
            request.urlopen("http://127.0.0.1:8384/rest/noauth/health", timeout=2).read()
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError("Syncthing API did not become available on 127.0.0.1:8384")

def remote_running():
    found=[]; unreachable=[]
    for name, addresses in peer_groups():
        address,result=query_group(name,addresses,["status","--machine"])
        if address is None:
            unreachable.append(name)
        elif result.returncode == 0 and result.stdout.strip()=="running":
            found.append((name,address))
    return found,unreachable

def network_prepare():
    """Use Ethernet only when physical carrier, NetworkManager connectivity and IPv4 are present."""
    state={"ethernet_device": None}
    r=run(["nmcli","-t","-f","DEVICE,TYPE,STATE","device","status"],check=False,capture=True)
    rows=[line.split(":",2) for line in r.stdout.splitlines() if line]
    devices=[x[0] for x in rows if len(x)==3 and x[1]=="ethernet"]
    usable=[]
    for dev in devices:
        carrier=Path(f"/sys/class/net/{dev}/carrier")
        try: linked=carrier.read_text().strip()=="1"
        except OSError: linked=False
        if not linked:
            continue
        print(f"Ethernet carrier detected on {dev}; requesting a connection...")
        run(["nmcli","device","connect",dev],check=False,capture=True,timeout=30)
        for _ in range(30):
            state_q=run(["nmcli","-g","GENERAL.STATE","device","show",dev],check=False,capture=True)
            ip_q=run(["nmcli","-g","IP4.ADDRESS","device","show",dev],check=False,capture=True)
            if state_q.stdout.startswith("100") and ip_q.stdout.strip():
                usable.append(dev)
                break
            time.sleep(1)
    if usable:
        dev=usable[0]
        state["ethernet_device"]=dev
        print(f"Using connected Ethernet with IPv4: {dev}")
    else:
        print("No Ethernet interface has both physical link and IPv4; keeping the existing connection.")
    NETWORK_STATE.parent.mkdir(parents=True,exist_ok=True)
    NETWORK_STATE.write_text(json.dumps(state)+"\n")

def network_restore():
    state={}
    try: state=json.loads(NETWORK_STATE.read_text())
    except (FileNotFoundError, json.JSONDecodeError): pass
    try: NETWORK_STATE.unlink()
    except FileNotFoundError: pass

def start(force=False):
    global CRITICAL
    if active(): print("Minecraft server is already running locally."); return
    ensure_dependencies()
    found, unreachable = remote_running()
    if len(found)>1: raise RuntimeError("Split brain detected: running on " + ", ".join(name for name,_ in found))
    if unreachable and not force: raise RuntimeError("Cannot verify peer(s): " + ", ".join(unreachable) + ". Use takeover only after checking them.")
    if found:
        name,address=found[0]
        print(f"Stopping remote Minecraft server on {name} through {address}...")
        r=ssh_address(address,["stop","--remote"])
        if r.returncode: raise RuntimeError(r.stdout.strip() or "remote stop failed")
    print("Waiting for the synced snapshot..."); syncthing_wait()
    network_prepare()
    if port_busy(): raise RuntimeError(f"TCP {PORT} is already occupied locally")
    CRITICAL=True
    atomic_mirror(SNAPSHOT,RUNTIME)
    write_owner()
    run(["sudo","systemctl","start",SERVICE])
    for _ in range(120):
        if active() and port_busy(): CRITICAL=False; print(f"Minecraft server started on {socket.gethostname()}:{PORT}"); return
        time.sleep(1)
    raise RuntimeError("Minecraft service did not become ready")

def stop(remote=False):
    global CRITICAL
    if not active():
        print("Minecraft server is already stopped locally.")
        return
    ensure_dependencies()
    CRITICAL=True
    print("Stopping Minecraft cleanly...")
    run(["sudo","systemctl","stop",SERVICE])
    end=time.time()+180
    while active() and time.time()<end: time.sleep(1)
    if active(): raise RuntimeError("Minecraft did not stop within 180 seconds")
    backup(); atomic_mirror(RUNTIME,SNAPSHOT)
    network_restore()
    try: OWNER.unlink()
    except FileNotFoundError: pass
    print("Publishing stopped snapshot through Syncthing..."); syncthing_wait()
    CRITICAL=False
    print("Minecraft server stopped, backed up, and published.")

def status(machine=False):
    state="running" if active() else "stopped"
    if machine: print(state); return 0 if state=="running" else 3
    print(f"Minecraft: {state}; port {PORT}: {'listening' if port_busy() else 'closed'}")
    found, unreachable=remote_running()
    if found: print("Remote active host(s): " + ", ".join(f"{name} via {address}" for name,address in found))
    if unreachable: print("Unreachable peer(s): " + ", ".join(unreachable))
    return 0 if state=="running" else 3

def on_signal(sig, frame):
    global DETACHED
    if CRITICAL:
        print("\nCritical save/transfer is protected and will continue; Ctrl+C is ignored in this phase.", file=sys.stderr)
        DETACHED=True
        return
    raise KeyboardInterrupt

def main():
    for d in (ROOT,RUNTIME,SNAPSHOT,BACKUPS,COORD): d.mkdir(parents=True,exist_ok=True)
    p=argparse.ArgumentParser(); p.add_argument("action",nargs="?",default="toggle",choices=["toggle","start","stop","status","logs","takeover"]); p.add_argument("--machine",action="store_true"); p.add_argument("--remote",action="store_true")
    a=p.parse_args()
    signal.signal(signal.SIGINT,on_signal); signal.signal(signal.SIGTERM,on_signal)
    with LOCK.open("w") as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError("Another Minecraft transition is already in progress")
        if a.action=="status": return status(a.machine)
        if a.action=="logs": return run(["journalctl","-u",SERVICE,"-f"],check=False).returncode
        if a.action=="toggle": stop() if active() else start()
        elif a.action=="start": start()
        elif a.action=="takeover": start(force=True)
        elif a.action=="stop": stop(a.remote)
    return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except KeyboardInterrupt: print("Cancelled safely.",file=sys.stderr); raise SystemExit(130)
    except Exception as e: print(f"minecraft-server: {e}",file=sys.stderr); raise SystemExit(1)
