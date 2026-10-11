#!/usr/bin/env python3
"""Bounded notification persistence and exact Hyprland window selection."""
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import struct
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

IMAGE_LIMIT = 5 * 1024 * 1024
IMAGE_NAME = re.compile(r"^([0-9]+-[0-9]+)-(appIcon|image)-([a-f0-9]{64})\.png$")
THEME_ICON = re.compile(r"^[A-Za-z0-9_.-]+$")
POLICY = Path(__file__).with_name('policy.xml')


def local_image(value):
    if not isinstance(value, str):
        return None
    if value.startswith('file://'):
        parsed = urlparse(value)
        if parsed.netloc or parsed.query or parsed.fragment:
            return None
        from urllib.parse import unquote
        value = unquote(parsed.path)
    return Path(value) if value.startswith('/') else None


def image_bytes(path):
    # Nonblocking + regular-file validation prevents FIFO/device stalls and bounds reads.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Image is not a regular file')
        data = stream.read(IMAGE_LIMIT + 1)
    if len(data) > IMAGE_LIMIT:
        raise ValueError('Image exceeds the size limit')
    return data


def png_dimensions(data):
    if len(data) < 33 or data[:16] != b'\x89PNG\r\n\x1a\n\0\0\0\rIHDR':
        raise ValueError('Converter did not produce a PNG')
    width, height = struct.unpack('!II', data[16:24])
    if not (0 < width <= 720 and 0 < height <= 720):
        raise ValueError('Converted image dimensions exceed the limit')
    return width, height


def decoder_limits():
    # The single-threaded helper forks only this worker; Qt never owns its decoder allocations.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (IMAGE_LIMIT,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))


def normalize_image(source, key, role):
    data = image_bytes(source)
    images = base() / 'images'
    images.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest = hashlib.sha256(data).hexdigest()
    match = IMAGE_NAME.fullmatch(source.name)
    # Content-addressed private outputs are reusable on text-only updates and restores.
    if source.parent == images and match and match.group(1) == key and match.group(2) == role:
        png_dimensions(data)
        return source
    destination = images / (key + '-' + role + '-' + digest + '.png')
    try:
        png_dimensions(image_bytes(destination))
        return destination
    except (OSError, ValueError):
        pass
    if not POLICY.is_file() or not shutil.which('magick') or not shutil.which('file'):
        raise ValueError('Image conversion requires file and ImageMagick 7')
    with tempfile.TemporaryDirectory(prefix='.foamy-image-', dir=images) as work:
        scratch = Path(work)
        incoming = scratch / 'input'
        incoming.write_bytes(data)
        mime = subprocess.run(['file', '-b', '--mime-type', str(incoming)],
                              check=True, capture_output=True, timeout=2).stdout.strip()
        coder = {b'image/png': 'PNG', b'image/jpeg': 'JPEG', b'image/gif': 'GIF',
                 b'image/webp': 'WEBP', b'image/bmp': 'BMP', b'image/x-ms-bmp': 'BMP',
                 b'image/vnd.microsoft.icon': 'ICO', b'image/x-icon': 'ICO',
                 b'image/svg+xml': 'RSVG'}.get(mime)
        if not coder:
            raise ValueError('Unsupported image format')
        output = scratch / 'output.png'
        empty = scratch / 'empty'
        empty.mkdir()
        # SVG receives stdin in an empty directory, without a base path for linked resources.
        input_spec = 'RSVG:-[0]' if coder == 'RSVG' else coder + ':' + str(incoming) + '[0]'
        env = dict(os.environ, MAGICK_CONFIGURE_PATH=str(POLICY.parent))
        subprocess.run(['magick', '-background', 'none', input_spec, '-auto-orient',
                        '-resize', '720x720>', '-strip', 'PNG:' + str(output)],
                       input=data if coder == 'RSVG' else None, cwd=empty, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True, timeout=5, preexec_fn=decoder_limits)
        normalized = image_bytes(output)
        png_dimensions(normalized)
        atomic(destination, normalized)
        # A new URL avoids Qt reusing the original or previous replacement's cached image.
        for previous in images.glob(key + '-' + role + '-*.png'):
            if previous != destination and IMAGE_NAME.fullmatch(previous.name):
                previous.unlink(missing_ok=True)
        return destination


