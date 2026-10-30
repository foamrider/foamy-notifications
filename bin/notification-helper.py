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

def state_home():
    return Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state')))

def base():
    # Foamy's own directory: Omarchy 4.5's upgrade migration deletes the 4.0 layout
    # under omarchy/notifications, and stock 4.5 owns that directory now.
    return state_home()/'foamy-notifications'

def popups():
    return base()/'popups'

def history():
    return base()/'history'

HISTORY_LIMIT = 100
HANDLED_LIMIT = 1000

def private_dirs():
    for folder in (base(), popups(), history(), base()/'images'):
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(folder, 0o700)

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
    target = history() if data.get('history') else popups()
    atomic(target/(key+'.json'), (json.dumps(entry,ensure_ascii=True)+'\n').encode())
    if data.get('history'):
        # A history write means the popup is gone; an earlier (or in-flight) popup
        # write for the same key must not survive to be restored after a restart.
        # Images stay: the history entry references them.
        (popups()/(key+'.json')).unlink(missing_ok=True)
        trim_history()

    if 'sources' in data:
        print(json.dumps({'key': key, 'version': data.get('imageVersion', 0), 'sources': data['sources'],
                          'appIcon': entry.get('appIcon', ''), 'image': entry.get('image', '')}))


def read(data):
    folder = history() if data.get('history') else popups()
    # Stream bounded rows instead of concatenating arbitrary files into the shell.
    count = 0
    paths = [p for p in folder.glob('[0-9]*-*.json') if KEY.fullmatch(p.stem)]
    for path in sorted(paths, key=lambda p: (stamp(p.stem), p.name), reverse=True):
        if count >= 1000: break
        if path.is_symlink(): continue
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
    # The handled journal is durable before an action that might restart the shell
    # runs; the center reads it (path from `storeInfo`) instead of being called here.
    journal = base()/'handled'
    try:
        with journal.open('rb') as stream: previous = stream.read(64 * 1024).decode('ascii', 'ignore').split()
    except OSError:
        previous = []
    lines = [k for k in previous if KEY.fullmatch(k) and k not in keys] + list(keys)
    atomic(journal, ('\n'.join(lines[-HANDLED_LIMIT:]) + '\n').encode())
    drop_keys(keys)

def drop_keys(keys):
    for key in keys:
        for folder in (popups(), history()): (folder/(key+'.json')).unlink(missing_ok=True)
        remove_images(key)

def stamp(name):
    return int(name.split('-', 1)[0])

def trim_history():
    files = sorted((p for p in history().glob('[0-9]*-*.json') if KEY.fullmatch(p.stem)), key=lambda p: (stamp(p.stem), p.name), reverse=True)
    for path in files[HISTORY_LIMIT:]:
        path.unlink(missing_ok=True)
        remove_images(path.stem)

def valid_keys(value, limit=100):
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(k, str) or not KEY.fullmatch(k) for k in value):
        raise ValueError('Invalid notification keys')
    return value

def archive(data):
    # A popup leaving the screen moves into history; images stay with it.
    keys = valid_keys(data['keys'], 2048) if 'keys' in data else valid_keys([data.get('key')])
    for key in keys:
        source = popups()/(key + '.json')
        if source.is_file() and not source.is_symlink():
            os.replace(source, history()/(key + '.json'))
    trim_history()

def delete(data):
    for key in valid_keys(data['keys'], 2048) if 'keys' in data else valid_keys([data.get('key')]):
        (popups()/(key + '.json')).unlink(missing_ok=True)
        for path in (base()/'images').glob(key + '-*'):
            path.unlink(missing_ok=True)

def clear(data):
    for path in history().glob('[0-9]*-*.json'):
        if KEY.fullmatch(path.stem):
            path.unlink(missing_ok=True)
            remove_images(path.stem)

def drop(data):
    # The center removed these durably: drop Foamy's own copies too.
    if 'before' in data:
        before = data['before']
        if type(before) is not int or before < 0: raise ValueError('Invalid cutoff')
        keys = [p.stem for folder in (popups(), history()) for p in folder.glob('[0-9]*-*.json') if KEY.fullmatch(p.stem) and stamp(p.stem) <= before]
        drop_keys(keys)
    else:
        drop_keys(valid_keys(data.get('keys')))

def sweep(data):
    # A restart can kill a write between its image copy and its JSON.
    images = base()/'images'
    for path in images.iterdir() if images.is_dir() else ():
        name = path.name
        if name.endswith('.tmp') or name.startswith('.foamy-'):
            if path.is_dir(): shutil.rmtree(path, ignore_errors=True)
            else: path.unlink(missing_ok=True)
            continue
        match = re.fullmatch(r'([0-9]+-[0-9]+)-(appIcon|image)(-[a-f0-9]+)?(\.png)?', name)
        if match and not (popups()/(match.group(1)+'.json')).exists() and not (history()/(match.group(1)+'.json')).exists():
            path.unlink(missing_ok=True)

