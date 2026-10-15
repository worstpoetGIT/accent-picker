#!/usr/bin/env python3
"""Backend for the Accent Picker bar widget.

  accent.py list [dark|light]   JSON: the source colors matugen finds in the
                                current background, plus which one is active
  accent.py apply N [dark|light]
                                build an Omarchy theme from source color N and
                                switch to it (the background is left alone)
  accent.py sync [dark|light]   if the accent theme is active and the background
                                changed since the last apply, re-apply using
                                the color remembered for the new background

The pick is remembered per background, so going back to a wallpaper brings
back the accent chosen for it.
"""
import colorsys
import fcntl
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

HOME = os.path.expanduser("~")
THEME_NAME = "accent-picker"
THEME_DIR = f"{HOME}/.config/omarchy/themes/{THEME_NAME}"
CURRENT = f"{HOME}/.local/state/omarchy/current"
STATE_DIR = f"{HOME}/.local/state/omarchy-accent-picker"
CHOICES = f"{STATE_DIR}/choices.json"
LAST = f"{STATE_DIR}/last-applied"
CACHE = f"{HOME}/.cache/omarchy-accent-picker/palette.json"
N_COLORS = 4  # matugen's --source-color-index accepts 0..3
VIDEO_EXT = (".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi")


def out(obj):
    print(json.dumps(obj))


def fail(msg):
    out({"ok": False, "error": msg})
    sys.exit(0)  # the widget reads the error from stdout


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
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
        fail("matugen is not installed (sudo pacman -S matugen)")
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None  # index past the number of colors in this image
    try:
        return json.loads(r.stdout)["colors"]
    except Exception:
        return None


def role(colors, name, mode):
    """matugen 4.x nests each role per mode; older builds return it flat."""
    c = colors[name]
    if isinstance(c, str):
        return c
    if mode in c:
        c = c[mode]
    return c["color"] if isinstance(c, dict) else c


def palette(wall, mode):
    """Every source color's [accent, secondary, tertiary], cached per
    background + mtime: a matugen pass over a 4K image takes seconds."""
    key = f"v1:{mode}:{wall}:{os.path.getmtime(wall)}"
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cache = load_json(CACHE)
        if key not in cache:
            with ThreadPoolExecutor(N_COLORS) as ex:
                runs = list(ex.map(lambda i: matugen(wall, i, mode), range(N_COLORS)))
            entries, seen = [], set()
            for i, c in enumerate(runs):
                if not c:
                    continue
                source = role(c, "source_color", mode)
                if source in seen:
                    continue
                seen.add(source)
                entries.append({"index": i, "source": source,
                                "accent": role(c, "primary", mode),
                                "secondary": role(c, "secondary", mode),
                                "tertiary": role(c, "tertiary", mode)})
            cache = {key: entries}  # only the current background is worth keeping
            write_atomic(CACHE, json.dumps(cache))
        return cache[key]


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_to_hex(r, g, b):
    return "#" + "".join(f"{round(max(0, min(1, v)) * 255):02x}" for v in (r, g, b))


def hue_name(hexcol):
    h, _, s = colorsys.rgb_to_hls(*hex_to_rgb(hexcol))
    h *= 360
    if s < 0.12:
        return "Neutral"
    for limit, name in ((15, "Red"), (40, "Orange"), (70, "Gold"), (160, "Green"),
                        (195, "Teal"), (255, "Blue"), (290, "Purple"),
                        (335, "Pink"), (361, "Red")):
        if h < limit:
            return name


def mix(a, b, t):
    a, b = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex(*(x + (y - x) * t for x, y in zip(a, b)))


def named(hue, accent, mode, bright=False, light=None, sat=None):
    """A terminal color at a fixed hue, nudged toward the accent's hue so the
    palette feels related, at a lightness readable on the background."""
    ah, _, _ = colorsys.rgb_to_hls(*hex_to_rgb(accent))
    d = ((ah * 360 - hue + 180) % 360) - 180
    h = ((hue + max(-8, min(8, d * 0.15))) % 360) / 360  # capped: red must stay red
    if light is None:
        light = (0.70 if mode == "dark" else 0.42) + (0.07 if bright else 0) * (1 if mode == "dark" else -1)
    return rgb_to_hex(*colorsys.hls_to_rgb(h, light, sat if sat is not None else (0.72 if bright else 0.62)))