def normalize_entry(entry, copies=()):
    key = str(entry['timestamp']) + '-' + str(entry['originalId'])
    if not KEY.fullmatch(key):
        raise ValueError('Invalid notification identity')
    copy_sources = {}
    for pair in copies:
        destination = Path(pair['to'])
        if destination.parent != base() / 'images' or destination.name not in (key+'-appIcon', key+'-image'):
            raise ValueError('Invalid image destination')
        copy_sources['file://' + str(destination)] = pair['from']
    for role in ('appIcon', 'image'):
        value = entry.get(role, '')
        if not isinstance(value, str):
            entry[role] = ''
            continue
        if role == 'appIcon' and isinstance(value, str) and THEME_ICON.fullmatch(value):
            continue
        source = local_image(copy_sources.get(value, value))
        entry[role] = ''
        if source is None:
            continue
        try:
            entry[role] = normalize_image(source, key, role).as_uri()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            # Failed optional images retain text and actions, never the unsafe original.
            print('foamy.notifications: ' + role + ' rejected: ' + type(error).__name__, file=sys.stderr)
    entry['imageFormat'] = 1
    return entry


def remove_images(key):
    for path in (base() / 'images').glob(key + '-*'):
        if path.name in (key+'-appIcon', key+'-image') or IMAGE_NAME.fullmatch(path.name):
            path.unlink(missing_ok=True)

def write(data):
    root = base()
    entry = dict(data['entry'])
    # Native action callbacks belong to the live sender and cannot survive a restart.
    entry.pop('actionsJson', None)
    key = str(entry['timestamp']) + '-' + str(entry['originalId'])
    if not KEY.fullmatch(key): raise ValueError('Invalid notification identity')
    normalize_entry(entry, data.get('copies', []))
    target = root/'history' if data.get('history') else root
    atomic(target/(key+'.json'), (json.dumps(entry,ensure_ascii=True)+'\n').encode())
    if data.get('history'):
        files = sorted(target.glob('[0-9]*-*.json'), key=lambda p:p.name, reverse=True)
        for path in files[100:]:
            path.unlink(missing_ok=True)
            remove_images(path.stem)

    if 'sources' in data:
        print(json.dumps({'key': key, 'version': data.get('imageVersion', 0), 'sources': data['sources'],
                          'appIcon': entry.get('appIcon', ''), 'image': entry.get('image', '')}))


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
            if str(row['timestamp']) + '-' + str(row.get('originalId', '')) != path.stem:
                continue
            original = dict(row)
            normalize_entry(row)
            if row != original:
                atomic(path, (json.dumps(row, ensure_ascii=True) + '\n').encode())
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
        remove_images(key)

def hypr(command):
    result = subprocess.run(['hyprctl','-j',command],check=True,capture_output=True,timeout=3)
    if len(result.stdout)>4*1024*1024: raise ValueError('Hyprland response is too large')
    return json.loads(result.stdout)

def origin_of(body):
    # Only a leading browser origin is an identity hint; URLs within message text are not.
    text = str(body).lstrip()
    # Vivaldi supplies a bare hostname on its own first line, without a URL scheme.
    first_line = text.split('\n', 1)[0].strip()
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    if len(first_line) <= 253 and re.fullmatch(r'(?:' + label + r'\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?', first_line, re.I):
        return first_line.lower()
    match = re.match(r'^(?:<a\s+[^>]*href=["\'](https?://[^"\']+)["\'][^>]*>|(https?://[^\s<]+))',text,re.I)
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