MARKER = '.migrated-from-omarchy-notifications'

def migrate(data):
    """One-time move of the 4.0 layout Foamy used under omarchy/notifications.

    Runs before anything else reads state. Only Foamy's per-file names move;
    stock files (live.json, history.json, quiet.json, notifications.json) stay."""
    private_dirs()
    if (base()/MARKER).exists():
        print(json.dumps({'migrated': False}))
        return
    old = state_home()/'omarchy/notifications'
    old_images, new_images = old/'images', base()/'images'
    moved = 0
    def rewrite(row):
        for role in ('appIcon', 'image'):
            value = row.get(role)
            if isinstance(value, str) and value.startswith('file://'):
                path = local_image(value)
                if path and path.parent == old_images:
                    row[role] = (new_images/path.name).as_uri()
        return row
    if old_images.is_dir() and not old_images.is_symlink():
        for path in old_images.iterdir():
            if IMAGE_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink() and not (new_images/path.name).exists():
                shutil.copyfile(path, new_images/path.name)
                os.chmod(new_images/path.name, 0o600)
                path.unlink(missing_ok=True)
    for source_dir, target_dir in ((old, popups()), (old/'history', history())):
        if not source_dir.is_dir() or source_dir.is_symlink(): continue
        for path in source_dir.glob('[0-9]*-*.json'):
            if not KEY.fullmatch(path.stem) or path.is_symlink() or not path.is_file(): continue
            target = target_dir/path.name
            try:
                if not target.exists():
                    with path.open('rb') as stream: raw = stream.read(LIMIT + 1)
                    if len(raw) <= LIMIT:
                        row = json.loads(raw)
                        if isinstance(row, dict):
                            atomic(target, (json.dumps(rewrite(row), ensure_ascii=True) + '\n').encode())
                            moved += 1
                path.unlink(missing_ok=True)
            except (OSError, ValueError):
                continue
    trim_history()
    state = base()/'state.json'
    if not state.exists():
        carried = {'version': 5, 'dnd': False, 'snoozes': {}}
        try:
            legacy = json.loads((state_home()/'omarchy/notifications.json').read_text())
            if isinstance(legacy, dict):
                carried['dnd'] = legacy.get('dnd') is True
                if isinstance(legacy.get('snoozes'), dict): carried['snoozes'] = legacy['snoozes']
        except (OSError, ValueError):
            try:
                quiet = json.loads((old/'quiet.json').read_text())
                carried['dnd'] = isinstance(quiet, dict) and quiet.get('dnd') is True
            except (OSError, ValueError):
                pass
        atomic(state, (json.dumps(carried, indent=2) + '\n').encode())
    atomic(base()/MARKER, b'1\n')
    print(json.dumps({'migrated': True, 'moved': moved}))

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

OPERATIONS = {'write': write, 'read': read, 'forget': forget, 'focus': focus, 'focus-configured': focus_configured,
              'context': context, 'archive': archive, 'delete': delete, 'clear': clear, 'drop': drop,
              'sweep': sweep, 'migrate': migrate}


def serve():
    """One long-lived worker: newline-delimited JSON jobs in, one JSON reply line each.

    {"id": n, "op": "write", "data": {...}} -> {"id": n, "ok": true, "out": "...", "error": ""}
    Replaces one python/bash process per persistence job (bursts no longer spawn)."""
    import contextlib, io
    private_dirs()
    stdin = sys.stdin.buffer
    while True:
        line = stdin.readline(LIMIT + 4096)
        if not line:
            return
        reply = {'id': None, 'ok': False, 'out': '', 'error': ''}
        try:
            if not line.endswith(b'\n'):
                # Oversized: discard the rest of the line, then answer the job as failed.
                while line and not line.endswith(b'\n'):
                    line = stdin.readline(LIMIT + 4096)
                raise ValueError('Notification exceeds the storage limit')
            job = json.loads(line)
            reply['id'] = job.get('id')
            data = job.get('data') or {}
            if not isinstance(data, dict): raise ValueError('Expected an object')
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                OPERATIONS[job.get('op')](data)
            reply['ok'] = True
            reply['out'] = buffer.getvalue()
        except Exception as error:
            reply['error'] = str(error) or type(error).__name__
        sys.stdout.write(json.dumps(reply, ensure_ascii=True) + '\n')
        sys.stdout.flush()


if __name__=='__main__':
    os.umask(0o077)
    try:
        operation = sys.argv[1]
        if operation == 'serve':
            serve()
            sys.exit(0)
        data = packet()
        OPERATIONS[operation](data)
    except Exception as error:
        print('foamy.notifications: '+str(error),file=sys.stderr)
        sys.exit(1)
