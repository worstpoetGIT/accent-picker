#!/usr/bin/env python3
"""Backend for the Accent Picker bar widget.

  accent.py list      JSON: the accent candidates matugen finds in the current
                      background, and which one (if any) is applied
  accent.py apply N   set the current theme's accent to candidate N
  accent.py reset     put the current theme's own accent back
  accent.py sync      if the current theme has a picked accent and the
                      background changed since, re-apply using the candidate
                      remembered for the new background

Only the `accent` key changes. The theme stays the active theme: the override
is a colors.toml in the theme's user overlay folder
(~/.config/omarchy/themes/<theme>/), which omarchy-theme-set already layers on
top of the theme. A colors.toml the user had there is backed up first and
restored on reset.
"""
import colorsys
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

HOME = os.path.expanduser("~")
CURRENT = f"{HOME}/.local/state/omarchy/current"
USER_THEMES = f"{HOME}/.config/omarchy/themes"
STATE_DIR = f"{HOME}/.local/state/omarchy-accent-picker"
CHOICES = f"{STATE_DIR}/choices.json"      # background -> candidate index
OVERRIDES = f"{STATE_DIR}/overrides.json"  # theme -> what reset must undo
BACKUPS = f"{STATE_DIR}/backups"
CACHE = f"{HOME}/.cache/omarchy-accent-picker/palette.json"
MARKER = "# Accent set by the Accent Picker plugin. Reset it from the widget to restore the theme's own accent."
N_COLORS = 4  # matugen's --source-color-index accepts 0..3
VIDEO_EXT = (".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi")
ACCENT_LINE = re.compile(r'^[ \t]*accent[ \t]*=.*$', re.M)


def out(obj):
    print(json.dumps(obj))


def fail(msg):
    out({"ok": False, "error": msg})
    sys.exit(0)  # the widget reads the error from stdout


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def write_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def save_json(path, obj):
    write_atomic(path, json.dumps(obj, indent=2))


def theme_name():
    name = read(f"{CURRENT}/theme.name").strip()
    if not name:
        fail("No Omarchy theme is active")
    return name


def official_colors(name):
    roots = [os.environ.get("OMARCHY_PATH", ""), "/usr/share/omarchy", f"{HOME}/.local/share/omarchy"]
    for root in roots:
        if root and os.path.isfile(f"{root}/themes/{name}/colors.toml"):
            return read(f"{root}/themes/{name}/colors.toml")
    return None


def accent_of(toml):
    m = ACCENT_LINE.search(toml or "")
    if not m:
        return None
    v = m.group(0).split("=", 1)[1].strip().strip('"\'')
    return v if re.fullmatch(r"#[0-9a-fA-F]{6}", v) else None


def theme_mode():
    toml = read(f"{CURRENT}/theme/colors.toml")
    m = re.search(r'^\s*mode\s*=\s*"?(dark|light)"?', toml, re.M)
    if m:
        return m.group(1)
    return "light" if os.path.exists(f"{CURRENT}/theme/light.mode") else "dark"


def background():
    path = os.path.realpath(f"{CURRENT}/background")
    if not os.path.isfile(path):
        fail("No background is set")
    if path.lower().endswith(VIDEO_EXT):
        fail("Video backgrounds aren't supported. Pick a still image")
    return path


