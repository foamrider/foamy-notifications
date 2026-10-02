#!/usr/bin/env python3
"""Bounded notification persistence and exact Hyprland window selection."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

LIMIT = 131072
KEY = re.compile(r"^[0-9]+-[0-9]+$")

def packet():
    raw = sys.stdin.buffer.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("Notification exceeds the storage limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Expected an object")
    return value

def base():
    return Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state')))/'omarchy/notifications'

def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.foamy-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

def copy_image(source, destination):
    # Nonblocking + regular-file validation prevents FIFO/device stalls and bounds reads.
    fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Image is not a regular file')
        data = stream.read(5242881)
    if len(data) > 5242880:
        raise ValueError('Image exceeds the size limit')
    # Preserve the retained inode/mtime on text-only updates so the center can reuse its thumbnail.
    try:
        if destination.is_file() and not destination.is_symlink() and destination.stat().st_size == len(data):
            with destination.open('rb') as previous:
                if previous.read(5242881) == data:
                    return
    except OSError:
        pass
    atomic(destination,data)

def write(data):
    root = base()
    entry = dict(data['entry'])
    # Native action callbacks belong to the live sender and cannot survive a restart.
    entry.pop('actionsJson', None)
    key = str(entry['timestamp']) + '-' + str(entry['originalId'])
    if not KEY.fullmatch(key): raise ValueError('Invalid notification identity')
    for pair in data.get('copies',[]):
        destination = Path(pair['to'])
        if destination.parent != root/'images' or destination.name not in (key+'-appIcon',key+'-image'):
            raise ValueError('Invalid image destination')
        try:
            copy_image(pair['from'], destination)
        except (OSError,ValueError):
            # Text remains useful even when an optional local image cannot be copied.
            for role in ('appIcon','image'):
                if str(entry.get(role,'')) == 'file://' + str(destination): entry[role] = ''
    target = root/'history' if data.get('history') else root
    atomic(target/(key+'.json'), (json.dumps(entry,ensure_ascii=True)+'\n').encode())
    if data.get('history'):
        files = sorted(target.glob('[0-9]*-*.json'), key=lambda p:p.name, reverse=True)
        for path in files[100:]:
            path.unlink(missing_ok=True)
            for role in ('appIcon','image'): (root/'images'/(path.stem+'-'+role)).unlink(missing_ok=True)

def read(data):
    folder = base()/('history' if data.get('history') else '')
    # Stream bounded rows instead of concatenating arbitrary files into the shell.
    count = 0
    for path in sorted(folder.glob('[0-9]*-*.json'), reverse=True):
        if count >= 1000: break
        if not KEY.fullmatch(path.stem) or path.is_symlink(): continue
        try:
            with path.open('rb') as stream: raw = stream.read(LIMIT+1)
            if len(raw)>LIMIT: continue
            row = json.loads(raw)
            if not isinstance(row,dict) or not isinstance(row.get('summary'),str) or not row.get('timestamp'): continue
            print(json.dumps(row))
            count += 1
        except (OSError,ValueError): continue

def forget(data):
    keys = data['keys']
    if not isinstance(keys,list) or len(keys)>100 or any(not isinstance(k,str) or not KEY.fullmatch(k) for k in keys):
        raise ValueError('Invalid handled notification keys')
    config_home = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))
    center = config_home/'omarchy/plugins/foamy.notification-center/bin/notification-center'
    # Persist the center's tombstones before running an action that might restart the shell.
    if center.is_file():
        subprocess.run([str(center),'remove',*keys],check=True,timeout=15,stdout=subprocess.DEVNULL)
    for key in keys:
        for folder in (base(),base()/'history'): (folder/(key+'.json')).unlink(missing_ok=True)
        for role in ('appIcon','image'): (base()/'images'/(key+'-'+role)).unlink(missing_ok=True)

def hypr(command):
    result = subprocess.run(['hyprctl','-j',command],check=True,capture_output=True,timeout=3)
    if len(result.stdout)>4*1024*1024: raise ValueError('Hyprland response is too large')
    return json.loads(result.stdout)

def origin_of(body):
    # Only a leading browser origin is an identity hint; URLs within message text are not.
    match = re.match(r'^\s*(?:<a\s+[^>]*href=["\'](https?://[^"\']+)["\'][^>]*>|(https?://[^\s<]+))',str(body),re.I)
    if not match: return ''
    parsed = urlparse(match.group(1) or match.group(2))
    return (parsed.hostname or '').lower()

def select_window(data, clients):
    app = str(data.get('app','')).casefold()
    desktop = str(data.get('desktopEntry','')).removesuffix('.desktop').casefold()
    is_browser = any(x in app for x in ('chrom','vivaldi','brave','edge','opera'))
    origin = origin_of(data.get('body','')) if is_browser else ''
    mappings = data.get('mappings',[])
    mapped = next((m['windowClass'] for m in mappings if m['origin'].lower()==origin),'') if origin else ''
    def cls(c): return str(c.get('class','')).casefold()
    if mapped:
        matches = [c for c in clients if cls(c)==mapped.casefold()]
    elif origin:
        prefixes = ('chrome-'+origin+'__','vivaldi-'+origin+'__','msedge-'+origin+'__','brave-'+origin+'__')
        matches = [c for c in clients if cls(c).startswith(prefixes)]
        if not matches: raise ValueError('No window for this browser app; configure browserMappings')
    else:
        matches = [c for c in clients if (desktop and cls(c)==desktop) or (app and cls(c)==app)]
        if not matches and app:
            matches = [c for c in clients if app in cls(c) or (c.get('initialClass')=='org.omarchy.agent' and app in str(c.get('initialTitle','')).casefold())]
    if len(matches)!=1:
        raise ValueError('Window target is ambiguous or unavailable; configure browserMappings')
    address = str(matches[0].get('address',''))
    if not re.fullmatch(r'0x[0-9a-fA-F]+',address): raise ValueError('Invalid window address')
    return address

def focus(data):
    address = select_window(data,hypr('clients'))
    # Address is validated and passed as an argument, never interpolated sender text.
    result = subprocess.run(['hyprctl','dispatch',f'hl.dsp.focus({{ window = "address:{address}" }})'],capture_output=True,timeout=3)
    if result.returncode:
        subprocess.run(['hyprctl','dispatch','focuswindow','address:'+address],check=True,capture_output=True,timeout=3)

def focus_configured(data):
    # Both plugins use the same resolver and user mappings; archived commands never enter this path.
    config = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'omarchy/shell.json'
    mappings = []
    if config.is_file():
        with config.open('rb') as stream: raw = stream.read(LIMIT+1)
        if len(raw)>LIMIT: raise ValueError('Notification settings exceed the size limit')
        settings = json.loads(raw)
        entry = next((p for p in settings.get('plugins',[]) if isinstance(p,dict) and p.get('id')=='foamy.notifications'),{})
        mappings = entry.get('browserMappings',[])
        if not isinstance(mappings,list) or len(mappings)>32 or any(not isinstance(m,dict) or not isinstance(m.get('origin'),str) or not isinstance(m.get('windowClass'),str) for m in mappings):
            raise ValueError('Invalid browserMappings settings')
    focus({'app':data.get('app',''),'desktopEntry':data.get('desktopEntry',''),'body':data.get('body',''),'mappings':mappings})


def context(data):
    monitors = hypr('monitors')
    clients = hypr('clients') if data.get('fullscreen') else []
    point = hypr('cursorpos') if data.get('pointer') else {}
    fullscreen = []
    pointer = ''
    for monitor in monitors:
        workspace = monitor.get('specialWorkspace',{}).get('id') or monitor.get('activeWorkspace',{}).get('id')
        if any(c.get('workspace',{}).get('id')==workspace and c.get('fullscreen')==2 for c in clients): fullscreen.append(monitor['name'])
        scale = monitor.get('scale',1) or 1
        width,height = monitor['width']/scale,monitor['height']/scale
        if monitor.get('transform',0)%2: width,height = height,width
        if monitor['x'] <= point.get('x',-1e9) < monitor['x']+width and monitor['y'] <= point.get('y',-1e9) < monitor['y']+height: pointer = monitor['name']
    print(json.dumps({'fullscreen':fullscreen,'pointer':pointer}))

if __name__=='__main__':
    os.umask(0o077)
    try:
        operation = sys.argv[1]
        data = packet()
        {'write':write,'read':read,'forget':forget,'focus':focus,'focus-configured':focus_configured,'context':context}[operation](data)
    except Exception as error:
        print('foamy.notifications: '+str(error),file=sys.stderr)
        sys.exit(1)