def colors_toml(c, mode):
    r = lambda n: role(c, n, mode)
    accent = r("primary")
    fg = r("on_surface")
    fg_var = r("on_surface_variant")
    keys = {
        "accent": accent,
        "selection": r("secondary_container"),
        "muted": r("outline_variant"),
        "background": r("surface"),
        "dark_background": r("surface_container_low"),
        "darker_background": r("surface_container_lowest"),
        "lighter_background": r("surface_container_high"),
        "foreground": fg_var,
        "dark_foreground": r("outline"),
        "light_foreground": mix(fg_var, fg, 0.5),
        "bright_foreground": fg,
        "red": named(0, accent, mode),
        "orange": named(25, accent, mode),
        "yellow": named(45, accent, mode),
        "green": named(120, accent, mode),
        "cyan": named(185, accent, mode),
        "blue": named(215, accent, mode),
        "magenta": named(290, accent, mode),
        "brown": named(20, accent, mode, light=0.35 if mode == "dark" else 0.30, sat=0.35),
        "bright_red": named(0, accent, mode, bright=True),
        "bright_yellow": named(45, accent, mode, bright=True),
        "bright_green": named(120, accent, mode, bright=True),
        "bright_cyan": named(185, accent, mode, bright=True),
        "bright_blue": named(215, accent, mode, bright=True),
        "bright_magenta": named(290, accent, mode, bright=True),
    }
    lines = ["# Generated by the Accent Picker plugin from the current background.",
             "# Changes here are overwritten the next time an accent is picked.",
             f'mode = "{mode}"', ""]
    lines += [f'{k} = "{v}"' for k, v in keys.items()]
    return "\n".join(lines) + "\n"


def apply(index, mode, wall=None):
    wall = wall or background()
    c = matugen(wall, index, mode)
    if not c and index != 0:
        index, c = 0, matugen(wall, 0, mode)  # saved index past this image's colors
    if not c:
        fail("matugen could not read the background")

    write_atomic(f"{THEME_DIR}/colors.toml", colors_toml(c, mode))
    choices = load_json(CHOICES)
    choices[wall] = index
    write_atomic(CHOICES, json.dumps(choices, indent=2))
    write_atomic(LAST, f"{wall}\n{mode}\n")

    env = dict(os.environ, OMARCHY_THEME_SKIP_BACKGROUND="1")
    r = subprocess.run(["omarchy-theme-set", THEME_NAME], env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        fail((r.stderr or "omarchy-theme-set failed").strip().splitlines()[-1])
    out({"ok": True, "index": index, "accent": role(c, "primary", mode)})


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__.strip())
        sys.exit(2)
    cmd = args[0]
    mode = next((a for a in args[1:] if a in ("dark", "light")), "dark")
    os.makedirs(STATE_DIR, exist_ok=True)

    if cmd == "list":
        wall = background()
        active_theme = read(f"{CURRENT}/theme.name") == THEME_NAME
        saved = load_json(CHOICES).get(wall)
        entries = palette(wall, mode)
        for e in entries:
            e["name"] = hue_name(e["accent"])
        out({"ok": True, "background": wall, "themeActive": active_theme,
             "active": saved if active_theme else None, "colors": entries})

    elif cmd == "apply" and len(args) > 1 and args[1].isdigit():
        apply(int(args[1]), mode)

    elif cmd == "sync":
        if read(f"{CURRENT}/theme.name") != THEME_NAME:
            out({"ok": True, "skipped": "theme not active"})
            return
        wall = background()
        if read(LAST).splitlines()[:1] == [wall]:
            out({"ok": True, "skipped": "unchanged"})
            return
        apply(int(load_json(CHOICES).get(wall, 0)), mode, wall)

    else:
        print(__doc__.strip())
        sys.exit(2)


if __name__ == "__main__":
    main()