def matugen(wall, index, mode):
    try:
        r = subprocess.run(
            ["matugen", "image", wall, "--dry-run", "-q", "-j", "hex",
             "-m", mode, "--source-color-index", str(index)],
            capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        fail("matugen is not installed (it's in the Arch extra repository)")
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None  # index past the number of colors in this image
    try:
        c = json.loads(r.stdout)["colors"]
    except Exception:
        return None

    def role(name):
        v = c[name]
        if isinstance(v, str):
            return v
        v = v.get(mode, v)  # matugen 4.x nests per mode; older builds are flat
        return v["color"] if isinstance(v, dict) else v
    return {"source": role("source_color"), "accent": role("primary")}


def candidates(wall, mode):
    """Each source color's accent, cached per background + mtime: a matugen
    pass over a 4K image takes seconds."""
    key = f"v2:{mode}:{wall}:{os.path.getmtime(wall)}"
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cache = load_json(CACHE)
        if key not in cache:
            with ThreadPoolExecutor(N_COLORS) as ex:
                runs = list(ex.map(lambda i: matugen(wall, i, mode), range(N_COLORS)))
            entries, seen = [], set()
            for i, c in enumerate(runs):
                if c and c["source"] not in seen:
                    seen.add(c["source"])
                    entries.append({"index": i, "accent": c["accent"]})
            cache = {key: entries}  # only the current background is worth keeping
            write_atomic(CACHE, json.dumps(cache))
        return cache[key]


def hue_name(hexcol):
    r, g, b = (int(hexcol[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, _, s = colorsys.rgb_to_hls(r, g, b)
    h *= 360
    if s < 0.12:
        return "Neutral"
    for limit, name in ((15, "Red"), (40, "Orange"), (70, "Gold"), (160, "Green"),
                        (195, "Teal"), (255, "Blue"), (290, "Purple"),
                        (335, "Pink"), (361, "Red")):
        if h < limit:
            return name


def base_colors(name, record):
    """The theme's colors.toml as it was before any pick."""
    if record.get("backup"):
        return read(record["backup"])
    return official_colors(name) or read(f"{CURRENT}/theme/colors.toml")


def retheme(name):
    env = dict(os.environ, OMARCHY_THEME_SKIP_BACKGROUND="1")
    r = subprocess.run(["omarchy-theme-set", name], env=env, capture_output=True, text=True)
    if r.returncode != 0:
        lines = (r.stderr or "").strip().splitlines()
        fail(lines[-1] if lines else "omarchy-theme-set failed")


def apply(index, wall=None):
    name = theme_name()
    wall = wall or background()
    user_dir = f"{USER_THEMES}/{name}"
    if os.path.isdir(f"{user_dir}/.git"):
        fail(f"{name} was installed from git, so its files are left alone. Try a built-in or your own theme")

    mode = theme_mode()
    entries = {e["index"]: e for e in candidates(wall, mode)}
    if index not in entries:
        index = min(entries) if entries else None
    if index is None:
        fail("matugen could not read the background")
    accent = entries[index]["accent"]

    overrides = load_json(OVERRIDES)
    record = overrides.get(name)
    target = f"{user_dir}/colors.toml"
    if record is None:
        # First pick on this theme: remember what reset has to restore.
        record = {"createdDir": not os.path.isdir(user_dir), "backup": None}
        if os.path.isfile(target):
            os.makedirs(BACKUPS, exist_ok=True)
            record["backup"] = f"{BACKUPS}/{name}.colors.toml"
            shutil.copy2(target, record["backup"])
        overrides[name] = record

    base = base_colors(name, record)
    if not base.strip():
        fail(f"Couldn't find the colors for {name}")
    line = f'accent = "{accent}"'
    if ACCENT_LINE.search(base):
        body = ACCENT_LINE.sub(line, base, count=1)
    else:
        body = base.rstrip("\n") + f"\n{line}\n"
    write_atomic(target, f"{MARKER}\n{body}")

    record["wallpaper"] = wall
    save_json(OVERRIDES, overrides)
    choices = load_json(CHOICES)
    choices[wall] = index
    save_json(CHOICES, choices)

    retheme(name)
    out({"ok": True, "index": index, "accent": accent})


def reset():
    name = theme_name()
    overrides = load_json(OVERRIDES)
    record = overrides.pop(name, None)
    if record is None:
        out({"ok": True, "skipped": "no accent picked for this theme"})
        return
    user_dir = f"{USER_THEMES}/{name}"
    target = f"{user_dir}/colors.toml"
    if record.get("backup") and os.path.isfile(record["backup"]):
        shutil.copy2(record["backup"], target)
        os.remove(record["backup"])
    elif read(target).startswith(MARKER):
        os.remove(target)
    if record.get("createdDir"):
        try:
            os.rmdir(user_dir)  # only if nothing else was put there since
        except OSError:
            pass
    save_json(OVERRIDES, overrides)
    retheme(name)
    out({"ok": True, "index": None})


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    os.makedirs(STATE_DIR, exist_ok=True)

    if cmd == "list":
        name = theme_name()
        wall = background()
        mode = theme_mode()
        record = load_json(OVERRIDES).get(name)
        entries = candidates(wall, mode)
        for e in entries:
            e["name"] = hue_name(e["accent"])
        active = load_json(CHOICES).get(wall) if record and record.get("wallpaper") == wall else None
        out({"ok": True, "theme": name, "mode": mode, "background": wall,
             "themeAccent": accent_of(base_colors(name, record or {})),
             "active": active, "colors": entries})

    elif cmd == "apply" and len(args) > 1 and args[1].isdigit():
        apply(int(args[1]))

    elif cmd == "reset":
        reset()

    elif cmd == "sync":
        record = load_json(OVERRIDES).get(theme_name())
        if not record:
            out({"ok": True, "skipped": "no accent picked for this theme"})
            return
        wall = background()
        if record.get("wallpaper") == wall:
            out({"ok": True, "skipped": "unchanged"})
            return
        apply(int(load_json(CHOICES).get(wall, 0)), wall)

    else:
        print(__doc__.strip())
        sys.exit(2)


if __name__ == "__main__":
    main()
