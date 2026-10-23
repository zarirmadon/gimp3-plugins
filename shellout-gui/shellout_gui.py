#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ShellOut — external graphics editor bridge for GIMP 3.2.

Tools > ShellOut… opens a single native GTK dialog for launching and
configuring external editors. Results ALWAYS return as a new image layer.
Application discovery happens only while this dialog is open.
"""

import os
import sys
import json
import copy
import re
import plistlib
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("GimpUi", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gimp, GimpUi, Gtk, Gdk, Gio, GLib

PLUGIN_ID = "python-fu-shellout"
MENU_PATH = "<Image>/Tools"
SETTINGS_PATH = os.path.join(GLib.get_user_config_dir(), "gimp-shellout-settings.json")
CACHE_ROOT = os.path.join(GLib.get_user_cache_dir(), "gimp-shellout")
DEBUG = False

# Desktop-menu semantics are shared by freedesktop-compatible UNIX desktops,
# not just Linux. BSD ports and several other UNIX systems use the same
# XDG applications database when GTK/GIO desktop integration is installed.
def _is_xdg_unix():
    return sys.platform.startswith(('linux', 'freebsd', 'openbsd', 'netbsd',
                                    'dragonfly', 'sunos', 'illumos'))


def _gio_app_infos(visible_only=False):
    """Return the installed OS application registry without filesystem scans.

    Gio.AppInfo is intentionally used as the common frontend on supported OSes.
    The underlying implementation and its coverage are platform dependent.
    """
    try:
        infos = Gio.AppInfo.get_all() or []
    except (GLib.Error, OSError, AttributeError):
        return []
    if not visible_only:
        return list(infos)
    result = []
    for info in infos:
        try:
            if info.should_show():
                result.append(info)
        except (GLib.Error, AttributeError, TypeError):
            continue
    return result

def _log_error(message):
    """Terminal output is reserved for genuine unexpected ShellOut errors."""
    print(f"[ShellOut ERROR] {message}", file=sys.stderr)

def _debug(message):
    if DEBUG:
        print(f"[ShellOut DEBUG] {message}", file=sys.stderr)


# ShellOut visual standard: Compact Visual Dark Adwaita, 19 October 2026.
# The CSS is scoped to ShellOut windows: it never changes GIMP or other plug-ins.
_SHELLOUT_CSS_PROVIDER = None
_SHELLOUT_CSS = r"""
.shellout-dialog,
.shellout-dialog > box {
    background-color: #303030;
    color: #ededed;
}
.shellout-dialog label { color: #ededed; }
.shellout-dialog label.shellout-muted { color: #b9b9b9; }
.shellout-dialog label.shellout-section { color: #e1e1e1; }
.shellout-dialog .shellout-panel {
    background-color: #353535;
    border: 1px solid #4a4a4a;
    border-radius: 6px;
}
.shellout-dialog .shellout-row {
    background-color: transparent;
    border-radius: 3px;
}
.shellout-dialog .shellout-row:hover { background-color: #3d3d3d; }
.shellout-dialog .shellout-mode-selected {
    background-color: #474747;
    border-left: 3px solid #929292;
    border-radius: 3px;
}
.shellout-dialog .shellout-footer { background-color: #2c2c2c; }
.shellout-dialog button {
    background-image: none;
    background-color: #414141;
    border: 1px solid #5a5a5a;
    border-radius: 5px;
    color: #eeeeee;
    text-shadow: none;
    box-shadow: none;
    min-height: 23px;
}
.shellout-dialog button:hover { background-color: #505050; }
.shellout-dialog button:active { background-color: #626262; }
.shellout-dialog button:disabled { color: #858585; background-color: #393939; }
.shellout-dialog button.shellout-primary {
    background-color: #565656;
    border-color: #818181;
    font-weight: bold;
}
.shellout-dialog checkbutton check:checked,
.shellout-dialog radiobutton radio:checked {
    background-image: none;
    background-color: #858585;
    border-color: #a5a5a5;
    color: #f5f5f5;
}
.shellout-dialog combobox button,
.shellout-dialog spinbutton entry,
.shellout-dialog spinbutton button {
    background-color: #3a3a3a;
    color: #eeeeee;
    border-color: #5b5b5b;
}
.shellout-dialog scrolledwindow { background-color: transparent; }
.shellout-dialog scrollbar.vertical { min-width: 16px; }
.shellout-dialog scrollbar.vertical slider {
    min-width: 11px;
    background-color: #777777;
    border-radius: 8px;
}
.shellout-dialog scrollbar.vertical slider:hover { background-color: #999999; }

/* Alternating neutral rows, with native GTK selection and keyboard focus. */
.shellout-dialog row.shellout-list-row { border-radius: 0; }
.shellout-dialog row.shellout-list-row.shellout-alt-even { background-color: #363636; }
.shellout-dialog row.shellout-list-row.shellout-alt-odd { background-color: #3d3d3d; }
.shellout-dialog row.shellout-list-row:hover { background-color: #474747; }
.shellout-dialog row.shellout-list-row:selected,
.shellout-dialog row.shellout-list-row:selected:hover {
    background-color: #555555;
    color: #f5f5f5;
}
.shellout-dialog row.shellout-list-row:selected label { color: #f5f5f5; }
.shellout-dialog row.shellout-list-row:disabled { color: #999999; }
.shellout-dialog .shellout-disabled-text { color: #989898; }
"""


def _install_shellout_css():
    """Install once per plug-in UI process; never install during query/init."""
    global _SHELLOUT_CSS_PROVIDER
    if _SHELLOUT_CSS_PROVIDER is not None:
        return
    # This plug-in runs in its own process; prefer the dark variant without
    # changing the running GIMP theme or the user's global GTK preferences.
    gtk_settings = Gtk.Settings.get_default()
    if gtk_settings is not None:
        gtk_settings.set_property('gtk-application-prefer-dark-theme', True)
    screen = Gdk.Screen.get_default()
    if screen is None:
        return
    provider = Gtk.CssProvider()
    try:
        provider.load_from_data(_SHELLOUT_CSS.encode('utf-8'))
    except GLib.Error as exc:
        _log_error(f'GTK theme stylesheet could not be loaded: {exc}')
        return
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _SHELLOUT_CSS_PROVIDER = provider


def _prepare_dialog(dialog):
    """Keep the dialog attached to GIMP and apply only scoped ShellOut styling."""
    _install_shellout_css()
    dialog.get_style_context().add_class('shellout-dialog')
    try:
        # Preserve a supplied Gtk parent for nested Settings/editor dialogs.
        # Only top-level ShellOut windows need their parent set to GIMP.
        if dialog.get_transient_for() is None:
            GimpUi.window_set_transient(dialog)
    except (AttributeError, GLib.Error):
        pass
    return dialog


AUTO_RENDER = "auto_render"
MANUAL_SAVE = "manual_save"
MANUAL_SAVE_AS = "manual_save_as"
CLOSE_ONLY = "close_only"

BEHAVIOR_LABELS = {
    MANUAL_SAVE: "Save before closing",
    MANUAL_SAVE_AS: "Save / export an image before closing",
    AUTO_RENDER: "Sidecar / automatic render on close",
    CLOSE_ONLY: "No explicit save required",
}

# These are behaviour profiles, not the source of the application list.
# Installed applications are discovered from the desktop application database.
KNOWN_PROFILES = {
    "krita": {"name": "Krita", "ext": ".png", "behavior": MANUAL_SAVE},
    "inkscape": {"name": "Inkscape", "ext": ".png", "behavior": MANUAL_SAVE, "special": "inkscape"},
    "blender": {"name": "Blender", "ext": ".png", "behavior": MANUAL_SAVE_AS, "special": "blender"},
    "mypaint": {"name": "MyPaint", "ext": ".png", "behavior": MANUAL_SAVE},
    "darktable": {"name": "Darktable", "ext": ".png", "behavior": AUTO_RENDER, "special": "darktable"},
    "art": {"name": "ART", "ext": ".tif", "behavior": AUTO_RENDER, "special": "art"},
    "rawtherapee": {"name": "RawTherapee", "ext": ".tif", "behavior": AUTO_RENDER, "special": "rawtherapee"},
    "pinta": {"name": "Pinta", "ext": ".png", "behavior": MANUAL_SAVE},
    "lazpaint": {"name": "LazPaint", "ext": ".png", "behavior": MANUAL_SAVE},
}

IMAGE_MIMES = {
    "image/png", "image/jpeg", "image/tiff", "image/webp", "image/bmp",
    "image/x-exr", "image/openraster", "image/svg+xml", "image/x-xcf",
}
VIEWER_WORDS = {
    "viewer", "browser", "file manager", "image viewer", "photo viewer",
    "gwenview", "loupe", "eog", "ristretto", "feh", "sxiv", "nomacs",
}


def _default_settings():
    return {"version": 4, "apps": {}, "dismissed": []}


def load_settings():
    data = _default_settings()
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
            old = json.load(fh)
        if isinstance(old, dict):
            if isinstance(old.get("apps"), dict):
                data.update(old)
            # Migration from the previous custom_programs format.
            for cp in old.get('custom_programs', []) if isinstance(old.get('custom_programs'), list) else []:
                if not isinstance(cp, dict):
                    continue
                args = cp.get('cmd_args') if isinstance(cp.get('cmd_args'), list) else []
                key = 'custom:' + str(cp.get('id') or _slug(cp.get('name', 'program')))
                data["apps"].setdefault(key, {
                    "id": key, "name": cp.get("name", "Custom Program"),
                    "commandline": (subprocess.list2cmdline([str(x) for x in [cp.get('exec_path', '')] + args])
                                    if os.name == 'nt' else
                                    shlex.join([str(x) for x in [cp.get('exec_path', '')] + args])),
                    "executable": cp.get("exec_path", ""), "desktop_id": "",
                    "ext": cp.get("ext", ".png"), "behavior": cp.get("behavior", MANUAL_SAVE),
                    "enabled": True, "recognized": False, "custom": True,
                })
    except (OSError, ValueError, TypeError):
        pass
    if not isinstance(data.get('apps'), dict):
        data['apps'] = {}
    else:
        data['apps'] = {str(k): v for k, v in data['apps'].items() if isinstance(v, dict)}
    if not isinstance(data.get('dismissed'), list):
        data['dismissed'] = []
    return data


def save_settings(data):
    """Atomically save settings; unique temp name prevents concurrent overwrite."""
    folder = os.path.dirname(SETTINGS_PATH)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.shellout-', suffix='.json.tmp', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, SETTINGS_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s[:48] or "application"


def _profile_for(name, executable="", desktop_id=""):
    hay = " ".join((name, executable, desktop_id)).lower()
    # ART is intentionally tested carefully: don't match arbitrary 'art'.
    if "rawtherapee" in hay:
        return KNOWN_PROFILES["rawtherapee"]
    if re.search(r"(^|[\s/._-])art([\s/._-]|$)", hay) or "us.pixls.art" in hay:
        return KNOWN_PROFILES["art"]
    for key in ("krita", "inkscape", "blender", "mypaint", "darktable", "pinta", "lazpaint"):
        if key in hay:
            return KNOWN_PROFILES[key]
    return None


def _strip_exec_placeholders(parts):
    """Remove desktop/registry file placeholders; ShellOut appends its own file."""
    out = []
    for part in parts:
        token = str(part).strip()
        if not token:
            continue
        if token.startswith("%") and len(token) <= 3:
            continue
        if token.lower() in ("%1", "%l", "%u", "%f", "%*", '"%1"', '"%l"', '"%u"', '"%f"'):
            continue
        out.append(token)
    return out


def _command_from_appinfo(info):
    cmdline = info.get_commandline() or ""
    exe = info.get_executable() or ""
    try:
        parts = shlex.split(cmdline, posix=(os.name != 'nt')) if cmdline else ([exe] if exe else [])
    except ValueError:
        parts = [exe] if exe else []
    return _strip_exec_placeholders(parts)


def _record_app(found, *, key, name, executable, command, desktop_id="", image_capable=False,
                source="", custom=False):
    """Normalize one discovered application into ShellOut's cross-platform schema."""
    profile = _profile_for(name, executable, desktop_id)
    if not image_capable and profile is None:
        return
    lower = name.casefold()
    likely_viewer = any(word in lower for word in VIEWER_WORDS)
    command = [str(x) for x in command if str(x)]
    if not command and not desktop_id:
        return
    rec = {
        "id": key,
        "name": profile.get("name", name) if profile else name,
        "desktop_id": desktop_id,
        "executable": executable,
        "command": command,
        "commandline": (subprocess.list2cmdline(command) if os.name == 'nt'
                        else shlex.join(command)),
        "ext": profile.get("ext", ".png") if profile else ".png",
        "behavior": profile.get("behavior", MANUAL_SAVE) if profile else MANUAL_SAVE,
        "special": profile.get("special", "") if profile else "",
        "recognized": profile is not None,
        "likely_viewer": likely_viewer,
        "custom": custom,
        "source": source,
        "platform": sys.platform,
    }
    old = found.get(key)
    if old is None or (rec["recognized"] and not old.get("recognized")):
        found[key] = rec


def _desktop_menu_categories(info):
    """Return freedesktop Categories for a menu entry, when available.

    GDesktopAppInfo exposes get_categories() on implementations supporting it.
    For GI versions that omit that method, read only the application's own
    desktop entry in XDG application directories. Never run executables.
    """
    try:
        getter = getattr(info, 'get_categories', None)
        raw = getter() if callable(getter) else None
        if raw:
            return set(filter(None, str(raw).split(';')))
    except (GLib.Error, TypeError, AttributeError):
        pass
    desktop_id = str(info.get_id() or '')
    if not desktop_id or '/' in desktop_id or '..' in desktop_id:
        return set()
    # Some Gio bindings expose a desktop filename and some do not.
    locations = []
    try:
        getter = getattr(info, 'get_filename', None)
        filename = getter() if callable(getter) else None
        if filename:
            locations.append(filename)
    except (GLib.Error, TypeError, AttributeError):
        pass
    home = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    data_dirs = os.environ.get('XDG_DATA_DIRS') or '/usr/local/share:/usr/share'
    locations.extend(os.path.join(root, 'applications', desktop_id)
                     for root in [home] + [x for x in data_dirs.split(os.pathsep) if x])
    for filename in locations:
        try:
            # GLib.KeyFile follows the official Desktop Entry specification.
            keyfile = GLib.KeyFile()
            keyfile.load_from_file(filename, GLib.KeyFileFlags.NONE)
            raw = keyfile.get_string('Desktop Entry', 'Categories')
            if raw:
                return set(filter(None, raw.split(';')))
        except (GLib.Error, OSError):
            continue
    return set()


def _desktop_app_record(info, include_unrelated=False):
    """Build one Linux application record from its desktop-menu registration.

    The user may explicitly pick ANY visible application. For automatic
    discovery, only image-associated or recognised editing applications are
    suggested, and we do not require Gio.supports_files() or should_show()
    for recognised graphics editors. Some desktop entries do not advertise
    file support even though their executable accepts image filenames.
    """
    name = (info.get_display_name() or info.get_name() or "").strip()
    if not name:
        return None
    desktop_id = info.get_id() or ""
    executable = info.get_executable() or ""
    types = set(info.get_supported_types() or ())
    profile = _profile_for(name, executable, desktop_id)
    if not include_unrelated and not (profile or types.intersection(IMAGE_MIMES)):
        return None
    command = _command_from_appinfo(info)
    if not command and not desktop_id:
        return None
    found = {}
    key = desktop_id or ("exec:" + executable if executable else "name:" + _slug(name))
    _record_app(found, key=key, name=name, executable=executable,
                command=command, desktop_id=desktop_id,
                image_capable=True, source="desktop")
    rec = found.get(key)
    if rec is not None:
        rec["supports_files"] = bool(info.supports_files() or info.supports_uris())
        rec["icon_name"] = desktop_id[:-8] if desktop_id.endswith('.desktop') else desktop_id
        rec["menu_categories"] = sorted(_desktop_menu_categories(info))
    return rec


def _discover_linux_apps():
    """Find suggested image applications in the installed desktop registry.

    No installation-specific paths and no subprocess discovery. GIO includes
    desktop entries whose MIME declarations are incomplete; known editors
    should still be offered, even with should_show() or supports_files() false.
    """
    found = {}
    for info in _gio_app_infos():
        try:
            rec = _desktop_app_record(info)
            if rec:
                found[rec['id']] = rec
        except (GLib.Error, ValueError, TypeError):
            continue

    # Package installations occasionally provide a callable editor but no
    # working .desktop registration. Look up only our named editor profiles
    # on PATH, during Settings discovery, rather than asking the user to know
    # /usr/bin paths or doing an expensive filesystem scan.
    known_executables = {
        'darktable': 'darktable', 'krita': 'krita',
        'inkscape': 'inkscape', 'blender': 'blender',
        'rawtherapee': 'rawtherapee', 'mypaint': 'mypaint',
        'pinta': 'pinta', 'lazpaint': 'lazpaint',
    }
    for key, command_name in known_executables.items():
        if any(_profile_for(rec['name'], rec.get('executable', ''),
                            rec.get('desktop_id', '')) is KNOWN_PROFILES[key]
               for rec in found.values()):
            continue
        executable = shutil.which(command_name)
        if executable:
            _record_app(found, key='path:' + key,
                        name=KNOWN_PROFILES[key]['name'],
                        executable=executable, command=[executable],
                        image_capable=True, source='system-path')
    return list(found.values())


def _windows_command_executable(command):
    """Extract the executable from a Windows shell-open command safely enough for discovery."""
    text = os.path.expandvars(str(command or "")).strip()
    if not text:
        return ""
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[1:end] if end > 1 else ""
    m = re.match(r"(.+?\.exe)(?:\s|$)", text, re.I)
    return m.group(1).strip() if m else ""


def _discover_windows_apps():
    """Windows discovery from RegisteredApplications and image file associations.

    No registry work happens at GIMP startup; this function is called only by
    Settings -> Refresh Applications.
    """
    found = {}
    try:
        import winreg
    except ImportError:
        return []

    roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    reg_paths = (r"Software\RegisteredApplications", r"Software\WOW6432Node\RegisteredApplications")
    image_exts = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp", ".svg", ".ora", ".xcf", ".exr")

    def reg_get(root, path, value=""):
        try:
            with winreg.OpenKey(root, path) as k:
                return winreg.QueryValueEx(k, value)[0]
        except OSError:
            return None

    for root in roots:
        for rp in reg_paths:
            try:
                with winreg.OpenKey(root, rp) as key:
                    count = winreg.QueryInfoKey(key)[1]
                    entries = [winreg.EnumValue(key, i)[:2] for i in range(count)]
            except OSError:
                continue
            for display_name, capabilities in entries:
                try:
                    cap = str(capabilities)
                    app_name = reg_get(root, cap, "ApplicationName") or display_name
                    assoc_path = cap + r"\FileAssociations"
                    progids = []
                    try:
                        with winreg.OpenKey(root, assoc_path) as ak:
                            n = winreg.QueryInfoKey(ak)[1]
                            for i in range(n):
                                ext, progid, _ = winreg.EnumValue(ak, i)
                                if ext.casefold() in image_exts:
                                    progids.append(str(progid))
                    except OSError:
                        pass
                    profile = _profile_for(str(app_name), "", str(display_name))
                    if not progids and profile is None:
                        continue
                    open_cmd = ""
                    for progid in progids:
                        open_cmd = reg_get(root, progid + r"\shell\open\command") or reg_get(
                            winreg.HKEY_CLASSES_ROOT, progid + r"\shell\open\command") or ""
                        if open_cmd:
                            break
                    executable = _windows_command_executable(open_cmd)
                    if not executable:
                        # Known editors can still be found through App Paths.
                        candidates = []
                        low = str(app_name).casefold()
                        for known in KNOWN_PROFILES:
                            if known in low:
                                candidates.extend((known + ".exe", KNOWN_PROFILES[known]["name"] + ".exe"))
                        for exe_name in candidates:
                            executable = reg_get(winreg.HKEY_LOCAL_MACHINE,
                                "Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + exe_name) or reg_get(
                                winreg.HKEY_CURRENT_USER,
                                "Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + exe_name) or ""
                            if executable:
                                break
                    if not executable:
                        continue
                    executable = os.path.expandvars(str(executable)).strip('"')
                    key_id = "winreg:" + _slug(str(display_name)) + ":" + _slug(executable)
                    _record_app(found, key=key_id, name=str(app_name), executable=executable,
                                command=[executable], image_capable=bool(progids) or profile is not None,
                                source="windows-registry")
                except Exception:
                    continue
    # GIMP bundles Gio on Windows; its native AppInfo implementation can
    # expose image editors missing from RegisteredApplications. Merge those
    # safely; do not assume every Store package has a runnable .exe path.
    for info in _gio_app_infos():
        try:
            rec = _desktop_app_record(info)
            if rec is not None:
                # Prefer a registered launch mechanism over reconstructing a
                # Windows registry command, when both describe the same app.
                duplicate_key = next((key for key, old in found.items()
                    if old.get('name', '').casefold() == rec['name'].casefold()), None)
                if duplicate_key is not None:
                    found.pop(duplicate_key)
                found[rec['id']] = rec
        except (GLib.Error, AttributeError, ValueError, TypeError):
            continue
    return list(found.values())


def _mac_bundle_image_capable(info):
    image_exts = {"png", "jpg", "jpeg", "tif", "tiff", "webp", "bmp", "svg", "ora", "xcf", "exr", "heic", "heif"}
    for doc in info.get("CFBundleDocumentTypes", []) or []:
        for ext in doc.get("CFBundleTypeExtensions", []) or []:
            if str(ext).casefold().lstrip(".") in image_exts or str(ext) == "*":
                return True
        for uti in doc.get("LSItemContentTypes", []) or []:
            u = str(uti).casefold()
            if u.startswith("public.image") or u in ("public.png", "public.jpeg", "public.tiff", "public.svg-image"):
                return True
    return False


def _discover_macos_apps():
    """macOS discovery from application bundles and their Info.plist document declarations."""
    found = {}
    roots = [Path("/Applications"), Path.home() / "Applications", Path("/System/Applications")]
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        # Most user applications are direct children; one nested level covers
        # common vendor folders without recursively walking huge app bundles.
        bundles = list(root.glob("*.app")) + list(root.glob("*/*.app"))
        for bundle in bundles:
            try:
                real = str(bundle.resolve())
                if real in seen:
                    continue
                seen.add(real)
                plist_path = bundle / "Contents" / "Info.plist"
                with plist_path.open("rb") as fh:
                    info = plistlib.load(fh)
                name = str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or bundle.stem)
                bundle_id = str(info.get("CFBundleIdentifier") or "")
                exe_name = str(info.get("CFBundleExecutable") or "")
                executable = str(bundle / "Contents" / "MacOS" / exe_name) if exe_name else ""
                profile = _profile_for(name, executable, bundle_id)
                image_capable = _mac_bundle_image_capable(info) or profile is not None
                if not executable or not os.path.isfile(executable):
                    continue
                key = "mac:" + (bundle_id or _slug(real))
                _record_app(found, key=key, name=name, executable=executable,
                            command=[executable], desktop_id=bundle_id,
                            image_capable=image_capable, source="macos-bundle")
                if key in found:
                    found[key]['bundle_path'] = str(bundle)
            except Exception:
                continue
    # Modern macOS GTK builds may also expose native registrations via Gio.
    # These complement .app bundles installed outside /Applications and avoid
    # guessing an executable path. Launch Services remains the fallback.
    for appinfo in _gio_app_infos():
        try:
            rec = _desktop_app_record(appinfo)
            if rec is None:
                continue
            if any(v.get('name', '').casefold() == rec['name'].casefold()
                   for v in found.values()):
                continue
            found[rec['id']] = rec
        except (GLib.Error, AttributeError, ValueError, TypeError):
            continue
    return list(found.values())


def discover_graphics_apps():
    """Best-effort, OS-specific editor discovery; not a guarantee for all packages.

    Linux/BSD/XDG UNIX: freedesktop registry plus known executable fallback.
    Windows: system associations, registry and Gio application registrations.
    macOS: .app bundles, Launch Services and optional Gio registrations.
    Sandboxed, Store-only and unregistered portable installs can need a
    user choice through the system menu or optional executable picker.
    """
    if sys.platform.startswith("win"):
        apps = _discover_windows_apps()
    elif sys.platform == "darwin":
        apps = _discover_macos_apps()
    elif _is_xdg_unix():
        apps = _discover_linux_apps()
    else:
        # Unknown POSIX platforms with a functioning GIO registry still get a
        # named application picker, rather than assuming Linux paths or tools.
        apps = []
        for info in _gio_app_infos():
            try:
                rec = _desktop_app_record(info)
                if rec is not None:
                    apps.append(rec)
            except (GLib.Error, AttributeError, ValueError, TypeError):
                pass
    return sorted(apps, key=lambda a: a.get("name", "").casefold())

def sync_discovery(settings):
    """Reconcile installed applications without discarding user preferences.

    Newly registered desktop IDs replace obsolete registrations with the same
    editor name (for example after swapping AppImage for a system package).
    This changes only the in-memory Settings draft until Apply/Open is used.
    """
    apps = settings.setdefault('apps', {})
    discovered = discover_graphics_apps()
    current_ids = {rec['id'] for rec in discovered}
    for d in discovered:
        key = d['id']
        if key in apps:
            # A user explicitly selected a replacement executable in Settings.
            # Keep that override rather than replacing it during re-scan.
            if apps[key].get('custom'):
                continue
            for field in ('name', 'desktop_id', 'executable', 'command',
                          'commandline', 'recognized', 'likely_viewer',
                          'special', 'source', 'platform', 'supports_files',
                          'icon_name', 'bundle_path', 'menu_categories'):
                apps[key][field] = d.get(field)
            apps[key].setdefault('ext', d['ext'])
            apps[key].setdefault('behavior', d['behavior'])
            apps[key].setdefault('enabled', bool(d['recognized'] and not d['likely_viewer']))
            continue

        # A formerly registered application may acquire a different desktop
        # ID when reinstalled. Move its explicit settings to the new record;
        # avoid keeping two rows labelled Darktable, for example.
        stale_id = next((old_key for old_key, old in apps.items()
                         if old_key not in current_ids
                         and not old.get('custom')
                         and old.get('name', '').casefold() == d['name'].casefold()
                         and (old.get('source') in ('desktop', 'system-path', None))), None)
        if stale_id:
            previous = apps.pop(stale_id)
            d['enabled'] = bool(previous.get('enabled', d['recognized']))
            d['ext'] = previous.get('ext') or d['ext']
            d['behavior'] = previous.get('behavior') or d['behavior']
        else:
            d['enabled'] = bool(d['recognized'] and not d['likely_viewer'])
        apps[key] = d
    # No changes are persisted until Apply/Open; Cancel discards this draft.
    return settings


def enabled_apps(settings):
    vals = [a for a in settings.get("apps", {}).values() if a.get("enabled")]
    # Deduplicate obvious duplicate native/desktop entries by display name, preferring recognised.
    chosen = {}
    for app in vals:
        k = app.get("name", "").casefold()
        if k not in chosen or (app.get("recognized") and not chosen[k].get("recognized")):
            chosen[k] = app
    return sorted(chosen.values(), key=lambda a: a.get("name", "").casefold())


def show_message(kind, title, text, parent=None):
    dlg = Gtk.MessageDialog(transient_for=parent, modal=bool(parent),
                            message_type=kind, buttons=Gtk.ButtonsType.OK, text=title)
    if parent is None:
        _prepare_dialog(dlg)
    dlg.format_secondary_text(text)
    dlg.run(); dlg.destroy()


def run_save_warning(app, work=None):
    """Show a prominent save reminder only when an editor requires it."""
    behavior = app.get("behavior", MANUAL_SAVE)
    name = app.get("name", "external application")
    if behavior == AUTO_RENDER:
        heading = f"Before using {name}"
        body = ("Finish editing and close the image. If this application writes a processing sidecar "
                "beside the interchange image, ShellOut can render it and return a NEW layer. "
                "Some desktop launchers cannot report when an editor closes; in that case, "
                "choose Return to GIMP once changes are detected.")
    elif behavior == CLOSE_ONLY:
        heading = f"Before using {name}"
        body = ("Close the editor when finished. ShellOut can return changes that the application "
                "has actually written to disk; unsaved in-memory edits cannot be recovered.")
    elif behavior == MANUAL_SAVE_AS:
        heading = f"SAVE OR EXPORT BEFORE CLOSING {name}"
        body = ("Save or export the finished image into the ShellOut working folder before closing. "
                "ShellOut cannot retrieve edits that exist only in the editor's memory. "
                "The result will be added as a NEW layer.")
    else:
        heading = f"SAVE BEFORE CLOSING {name}"
        shortcut = 'Cmd+S' if sys.platform == 'darwin' else 'Ctrl+S'
        body = (f"Press {shortcut} before closing the editor. ShellOut automatically imports the "
                "saved image as a NEW layer; unsaved changes cannot be recovered. "
                "Your original GIMP layer remains untouched.")
    dlg = _prepare_dialog(Gtk.MessageDialog(
        message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.OK_CANCEL,
        text=heading))
    if behavior in (MANUAL_SAVE, MANUAL_SAVE_AS):
        # Native GTK/Pango markup: the critical warning is deliberately bigger
        # and bold, without overriding the user's Adwaita colours or buttons.
        safe_heading = GLib.markup_escape_text(heading)
        dlg.set_markup(f'<span size="x-large" weight="bold">{safe_heading}</span>')
        if behavior == MANUAL_SAVE:
            shortcut = 'Cmd+S' if sys.platform == 'darwin' else 'Ctrl+S'
            dlg.format_secondary_markup(
                f'<span size="large" weight="bold">Press {shortcut} to save your edits.</span>\n\n'
                'ShellOut imports the saved image as a new layer. '
                'Unsaved changes cannot be recovered. Your original GIMP layer is untouched.')
        else:
            folder = GLib.markup_escape_text(work or 'ShellOut working folder')
            dlg.format_secondary_markup(
                '<span size="large" weight="bold">Save or export the finished image first.</span>\n\n'
                'Export a raster image into this folder:\n'
                f'<tt>{folder}</tt>\n\n'
                'Unsaved changes cannot be recovered. The returned image becomes a new GIMP layer.')
    else:
        dlg.format_secondary_text(body)
    dlg.set_default_response(Gtk.ResponseType.OK)
    result = dlg.run() == Gtk.ResponseType.OK
    dlg.destroy()
    return result


def _snapshot(root):
    out = {}
    for base, _, files in os.walk(root):
        for n in files:
            p = os.path.join(base, n)
            try:
                st = os.stat(p); out[p] = (st.st_mtime_ns, st.st_size)
            except OSError: pass
    return out


def _changed_images(root, before, now=None):
    valid = {'.png', '.tif', '.tiff', '.exr', '.jpg', '.jpeg', '.webp', '.bmp'}
    result = []
    for p, state in (now if now is not None else _snapshot(root)).items():
        # Avoid following an editor-produced symlink outside our workspace.
        if (Path(p).suffix.lower() in valid and before.get(p) != state
                and not os.path.islink(p) and state[1] > 0):
            result.append((state[0], p))
    result.sort(reverse=True)
    return [p for _, p in result]


def _native_launch(app, target):
    """Launch an editor through the host OS without relying on its process lifetime.

    Desktop applications may hand a document to an already-running instance and
    immediately exit.  ShellOut therefore treats launching and completion as two
    separate jobs: the OS opens the document; ShellOut watches the workspace.
    """
    if app.get("custom"):
        cmd = _command_for(app)
        if not cmd:
            raise RuntimeError("No runnable command is stored for this application.")
        # A macOS application bundle is a directory, not an executable file.
        # Launch it via Launch Services, including documents, rather than
        # trying to run Contents/MacOS binaries out of their launch context.
        if sys.platform == 'darwin' and str(cmd[0]).lower().endswith('.app'):
            return subprocess.Popen(['open', '-a', cmd[0], target],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return subprocess.Popen(cmd + [target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=os.environ.copy(), start_new_session=(os.name != 'nt'))

    if _is_xdg_unix():
        # Use GIO itself first.  This is the same desktop application database
        # ShellOut uses for discovery and, unlike spawning the executable, it
        # correctly handles D-Bus activation, Flatpak/portals and applications
        # that hand the document to an already-running instance.  There is no
        # child process to wait for: completion is detected from the workspace.
        desktop_id = str(app.get("desktop_id") or "").strip()
        if desktop_id:
            target_file = Gio.File.new_for_path(target)
            # A GdkAppLaunchContext carries the active display/session details
            # (Wayland/X11 startup context, timestamp, desktop activation).
            # Passing None here is legal Gio, but is not sufficient for every
            # desktop/Qt combination when GIMP itself is the launcher.
            display = Gdk.Display.get_default()
            launch_context = display.get_app_launch_context() if display else None
            if launch_context is not None:
                try:
                    launch_context.set_timestamp(Gtk.get_current_event_time())
                except Exception:
                    pass
            for info in _gio_app_infos():
                try:
                    if info.get_id() == desktop_id:
                        # Gio.launch() may open an application WITHOUT its
                        # document when the desktop registration declares no
                        # file/URI argument. That leaves users waiting forever.
                        if not (info.supports_files() or info.supports_uris()):
                            continue
                        launched = info.launch([target_file], launch_context)
                        if launched is False:
                            raise RuntimeError(f"The desktop could not launch {app.get('name', 'the application')}.")
                        return
                except GLib.Error:
                    # Keep looking/fall through to gtk-launch/direct command.
                    pass

        # gtk-launch is the freedesktop fallback when a saved desktop id is no
        # longer returned by Gio.AppInfo (desktop database quirks do occur).
        launcher = shutil.which("gtk-launch")
        info_found = None
        if desktop_id:
            try:
                info_found = next((i for i in _gio_app_infos()
                                   if i.get_id() == desktop_id), None)
            except (GLib.Error, OSError):
                pass
        # Do not silently drop the document argument: gtk-launch only forwards
        # files when the desktop entry advertises appropriate field codes.
        if desktop_id and launcher and info_found is not None and (info_found.supports_files() or info_found.supports_uris()):
            app_id = desktop_id[:-8] if desktop_id.endswith('.desktop') else desktop_id
            subprocess.Popen([launcher, app_id, Path(target).as_uri()],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             env=os.environ.copy(), start_new_session=True)
            return
    elif sys.platform == "darwin":
        # On macOS, Gio may expose a native application registration. Try that
        # before falling back to Launch Services and bundle identifiers.
        desktop_id = str(app.get('desktop_id') or '').strip()
        if desktop_id:
            for info in _gio_app_infos():
                try:
                    if info.get_id() == desktop_id and (info.supports_files() or info.supports_uris()):
                        info.launch([Gio.File.new_for_path(target)], None)
                        return None
                except (GLib.Error, AttributeError, TypeError):
                    continue
        # Launch Services can target a stable bundle ID without relying on a
        # localised application display name. Do not spawn Contents/MacOS
        # executables directly: their environment and activation vary.
        bundle_id = str(app.get('desktop_id') or '').strip()
        bundle = str(app.get('bundle_path') or '').strip()
        name = str(app.get('name') or '').strip()
        if bundle_id or bundle or name:
            selector = ['-b', bundle_id] if bundle_id else ['-a', bundle or name]
            subprocess.Popen(['open', *selector, target], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return
    elif sys.platform.startswith("win"):
        # Prefer the actual installed application registration (as on Linux).
        # Gio can represent AppInfo IDs on Windows; registry paths are fallback.
        desktop_id = str(app.get('desktop_id') or '').strip()
        if desktop_id:
            for info in _gio_app_infos():
                try:
                    if info.get_id() == desktop_id and (info.supports_files() or info.supports_uris()):
                        info.launch([Gio.File.new_for_path(target)], None)
                        return None
                except (GLib.Error, AttributeError, TypeError):
                    continue
        cmd = _command_for(app)
        if cmd:
            subprocess.Popen(cmd + [target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            return

    # Portable fallback for unusual desktop registrations.
    cmd = _command_for(app)
    if not cmd:
        raise RuntimeError('No runnable desktop or executable command is available for this application.')
    # Do not launch a desktop Exec line containing Flatpak's special @@ file
    # forwarding markers as ordinary subprocess arguments. Requiring a valid
    # installed desktop entry is safer than opening the wrong document.
    if _flatpak_launch_details(app):
        raise RuntimeError('Flatpak desktop registration cannot accept a file. '
                           'Refresh this application in ShellOut Settings or use '
                           'the system-menu application picker.')
    return subprocess.Popen(cmd + [target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=os.environ.copy(), start_new_session=(os.name != "nt"))


def _changed_sidecars(source, before, now=None):
    """Profiles written beside the source image, never global/cache guesses."""
    candidates = [source + suffix for suffix in ('.arp', '.pp3', '.xmp')]
    stem = os.path.splitext(source)[0]
    candidates.extend(stem + suffix for suffix in ('.arp', '.pp3', '.xmp'))
    now = now if now is not None else _snapshot(os.path.dirname(source))
    return [path for path in dict.fromkeys(candidates)
            if path in now and now[path] != before.get(path) and now[path][1] > 0
            and not os.path.islink(path)]


def _launch_with_session(app, source):
    """Use a waitable child when reliable; otherwise desktop activation.

    Flatpak desktop proxies, D-Bus activation and existing app instances can
    detach. In that case no reliable editor-close notification is available.
    """
    cmd = _command_for(app)
    special = app.get('special', '')
    # Native photo editors normally run as distinct processes, and their
    # profiles are automatically written on image close (when configured).
    if special in ('art', 'rawtherapee', 'darktable') and cmd and not _flatpak_launch_details(app):
        # Only a directly invoked native binary can be a useful close signal.
        # Flatpak desktop commands use file-forwarding tokens and may be
        # short-lived activation proxies. Launch those through Gio instead.
        binary = os.path.basename(cmd[0]).casefold()
        if binary not in ('env', 'gtk-launch', 'flatpak', 'flatpak.exe', 'sh', 'bash'):
            try:
                proc = subprocess.Popen(cmd + [source], stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        env=os.environ.copy())
                return proc
            except OSError:
                pass
    return _native_launch(app, source)


def _retry_launch_direct(app, source, work):
    """User-initiated fallback when OS desktop activation did not open the editor.

    Never automatically relaunch after a successful Gio request: that could
    open two editor instances while the user is still making changes. Capture
    stderr on disk for diagnosis and keep it with the recovery workspace.
    """
    cmd = _command_for(app)
    flatpak = _flatpak_launch_details(app)
    if flatpak:
        # Official Flatpak forwarding exports this specific temporary file
        # into the document portal with read/write permissions for the editor.
        # A plain argument would often be inaccessible inside the sandbox.
        cmd = [flatpak[0], 'run', '--file-forwarding', flatpak[1], '@@', source, '@@']
    elif not cmd or not (os.path.isfile(cmd[0]) or shutil.which(cmd[0])):
        # macOS desktop bundles are launched through native Launch Services;
        # direct execution of .app internals is not a portable fallback.
        if sys.platform == 'darwin':
            _native_launch(app, source)
            return None
        raise RuntimeError('No usable direct-launch command is registered for this editor. '
                           'Refresh or re-add it from the system Graphics menu in Settings.')
    if sys.platform == 'darwin':
        _native_launch(app, source)
        return None
    argv = cmd if flatpak else cmd + [source]
    env = os.environ.copy()
    # Correct only a demonstrably invalid X11 display (a Wayland socket name
    # is not an X11 DISPLAY). Leave valid Wayland/Xwayland environments alone.
    if sys.platform.startswith('linux') and env.get('DISPLAY') and (
            env['DISPLAY'] == env.get('WAYLAND_DISPLAY') or
            env['DISPLAY'].startswith('wayland-')):
        env.pop('DISPLAY', None)
        if env.get('QT_QPA_PLATFORM', '').lower() == 'xcb' and env.get('WAYLAND_DISPLAY'):
            env.pop('QT_QPA_PLATFORM', None)
    err_path = os.path.join(work, 'editor_launch_stderr.log')
    err_file = open(err_path, 'ab', buffering=0)
    try:
        return subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=err_file,
                                env=env,
                                start_new_session=(os.name != 'nt'))
    finally:
        err_file.close()


def _wait_for_workspace_save(app, work, before, source, process=None):
    """Monitor results and return automatically only at a trustworthy boundary.

    Untracked desktop activation cannot tell when another application's
    document is closed; do not pretend the launch proxy's exit means that.
    """
    name = app.get('name', 'external editor')
    options = load_settings()
    dlg = _prepare_dialog(Gtk.Dialog(title=f'ShellOut — {name}', modal=True, use_header_bar=False))
    dlg.set_resizable(True)
    dlg.set_default_size(540, 390)
    dlg.add_button('Cancel', Gtk.ResponseType.CANCEL)
    retry = dlg.add_button('Retry opening editor…', Gtk.ResponseType.APPLY)
    retry.set_tooltip_text('Use only if the external editor never opened. Try an alternative launch method and capture startup errors.')
    done = dlg.add_button('Return to GIMP', Gtk.ResponseType.OK)
    done.set_sensitive(False)
    done.set_tooltip_text('Import the detected edited image as a new layer.')
    box = dlg.get_content_area()
    box.set_border_width(16)
    box.set_spacing(12)
    box.pack_start(_shellout_label(name, 'shellout-heading'), False, False, 0)
    heading = _shellout_label('Waiting for editor changes…', 'shellout-heading')
    box.pack_start(heading, False, False, 0)
    description = _shellout_label('Close the editor when finished. ShellOut will check for saved results.', 'shellout-muted')
    description.set_line_wrap(True)
    box.pack_start(description, False, False, 0)
    activity = Gtk.TextView()
    activity.set_editable(False)
    activity.set_cursor_visible(False)
    expander = Gtk.Expander(label='Activity log')
    expander.set_expanded(bool(options.get('show_activity_log', False)))
    scroll = Gtk.ScrolledWindow()
    scroll.set_size_request(-1, 105)
    scroll.add(activity)
    _shellout_style(scroll, 'shellout-panel')
    expander.add(scroll)
    box.pack_start(expander, True, True, 0)

    def log(message):
        buf = activity.get_buffer()
        buf.insert(buf.get_end_iter(), time.strftime('%H:%M:%S  ') + message + '\n')

    state = {'changed': [], 'profiles': [], 'last_signature': None,
             'stable_since': time.monotonic(), 'finished': False,
             'auto_completed': False, 'reported': False,
             'process': process, 'start': time.monotonic(), 'exit_reported': False,
             'launch_method': 'direct' if process else 'desktop',
             'trust_process_exit': bool(process and not _flatpak_launch_details(app))}
    log('Launch request sent; the operating system may accept a request even if the editor fails to start')

    def check_saved():
        if not dlg.get_visible():
            return False
        current_files = _snapshot(work)
        changed = _changed_images(work, before, current_files)
        profiles = (_changed_sidecars(source, before, current_files)
                    if app.get('behavior') == AUTO_RENDER else [])
        state['changed'], state['profiles'] = changed, profiles
        signature = (tuple((path, current_files.get(path)) for path in changed),
                     tuple((path, current_files.get(path)) for path in profiles))
        if signature != state['last_signature']:
            state['last_signature'] = signature
            state['stable_since'] = time.monotonic()
        stable = time.monotonic() - state['stable_since'] >= 1.0
        ready = bool(changed or profiles)
        done.set_sensitive(ready and stable)
        if ready:
            heading.set_text('Changes detected' if stable else 'Waiting for file write to finish…')
            if not state['reported']:
                log('Edited image or processing profile detected')
                state['reported'] = True
        else:
            heading.set_text('Waiting for editor changes…')
            state['reported'] = False
        # Only an observed editor-process exit is a trustworthy close signal.
        # Desktop launch proxies and reused instances do not qualify.
        active = state['process']
        if active is not None and active.poll() is not None:
            if not state['finished']:
                state['finished'] = True
                if active.returncode:
                    log(f'Editor exited with error code {active.returncode}')
                    err_path = os.path.join(work, 'editor_launch_stderr.log')
                    if os.path.isfile(err_path):
                        try:
                            excerpt = Path(err_path).read_bytes()[-1500:].decode('utf-8', 'replace').strip()
                            if excerpt:
                                log('Error: ' + excerpt[:450])
                                expander.set_expanded(True)
                                description.set_text('The editor failed to launch (exit '
                                                     + str(active.returncode) + '). '
                                                     'See its error in Activity log, then retry or cancel.')
                        except OSError:
                            pass
                else:
                    log('Editor process finished (it may have handed off to another instance)')
            if ready and stable:
                if (options.get('return_mode', 'auto') != 'manual'
                        and state['trust_process_exit']
                        and time.monotonic() - state['start'] >= 3.0):
                    state['auto_completed'] = True
                    dlg.response(Gtk.ResponseType.OK)
                    return False
                description.set_text('The editor has closed and its result is ready. '
                                     'Select Return to GIMP to import the new layer.')
            elif active.returncode:
                # Preserve the actionable launch error rather than reverting
                # to a misleading "you forgot to save" status every poll.
                if not description.get_text().startswith('The editor failed to launch'):
                    description.set_text('The editor exited with an error (code '
                                         + str(active.returncode) + '). Open the Activity log '
                                         'or choose Retry opening editor. GIMP is unchanged.')
            else:
                description.set_text('The editor exited without a saved result. If it never opened, '
                                     'use Retry opening editor to see any startup errors. '
                                     'Nothing in GIMP has been changed.')
        elif active is None:
            if time.monotonic() - state['start'] >= 5 and not ready:
                description.set_text('ShellOut sent the launch request but cannot confirm that the editor opened. '
                                     'If the editor did not appear, click Retry opening editor. '
                                     'Otherwise edit and save normally; Return to GIMP enables after a detected result.')
            else:
                description.set_text('Desktop launch requested. Save your work in the editor. '
                                     'ShellOut will enable Return to GIMP once a changed file is detected.')
        return True

    dlg.show_all()
    timer = GLib.timeout_add(max(250, min(1500, int(options.get('poll_interval_ms', 350)))), check_saved)
    check_saved()
    while True:
        response = dlg.run()
        if response != Gtk.ResponseType.APPLY:
            break
        # An explicit user request is required for a second launch because
        # desktop activation gives no proof that the first request failed.
        if state['process'] is not None and state['process'].poll() is None:
            show_message(Gtk.MessageType.INFO, 'Editor is still running',
                         'The directly launched editor has not exited. Check its window before retrying.', dlg)
            continue
        try:
            state['process'] = _retry_launch_direct(app, source, work)
            state['finished'] = False
            state['start'] = time.monotonic()
            state['launch_method'] = 'retry'
            state['trust_process_exit'] = bool(state['process'] and not _flatpak_launch_details(app))
            expander.set_expanded(True)
            log('Alternative launch requested; monitoring for saved changes')
            description.set_text('Alternative launch requested. If the editor still does not open, '
                                 'check the Activity log for startup errors.')
        except (OSError, RuntimeError, GLib.Error) as exc:
            log('Retry failed: ' + str(exc))
            show_message(Gtk.MessageType.ERROR, 'Editor could not launch', str(exc), dlg)
    try:
        GLib.source_remove(timer)
    except Exception:
        pass
    changed = _changed_images(work, before)
    profiles = _changed_sidecars(source, before) if app.get('behavior') == AUTO_RENDER else []
    dlg.destroy()
    return response == Gtk.ResponseType.OK and bool(changed or profiles), changed, profiles


def _pump_wait(cmd, name):
    """Wait for workflows that genuinely require a child process (Blender/Inkscape/sidecars)."""
    err_file = tempfile.TemporaryFile(mode="w+b")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err_file, env=os.environ.copy())
    except (OSError, FileNotFoundError) as e:
        err_file.close()
        raise RuntimeError(f"Could not launch {name}: {e}")
    while proc.poll() is None:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.05)
    err_file.seek(0)
    stderr = err_file.read(16384).decode("utf-8", "replace").strip()
    err_file.close()
    if proc.returncode != 0:
        detail = stderr[-4000:] if stderr else f"Process exited with status {proc.returncode}."
        raise RuntimeError(f"{name} failed to launch or exited with an error.\n\n{detail}")
    return proc.returncode


def _command_for(app):
    command = app.get("command")
    if isinstance(command, list) and command:
        return [str(x) for x in command]
    text = app.get("commandline", "")
    try:
        return shlex.split(text, posix=(os.name != 'nt'))
    except ValueError:
        return []

def _flatpak_launch_details(app):
    """Identify a Flatpak installation even when Exec starts with /usr/bin/flatpak.

    A desktop entry commonly looks like:
    /usr/bin/flatpak run --branch=stable --command=ART --file-forwarding
    us.pixls.art.ART @@ %f @@
    The executable's basename, not its literal spelling, identifies Flatpak.
    """
    command = _command_for(app)
    flatpak_exe = next((part for part in command[:2]
                        if os.path.basename(str(part)).lower() in ('flatpak', 'flatpak.exe')), None)
    if flatpak_exe is None:
        return None
    desktop_id = str(app.get('desktop_id') or '').removesuffix('.desktop')
    # Desktop IDs provide the authoritative app identity. Avoid mistaking
    # arbitrary command arguments (e.g. --command=ART) for application IDs.
    if not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+', desktop_id):
        try:
            run_pos = command.index('run')
        except ValueError:
            return None
        # Prefer an application-looking ID among the positional arguments;
        # skip option values used with --branch, --arch, etc.
        rest = command[run_pos + 1:]
        skip_next = False
        desktop_id = ''
        for part in rest:
            if skip_next:
                skip_next = False
                continue
            if part in ('--branch', '--arch', '--command', '--installation'):
                skip_next = True
                continue
            if part.startswith('-') or part in ('@@', '@@u', '@@f'):
                continue
            if re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+', part):
                desktop_id = part
                break
    if not desktop_id:
        return None
    return flatpak_exe, desktop_id


def _sidecar_cli_candidates(app, source, output, profile):
    """Find available CLI entry points for native, Flatpak and AppImage builds.

    Returns an ordered list; no shell invocations. Keep the editor's own CLI
    first when it is recognisable, then try a compatible native installation.
    """
    special = app.get('special', '')
    expected = {'art': '.arp', 'rawtherapee': '.pp3', 'darktable': '.xmp'}.get(special)
    if not expected or not profile or not profile.lower().endswith(expected):
        return []

    cli_names = {
        'art': ('ART-cli', 'art-cli'),
        'rawtherapee': ('rawtherapee-cli',),
        'darktable': ('darktable-cli',),
    }[special]
    candidates = []
    seen = set()

    def add(prefix, label):
        key = tuple(prefix)
        if key not in seen:
            seen.add(key)
            candidates.append((prefix, label))

    flatpak = _flatpak_launch_details(app) if sys.platform.startswith('linux') else None
    if flatpak:
        flatpak_exe, app_id = flatpak
        for cli in cli_names:
            add([flatpak_exe, 'run', '--command=' + cli, app_id],
                f'{app_id} Flatpak ({cli})')

    # The native CLI may live alongside the GUI executable but not in PATH,
    # e.g. locally unpacked ART/RawTherapee installations.
    command = _command_for(app)
    paths = []
    for entry in (app.get('executable'), command[0] if command else None):
        if entry and os.path.isfile(str(entry)):
            paths.append(Path(str(entry)).resolve().parent)
    for folder in paths:
        for cli in cli_names:
            exe = folder / (cli + ('.exe' if os.name == 'nt' else ''))
            if exe.is_file() and os.access(exe, os.X_OK):
                add([str(exe)], f'installed alongside {app.get("name", "editor")}')
    for cli in cli_names:
        exe = shutil.which(cli) or (shutil.which(cli + '.exe') if os.name == 'nt' else None)
        if exe:
            add([exe], f'native {cli}')

    # Official ART AppImages expose ART-cli through the AppRun --cli switch;
    # this is the documented ART bundling convention, not a universal AppImage
    # guarantee. Only attempt it for an ART app whose executable is an AppImage.
    if special == 'art' and not flatpak:
        for entry in (app.get('executable'), command[0] if command else None):
            if entry and str(entry).lower().endswith('.appimage') and os.path.isfile(str(entry)):
                add([str(entry), '--cli'], 'ART AppImage CLI')

    if special == 'darktable':
        return [(prefix + [source, profile, output], label) for prefix, label in candidates]

    # ART/RawTherapee CLI accept explicit processing profiles. The -c source
    # option must be last. TIFF is deliberately the universal render exchange
    # format here: a user's image may be PNG/WebP/EXR but the CLI may not
    # support those outputs. Import the 16-bit TIFF directly into GIMP.
    return [(prefix + ['-o', output, '-p', profile, '-Y', '-t', '-b16', '-c', source], label)
            for prefix, label in candidates]


def _run_cli_responsive(argv, timeout=600):
    """Run a render tool without blocking GTK for minutes.

    stderr is kept on disk, not buffered in a PIPE that can deadlock when
    the renderer prints a lot of diagnostic output. The child is terminated
    on timeout, with a short kill fallback. No shell is involved.
    """
    with tempfile.TemporaryFile(mode='w+b') as errors:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=errors)
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(argv, timeout)
                time.sleep(0.07)
        except BaseException:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
            raise
        errors.seek(0)
        tail = errors.read(32768)
    return subprocess.CompletedProcess(argv, proc.returncode, stderr=tail)


def _render_sidecar_image(app, source, output, profile):
    """Render edited profile, preserving the session on ALL failures."""
    candidates = _sidecar_cli_candidates(app, source, output, profile)
    if not candidates:
        label = app.get('name', 'this editor')
        raise RuntimeError(
            f'No compatible renderer was found for {label}. '
            'ShellOut looked for its native command-line tool, a sibling '
            'installation and (where applicable) its Flatpak or AppImage CLI. '
            'The processing profile remains in the recovery workspace.')

    failures = []
    for cli, label in candidates:
        try:
            completed = _run_cli_responsive(cli, timeout=600)
            if completed.returncode == 0 and os.path.isfile(output) and os.path.getsize(output) > 0:
                return output
            stderr = (completed.stderr or b'').decode('utf-8', 'replace').strip()[-900:]
            failures.append(f'{label}: exit {completed.returncode}; {stderr or "no usable output"}')
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f'{label}: {exc}')
        # Remove an incomplete file before attempting another backend.
        try:
            if os.path.exists(output):
                os.remove(output)
        except OSError:
            pass
    raise RuntimeError('ART/RAW profile rendering failed. The edited profile is preserved.\n'
                       + '\n'.join(failures[-4:]))


def shellout(image, drawables, app):
    drawable = drawables[0] if drawables else None
    if not isinstance(drawable, Gimp.Layer):
        show_message(Gtk.MessageType.WARNING, "ShellOut", "Select a normal image layer first.")
        return False
    try:
        os.makedirs(CACHE_ROOT, mode=0o700, exist_ok=True)
        work = tempfile.mkdtemp(prefix='session_', dir=CACHE_ROOT)
    except OSError as exc:
        show_message(Gtk.MessageType.ERROR, 'ShellOut cannot create a working folder',
                     f'Please check free disk space and cache permissions.\n\n{exc}')
        return False
    if not run_save_warning(app, work):
        shutil.rmtree(work, ignore_errors=True)
        return False
    ext = str(app.get('ext') or '.png').lower()
    # Old, migrated or hand-edited settings may contain arbitrary extensions.
    # Never allow values containing path separators to escape CACHE_ROOT.
    allowed = {'.png', '.tif', '.tiff', '.exr', '.jpg', '.jpeg', '.webp', '.bmp'}
    if ext not in allowed:
        show_message(Gtk.MessageType.ERROR, 'Invalid interchange format',
                     'Select PNG, TIFF, EXR, JPEG or WebP in this editor’s Settings.')
        shutil.rmtree(work, ignore_errors=True)
        return False
    source = os.path.join(work, 'gimp_input' + ext)
    temp_image = None
    success = False
    keep = False
    try:
        Gimp.progress_init(f"ShellOut: preparing {app.get('name', 'editor')}…")
        source_base = image.get_base_type()
        # An empty indexed image has no source colormap. Export indexed
        # drawables through RGB instead so colours cannot be reinterpreted
        # as palette indices from a missing palette.
        export_base = (Gimp.ImageBaseType.RGB if source_base == Gimp.ImageBaseType.INDEXED
                       else source_base)
        temp_image = Gimp.Image.new(image.get_width(), image.get_height(), export_base)
        if temp_image is None:
            raise RuntimeError('GIMP could not create a temporary interchange image.')
        # New GIMP images start at their default pixel precision and profile.
        # That would otherwise silently reduce high-bit-depth source images.
        if source_base != Gimp.ImageBaseType.INDEXED and temp_image.get_precision() != image.get_precision():
            if not temp_image.convert_precision(image.get_precision()):
                raise RuntimeError('Could not preserve source image precision for interchange.')
        color_profile = image.get_color_profile()
        if color_profile is not None and not temp_image.set_color_profile(color_profile):
            raise RuntimeError('Could not preserve source image colour profile for interchange.')
        layer = Gimp.Layer.new_from_drawable(drawable, temp_image)
        if layer is None or not temp_image.insert_layer(layer, None, 0):
            raise RuntimeError('GIMP could not copy the selected layer into the interchange image.')
        # new_from_drawable does NOT automatically carry layer offsets.
        offsets = drawable.get_offsets()
        if len(offsets) == 3:
            _, offset_x, offset_y = offsets
        else:
            offset_x, offset_y = offsets[-2:]
        layer.set_offsets(int(offset_x), int(offset_y))
        # Preserve alpha for normal interchange formats. Flatten only formats
        # that cannot represent alpha reliably. This avoids an unnecessary
        # full-image operation for the common PNG/TIFF/EXR/WebP paths.
        if ext.lower() in (".jpg", ".jpeg", ".bmp"):
            temp_image.flatten()
        if not Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, temp_image, Gio.File.new_for_path(source), None):
            raise RuntimeError('GIMP could not export the interchange image.')
        if not os.path.isfile(source) or os.path.getsize(source) == 0:
            raise RuntimeError('GIMP reported an export but created no usable interchange image.')
        temp_image.delete(); temp_image = None
        before = _snapshot(work)
        base = _command_for(app)
        if not base and not app.get("desktop_id"):
            raise RuntimeError("No runnable command or desktop registration is stored for this application. Open ShellOut Settings and refresh applications.")

        if app.get("special") == "inkscape":
            # Give Inkscape a real SVG project in our workspace from the start.
            # Ctrl+S therefore saves safely to ShellOut's workspace; after close
            # we export that SVG back to a raster image automatically.
            svg = os.path.join(work, "shellout_work.svg")
            href = Path(source).as_uri().replace("&", "&amp;").replace('"', "&quot;")
            Path(svg).write_text(
                f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                f'width="{image.get_width()}" height="{image.get_height()}" viewBox="0 0 {image.get_width()} {image.get_height()}">\n'
                f'<image x="0" y="0" width="{image.get_width()}" height="{image.get_height()}" href="{href}"/>\n</svg>\n',
                encoding="utf-8")
            before = _snapshot(work)  # include our generated project in baseline
            cmd = base + [svg]
            keep = True
        elif app.get("special") == "blender":
            blend = os.path.join(work, "shellout_work.blend")
            script = os.path.join(work, "shellout_blender.py")
            Path(script).write_text(
                "import bpy, atexit\n"
                f"src={source!r}\nproject={blend!r}\n"
                "try: img=bpy.data.images.load(src, check_existing=False)\nexcept Exception: img=None\n"
                "if img:\n"
                "  for w in bpy.context.window_manager.windows:\n"
                "    for a in w.screen.areas:\n"
                "      if a.type=='IMAGE_EDITOR': a.spaces.active.image=img\n"
                "try: bpy.ops.wm.save_as_mainfile(filepath=project)\nexcept Exception: pass\n"
                "def save_project():\n"
                "  try: bpy.ops.wm.save_as_mainfile(filepath=project)\n"
                "  except Exception: pass\n"
                "atexit.register(save_project)\n", encoding="utf-8")
            cmd = base + ["--python", script]
            keep = True
        else:
            cmd = None

        Gimp.progress_set_text(f"ShellOut: opening {app.get('name')}…")
        Gimp.progress_update(0.5)
        watched_changed = None
        watched_profiles = []
        if cmd is None:
            process = _launch_with_session(app, source)
            accepted, watched_changed, watched_profiles = _wait_for_workspace_save(
                app, work, before, source, process)
            if not accepted:
                keep = True
                return False
        else:
            _pump_wait(cmd, app.get("name", "application"))

        required_result = None
        if app.get("special") == "inkscape":
            rendered = os.path.join(work, "shellout_rendered" + ext)
            svg = os.path.join(work, "shellout_work.svg")
            # Rendering the initial (unmodified) project is not an edit.
            if _snapshot(work).get(svg) == before.get(svg):
                raise RuntimeError('Inkscape closed without saving changes to the SVG project. '
                                   'The source layer has not been modified.')
            exported = _run_cli_responsive(base + [svg, '--export-filename=' + rendered],
                                           timeout=120)
            if exported.returncode or not os.path.isfile(rendered) or os.path.getsize(rendered) == 0:
                detail = (exported.stderr or b'').decode('utf-8', 'replace')[-1200:]
                raise RuntimeError('Inkscape could not render the edited SVG project. ' + detail)
            required_result = rendered
        elif app.get("behavior") == AUTO_RENDER:
            # Always use a 16-bit TIFF interchange for ART and RawTherapee;
            # do not pass their CLI an extension it cannot actually produce.
            rendered = os.path.join(work, "shellout_rendered.tif")
            profiles = watched_profiles if watched_profiles else _changed_sidecars(source, before)
            expected = {"art": ".arp", "rawtherapee": ".pp3", "darktable": ".xmp"}.get(app.get("special"))
            profile = next((p for p in profiles if p.lower().endswith(expected or '.unmatched')), None)
            if not profile:
                raise RuntimeError('No matching updated processing profile was found. '
                                   'Configure the external editor to write sidecars beside the opened image.')
            _render_sidecar_image(app, source, rendered, profile)
            required_result = rendered

        changed = _changed_images(work, before)
        if required_result is not None:
            result = required_result
        elif app.get("behavior") == MANUAL_SAVE_AS:
            new_files = [p for p in changed if os.path.abspath(p) != os.path.abspath(source)]
            result = new_files[0] if new_files else (source if source in changed else None)
        else:
            result = source if source in changed else (changed[0] if changed else None)
        if not result or not os.path.isfile(result):
            keep = True
            show_message(Gtk.MessageType.WARNING, "No saved output detected",
                         f"{app.get('name')} finished, but ShellOut could not find a saved image to return.\n\n"
                         f"Nothing in GIMP was changed. Recovery workspace kept at:\n{work}")
            return False

        Gimp.progress_set_text("ShellOut: importing new layer…")
        # The documented GIMP 3 API loads directly as a layer into the
        # destination image. Avoid materialising a second full-sized Gimp.Image
        # and another expensive new_from_drawable() copy on every return.
        image.undo_group_start()
        try:
            new_layer = Gimp.file_load_layer(
                Gimp.RunMode.NONINTERACTIVE, image, Gio.File.new_for_path(result))
            if not new_layer:
                raise RuntimeError('GIMP could not load the saved result as a layer.')
            new_layer.set_name(f"{app.get('name', 'ShellOut')} Output")
            if not image.insert_layer(new_layer, drawable.get_parent(),
                                      image.get_item_position(drawable)):
                raise RuntimeError('GIMP could not insert the returned layer.')
        finally:
            image.undo_group_end()
        Gimp.displays_flush()
        success = True
        return True
    except Exception as e:
        keep = True
        show_message(Gtk.MessageType.ERROR, "ShellOut error", f"{e}\n\nWorkspace kept at:\n{work}")
        return False
    finally:
        for obj in (temp_image,):
            if obj:
                try: obj.delete()
                except Exception: pass
        try: Gimp.progress_end()
        except Exception: pass
        # Successful Blender sessions retain the .blend project by design.
        if success and not keep:
            shutil.rmtree(work, ignore_errors=True)


def _add_from_system_menu(parent, settings):
    """Choose an installed application by NAME and ICON, not an executable path.

    On freedesktop Linux/BSD systems, Gio reads .desktop menu entries;
    Graphics appears first and All Applications exposes other registrations.
    Windows/macOS also use Gio when registered, plus native discovery.
    Unregistered AppImages must be selected with Browse Executable.
    """
    try:
        # Use registered system applications for ALL supported operating
        # systems, including freedesktop-based BSD installations. Supplement
        # with native Windows/macOS discovery for programs missing from Gio.
        entries = []
        for info in _gio_app_infos(visible_only=True):
            rec = _desktop_app_record(info, True)
            if rec is not None:
                entries.append(rec)
        existing = {r['id'] for r in entries}
        for rec in discover_graphics_apps():
            if rec['id'] not in existing:
                entries.append(rec)
                existing.add(rec['id'])
    except (GLib.Error, OSError, ValueError, TypeError) as exc:
        show_message(Gtk.MessageType.ERROR, 'Could not read installed applications', str(exc), parent)
        return False
    # Keep a truthful categorisation: missing categories are not assumed to
    # mean Graphics, but named editors are also listed as relevant suggestions.
    entries.sort(key=lambda rec: (rec['name'].casefold(), rec.get('id', '')))
    graphics_entries = [rec for rec in entries
                        if 'Graphics' in rec.get('menu_categories', ())
                        or bool(_profile_for(rec.get('name', ''),
                                             rec.get('executable', ''), rec.get('desktop_id', '')))]
    graphics_ids = {rec['id'] for rec in graphics_entries}

    chooser = _prepare_dialog(Gtk.Dialog(title='Add from Graphics Menu',
                                         transient_for=parent, modal=True, use_header_bar=False))
    chooser.set_resizable(True)
    chooser.add_button('_Cancel', Gtk.ResponseType.CANCEL)
    select_button = chooser.add_button('_Add Selected', Gtk.ResponseType.OK)
    select_button.set_sensitive(False)
    select_button.set_tooltip_text('Add this installed application to ShellOut.')
    outer = chooser.get_content_area()
    outer.set_spacing(10)
    outer.set_border_width(14)
    description = _shellout_label('Choose an editor from your system Graphics menu. No executable path needed.', 'shellout-muted')
    description.set_line_wrap(True)
    outer.pack_start(description, False, False, 0)
    category = Gtk.ComboBoxText()
    linux_menu = _is_xdg_unix()
    category.append('graphics', 'Graphics menu' if linux_menu else 'Recognised graphics editors')
    category.append('all', 'All installed applications')
    category.set_active_id('graphics' if linux_menu and graphics_entries else 'all')
    category.set_tooltip_text('Graphics shows applications registered under the system Graphics menu. Switch to All to find other installed tools.')
    outer.pack_start(category, False, False, 0)
    search = Gtk.SearchEntry()
    search.set_placeholder_text('Search Graphics applications…')
    search.set_tooltip_text('Search the applications registered with your operating system.')
    outer.pack_start(search, False, False, 0)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_overlay_scrolling(False)
    scroller.set_vexpand(True)
    outer.pack_start(scroller, True, True, 0)
    listing = Gtk.ListBox()
    listing.set_selection_mode(Gtk.SelectionMode.SINGLE)
    scroller.add(listing)
    by_row = {}
    info_map = _shellout_appinfo_map()
    for rec in entries:
        row = Gtk.ListBoxRow()
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        line.set_margin_top(7)
        line.set_margin_bottom(7)
        line.set_margin_start(10)
        line.set_margin_end(10)
        line.pack_start(_shellout_icon(rec, info_map, 28), False, False, 0)
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        details.pack_start(_shellout_label(rec['name']), False, False, 0)
        source = rec.get('desktop_id') or rec.get('executable') or rec.get('commandline') or ''
        hint = _shellout_label(source, 'shellout-muted')
        hint.set_ellipsize(3)
        hint.set_max_width_chars(72)
        hint.set_tooltip_text(source)
        details.pack_start(hint, False, False, 0)
        line.pack_start(details, True, True, 0)
        row.add(line)
        listing.add(row)
        by_row[row] = rec

    def filter_row(row, _data=None):
        term = search.get_text().strip().casefold()
        rec = by_row[row]
        if category.get_active_id() == 'graphics' and rec['id'] not in graphics_ids:
            return False
        return not term or term in (rec['name'] + ' ' + rec.get('desktop_id', '')).casefold()

    listing.set_filter_func(filter_row)
    category.connect('changed', lambda _combo: listing.invalidate_filter())
    search.connect('search-changed', lambda _entry: listing.invalidate_filter())
    listing.connect('row-selected', lambda _list, row: select_button.set_sensitive(row in by_row))
    listing.connect('row-activated', lambda _list, row: chooser.response(Gtk.ResponseType.OK))
    chooser.show_all()
    _screen_limited(chooser, width=650, desired_height=540)
    # No desktop registration exists for an unintegrated AppImage. Avoid
    # advertising that selecting it here is possible until it is integrated.
    if not entries:
        show_message(Gtk.MessageType.INFO, 'No registered applications',
                     'No applications were returned by the system application menu. '
                     'Unregistered AppImages can still be selected using Browse Executable in Settings.', chooser)
    result = chooser.run()
    selected_row = listing.get_selected_row()
    selected = by_row.get(selected_row) if result == Gtk.ResponseType.OK else None
    chooser.destroy()
    if not selected:
        return False
    key = selected['id']
    old = settings.setdefault('apps', {}).get(key, {})
    selected.update({k: old[k] for k in ('ext', 'behavior', 'enabled') if k in old})
    selected['enabled'] = True
    # An explicit user choice overrides the automatic discovery shortlist.
    settings['apps'][key] = selected
    return True


def _add_macos_bundle(parent, settings):
    """Allow picking a macOS .app bundle without an executable path.

    File chooser treats .app as a folder in some GTK builds. The user can
    choose the enclosing application folder; Launch Services opens the bundle.
    This is only a fallback for applications absent from registered results.
    """
    if sys.platform != 'darwin':
        return False
    chooser = Gtk.FileChooserDialog(title='Select macOS Application Bundle',
                                    transient_for=parent,
                                    action=Gtk.FileChooserAction.SELECT_FOLDER)
    chooser.add_buttons('_Cancel', Gtk.ResponseType.CANCEL, '_Add', Gtk.ResponseType.OK)
    selected = chooser.get_filename() if chooser.run() == Gtk.ResponseType.OK else None
    chooser.destroy()
    if not selected:
        return False
    bundle = Path(selected)
    if bundle.suffix.lower() != '.app' or not (bundle / 'Contents' / 'Info.plist').is_file():
        show_message(Gtk.MessageType.WARNING, 'Select a macOS application',
                     'Choose an installed .app bundle, not an ordinary folder.', parent)
        return False
    try:
        with (bundle / 'Contents' / 'Info.plist').open('rb') as fh:
            meta = plistlib.load(fh)
    except (OSError, ValueError, TypeError) as exc:
        show_message(Gtk.MessageType.ERROR, 'Could not read application', str(exc), parent)
        return False
    name = str(meta.get('CFBundleDisplayName') or meta.get('CFBundleName') or bundle.stem)
    key = 'custom:macos-bundle:' + _slug(str(bundle.resolve()))
    profile = _profile_for(name, '', str(meta.get('CFBundleIdentifier') or ''))
    settings.setdefault('apps', {})[key] = {
        'id': key, 'name': name, 'desktop_id': '',
        'bundle_path': str(bundle), 'executable': str(bundle),
        'command': [str(bundle)], 'commandline': shlex.quote(str(bundle)),
        'ext': profile.get('ext', '.png') if profile else '.png',
        'behavior': profile.get('behavior', MANUAL_SAVE) if profile else MANUAL_SAVE,
        'special': profile.get('special', '') if profile else '',
        'recognized': bool(profile), 'likely_viewer': False,
        'custom': True, 'enabled': True, 'source': 'macos-manual-bundle',
        'platform': 'darwin',
    }
    return True


def _add_manual_program(parent, settings):
    chooser = Gtk.FileChooserDialog(title="Add Application Executable", transient_for=parent,
                                    action=Gtk.FileChooserAction.OPEN)
    chooser.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Add", Gtk.ResponseType.OK)
    path = chooser.get_filename() if chooser.run() == Gtk.ResponseType.OK else None
    chooser.destroy()
    if not path: return False
    name = os.path.basename(path)
    key = "custom:" + _slug(name) + ":" + str(GLib.get_real_time())
    settings["apps"][key] = {"id": key, "name": name, "desktop_id": "", "executable": path,
        "command": [path], "commandline": (subprocess.list2cmdline([path]) if os.name == 'nt' else shlex.quote(path)),
        "ext": ".png", "behavior": MANUAL_SAVE,
        "special": "", "recognized": False, "likely_viewer": False, "custom": True, "enabled": True}
    return True


def _shellout_style(widget, *classes):
    """Tag only this widget; the host GTK theme supplies all visual colours."""
    context = widget.get_style_context()
    for name in classes:
        context.add_class(name)
    return widget


def _shellout_label(text, style=None):
    """Native GTK label; only title/section emphasis uses standard Pango markup."""
    lbl = Gtk.Label(xalign=0)
    escaped = GLib.markup_escape_text(str(text))
    if style == 'shellout-heading':
        lbl.set_markup('<span weight="bold" size="large">' + escaped + '</span>')
    elif style == 'shellout-section':
        lbl.set_markup('<span weight="bold">' + escaped + '</span>')
    elif style == 'shellout-muted':
        lbl.set_markup('<span size="small">' + escaped + '</span>')
    else:
        lbl.set_text(str(text))
    if style:
        _shellout_style(lbl, style)
    return lbl


def _shellout_appinfo_map():
    """Resolve installed application IDs ONCE per Settings opening, not per row.

    Gimp is never consulted for program icons: application GIcons are read
    from the desktop registry and handed to GTK's icon renderer.
    """
    result = {}
    for info in _gio_app_infos():
        try:
            if info.get_id():
                result[info.get_id()] = info
        except (GLib.Error, AttributeError, TypeError):
            continue
    return result


def _shellout_icon(app, info_map=None, size=28):
    """Prefer the REAL system application icon; no program-specific artwork."""
    image = Gtk.Image()
    desktop_id = app.get('desktop_id') or ''
    info = (info_map or {}).get(desktop_id)
    if info is not None:
        try:
            icon = info.get_icon()
            if icon is not None:
                image.set_from_gicon(icon, Gtk.IconSize.DIALOG)
                image.set_pixel_size(size)
                return image
        except (GLib.Error, TypeError):
            pass
    # Some application icons are present in the icon theme even when their
    # desktop metadata has disappeared (for example, manual executables).
    theme = Gtk.IconTheme.get_default()
    names = [app.get('icon_name', ''), desktop_id.removesuffix('.desktop'),
             os.path.basename(str(app.get('executable') or '')).casefold(),
             _slug(app.get('name', '')).replace('-', '')]
    if theme:
        for name in names:
            if not name:
                continue
            try:
                if theme.has_icon(name):
                    image.set_from_icon_name(name, Gtk.IconSize.DIALOG)
                    image.set_pixel_size(size)
                    return image
            except (GLib.Error, TypeError):
                pass
    image.set_from_icon_name('application-x-executable-symbolic', Gtk.IconSize.DIALOG)
    image.set_pixel_size(size)
    return image


def _shellout_available(app, info_map):
    """Do not call an absent executable 'Found' just because JSON has a path."""
    info = info_map.get(app.get('desktop_id') or '')
    if info is not None and (info.supports_files() or info.supports_uris()):
        return True
    exe = str(app.get('executable') or '').strip()
    if exe and (os.path.isfile(exe) or shutil.which(exe)
                or (sys.platform == 'darwin' and exe.lower().endswith('.app')
                    and os.path.isdir(exe))):
        return True
    parts = _command_for(app)
    return bool(parts and not _flatpak_launch_details(app)
                and (os.path.isfile(parts[0]) or shutil.which(parts[0])))


def _screen_limited(dialog, width=760, desired_height=650, height_fraction=.8):
    """Natural initial size, maximum 80% workarea height, horizontal resize."""
    w, h = 1200, 900
    try:
        display = Gdk.Display.get_default()
        monitor = (display.get_primary_monitor() or display.get_monitor(0)) if display else None
        if monitor:
            workarea = monitor.get_workarea()
            w, h = workarea.width, workarea.height
    except (AttributeError, GLib.Error):
        pass
    max_width = max(300, w - 24)
    max_height = max(240, int(h * height_fraction))
    dialog.set_resizable(True)
    dialog.set_default_size(min(width, max_width), min(desired_height, max_height))
    # Size hints are not always enforced by Wayland compositors, so the
    # initial size is capped independently above as well.
    try:
        geometry = Gdk.Geometry()
        geometry.max_width = max_width
        geometry.max_height = max_height
        dialog.set_geometry_hints(None, geometry, Gdk.WindowHints.MAX_SIZE)
    except (AttributeError, TypeError):
        pass


def _shellout_list_row(app, index, info_map, *, settings_mode=False, on_details=None):
    """Two-line app identity with alternating neutral GTK list-row backgrounds.

    The main launcher has NO individual action or toggle buttons: selecting
    its row makes the one footer action (Open with) available.
    """
    row = Gtk.ListBoxRow()
    _shellout_style(row, 'shellout-list-row',
                    'shellout-alt-odd' if index % 2 else 'shellout-alt-even')
    line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    line.set_margin_start(12)
    line.set_margin_end(10)
    line.set_margin_top(9)
    line.set_margin_bottom(9)
    line.pack_start(_shellout_icon(app, info_map, 28), False, False, 0)
    identity = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    name = _shellout_label(app.get('name') or 'Application')
    name.set_ellipsize(3)
    name.set_tooltip_text(app.get('name') or 'Application')
    identity.pack_start(name, False, False, 0)
    available = _shellout_available(app, info_map)
    if settings_mode:
        caption = ('Enabled' if app.get('enabled') else 'Disabled')
        if not available:
            caption += ' · Not found'
        elif app.get('source') == 'desktop':
            caption += ' · System application'
        else:
            caption += ' · Added application'
    else:
        caption = 'Installed application' if available else 'Not currently available — see Settings'
    sub = _shellout_label(caption, 'shellout-muted')
    identity.pack_start(sub, False, False, 0)
    identity.set_hexpand(True)
    line.pack_start(identity, True, True, 0)
    if settings_mode:
        if not app.get('enabled'):
            _shellout_style(name, 'shellout-disabled-text')
            name.set_sensitive(False)
        button = Gtk.Button.new_from_icon_name('emblem-system-symbolic', Gtk.IconSize.BUTTON)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_tooltip_text('Open settings for ' + str(app.get('name') or 'this editor')
                                + ': enable/disable, executable, file format and save workflow')
        button.connect('clicked', lambda _button: on_details(app))
        line.pack_end(button, False, False, 0)
        row.set_activatable(False)
        row.set_selectable(False)
    else:
        row.set_sensitive(available)
        row.set_tooltip_text('Select this editor; then choose Open with'
                             if available else 'Configure the application in Settings')
    row.add(line)
    return row


def _editor_preferences_dialog(parent, app, info_map):
    """Application-specific details, intentionally absent from launcher rows."""
    editor = _prepare_dialog(Gtk.Dialog(title='ShellOut — ' + app.get('name', 'Editor'),
                                        transient_for=parent, modal=True, use_header_bar=False))
    editor.add_button('_Cancel', Gtk.ResponseType.CANCEL)
    apply = editor.add_button('_Apply', Gtk.ResponseType.OK)
    apply.set_tooltip_text('Apply this editor’s activation and interchange settings to the Settings draft')
    body = editor.get_content_area()
    body.set_spacing(12)
    body.set_border_width(16)

    heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    heading.pack_start(_shellout_icon(app, info_map, 32), False, False, 0)
    heading.pack_start(_shellout_label(app.get('name', 'Application'), 'shellout-heading'), True, True, 0)
    body.pack_start(heading, False, False, 0)

    enabled = Gtk.CheckButton(label='Enabled in ShellOut')
    enabled.set_active(bool(app.get('enabled')))
    enabled.set_tooltip_text('Enabled applications appear in the main ShellOut chooser. Disabled ones stay in Settings only.')
    body.pack_start(enabled, False, False, 0)

    grid = Gtk.Grid(row_spacing=10, column_spacing=12)
    body.pack_start(grid, False, False, 0)
    source = app.get('desktop_id') or app.get('source') or 'Manual application'
    source_label = _shellout_label(source, 'shellout-muted')
    source_label.set_selectable(True)
    grid.attach(_shellout_label('Registered application'), 0, 0, 1, 1)
    grid.attach(source_label, 1, 0, 2, 1)

    executable = Gtk.Entry()
    initial_path = str(app.get('executable') or '')
    executable.set_text(initial_path)
    executable.set_hexpand(True)
    executable.set_tooltip_text('Executable path supplied by the operating system. Usually no changes are necessary.')
    browse = Gtk.Button(label='Browse…')
    browse.set_tooltip_text('Optional: choose an executable manually, for example an unregistered AppImage.')
    def browse_clicked(_button):
        chooser = Gtk.FileChooserDialog(title='Select Application Executable',
                                        transient_for=editor, action=Gtk.FileChooserAction.OPEN)
        chooser.add_buttons('_Cancel', Gtk.ResponseType.CANCEL, '_Select', Gtk.ResponseType.OK)
        if chooser.run() == Gtk.ResponseType.OK and chooser.get_filename():
            executable.set_text(chooser.get_filename())
        chooser.destroy()
    browse.connect('clicked', browse_clicked)
    grid.attach(_shellout_label('Executable'), 0, 1, 1, 1)
    grid.attach(executable, 1, 1, 1, 1)
    grid.attach(browse, 2, 1, 1, 1)

    fmt = Gtk.ComboBoxText()
    formats = ('.png', '.tif', '.exr', '.jpg', '.webp')
    for extension in formats:
        fmt.append(extension, extension.upper().lstrip('.'))
    fmt.set_active_id(app.get('ext') if app.get('ext') in formats else '.png')
    fmt.set_tooltip_text('Raster format used to send the layer to the editor; choose a format it can open.')
    grid.attach(_shellout_label('Interchange format'), 0, 2, 1, 1)
    grid.attach(fmt, 1, 2, 2, 1)

    workflow = Gtk.ComboBoxText()
    for key, label in BEHAVIOR_LABELS.items():
        workflow.append(key, label)
    workflow.set_active_id(app.get('behavior', MANUAL_SAVE))
    workflow.set_tooltip_text('Select whether this editor saves its interchange image or writes a processing sidecar.')
    grid.attach(_shellout_label('Save workflow'), 0, 3, 1, 1)
    grid.attach(workflow, 1, 3, 2, 1)

    command = _shellout_label(app.get('commandline') or 'Launched through registered application', 'shellout-muted')
    command.set_line_wrap(True)
    command.set_selectable(True)
    command.set_max_width_chars(55)
    command.set_tooltip_text('Current desktop application command; not normally something users need to edit.')
    grid.attach(_shellout_label('Detected command'), 0, 4, 1, 1)
    grid.attach(command, 1, 4, 2, 1)

    _screen_limited(editor, width=650, desired_height=390)
    editor.show_all()
    accepted = False
    while editor.run() == Gtk.ResponseType.OK:
        new_path = executable.get_text().strip()
        is_macos_bundle = (sys.platform == 'darwin' and new_path.lower().endswith('.app')
                           and Path(new_path).is_dir())
        if new_path != initial_path and new_path and not (os.path.isfile(new_path)
                                                          or shutil.which(new_path)
                                                          or is_macos_bundle):
            show_message(Gtk.MessageType.ERROR, 'Executable not found',
                         'Choose an installed executable or leave the existing path unchanged.', editor)
            continue  # Keep this dialog open so the user can correct the path.
        if new_path != initial_path and new_path:
            # An explicit override launches this executable directly. Retain
            # the stable app ID and fallback icon for the Settings list.
            app['executable'] = new_path
            app['command'] = [new_path]
            app['commandline'] = (subprocess.list2cmdline([new_path]) if os.name == 'nt'
                                  else shlex.quote(new_path))
            app['desktop_id'] = ''
            app['custom'] = True
            app['source'] = 'manual-override'
        app['enabled'] = enabled.get_active()
        app['ext'] = fmt.get_active_id() or '.png'
        chosen_behavior = workflow.get_active_id() or MANUAL_SAVE
        if chosen_behavior == AUTO_RENDER and app.get('special') not in ('art', 'rawtherapee', 'darktable'):
            show_message(Gtk.MessageType.WARNING, 'Automatic rendering not supported',
                         'Only ART, RawTherapee and Darktable currently have supported '
                         'sidecar-renderer backends. Select a save-based workflow for this application.', editor)
            continue
        app['behavior'] = chosen_behavior
        accepted = True
        break
    editor.destroy()
    return accepted


def settings_dialog(settings, parent=None):
    """All installed/disabled applications and preferences live in Settings.

    A cancel/close discards the entire draft, including discovery and app edits.
    """
    draft = copy.deepcopy(settings)
    # Recovery workspaces are user data. Do not delete them merely because
    # Settings was opened or a configurable number of days has elapsed.
    sync_discovery(draft)
    app_infos = _shellout_appinfo_map()
    dlg = _prepare_dialog(Gtk.Dialog(title='ShellOut — Settings', transient_for=parent,
                                     modal=True, use_header_bar=False))
    area = dlg.get_content_area()
    area.set_border_width(0)
    area.set_spacing(0)

    header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    header.set_margin_start(16)
    header.set_margin_end(16)
    header.set_margin_top(14)
    header.set_margin_bottom(12)
    header.pack_start(_shellout_label('ShellOut settings', 'shellout-heading'), False, False, 0)
    header.pack_start(_shellout_label('Manage installed editors and return behaviour', 'shellout-muted'), False, False, 0)
    area.pack_start(header, False, False, 0)
    area.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_overlay_scrolling(False)
    scroller.set_hexpand(True)
    scroller.set_vexpand(True)
    area.pack_start(scroller, True, True, 0)
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    content.set_margin_start(16)
    content.set_margin_end(22)
    content.set_margin_top(12)
    content.set_margin_bottom(12)
    scroller.add(content)

    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
    top.pack_start(_shellout_label('APPLICATIONS', 'shellout-section'), True, True, 0)
    refresh = Gtk.Button.new_from_icon_name('view-refresh-symbolic', Gtk.IconSize.BUTTON)
    refresh.set_tooltip_text('Re-scan installed programs from the operating system application registry.')
    add = Gtk.Button(label='Add from Graphics Menu…')
    add.set_tooltip_text('Pick a registered graphics application by its system name and icon; no path required. You can also view all installed applications.')
    for button in (refresh, add):
        top.pack_end(button, False, False, 0)
    content.pack_start(top, False, False, 0)
    note = _shellout_label('Disabled editors stay here and are hidden from the main launcher. '
                           'Use the settings button beside an editor to activate or configure it.', 'shellout-muted')
    note.set_line_wrap(True)
    content.pack_start(note, False, False, 0)

    frame = Gtk.Frame()
    _shellout_style(frame, 'shellout-panel')
    frame.set_shadow_type(Gtk.ShadowType.NONE)
    listing = Gtk.ListBox()
    listing.set_selection_mode(Gtk.SelectionMode.NONE)
    frame.add(listing)
    content.pack_start(frame, False, False, 0)

    def detail_clicked(app):
        if _editor_preferences_dialog(dlg, app, app_infos):
            populate()

    def populate():
        for child in listing.get_children():
            listing.remove(child)
        apps = sorted(draft.get('apps', {}).values(),
                      key=lambda a: (a.get('name') or '').casefold())
        for index, app in enumerate(apps):
            listing.add(_shellout_list_row(app, index, app_infos,
                                           settings_mode=True, on_details=detail_clicked))
        if not apps:
            placeholder = Gtk.ListBoxRow()
            placeholder.set_selectable(False)
            label = _shellout_label('No applications found. Add one from your system menu.', 'shellout-muted')
            label.set_margin_top(16)
            label.set_margin_bottom(16)
            placeholder.add(label)
            listing.add(placeholder)
        listing.show_all()

    populate()

    def refresh_clicked(_button):
        sync_discovery(draft)
        app_infos.clear()
        app_infos.update(_shellout_appinfo_map())
        populate()

    def add_clicked(_button):
        if _add_from_system_menu(dlg, draft):
            app_infos.clear()
            app_infos.update(_shellout_appinfo_map())
            populate()

    refresh.connect('clicked', refresh_clicked)
    add.connect('clicked', add_clicked)
    content.pack_start(_shellout_label('RETURN BEHAVIOUR', 'shellout-section'), False, False, 0)
    mode_frame = Gtk.Frame()
    mode_frame.set_shadow_type(Gtk.ShadowType.NONE)
    _shellout_style(mode_frame, 'shellout-panel')
    modes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    mode_frame.add(modes)
    auto = Gtk.RadioButton.new_from_widget(None)
    manual = Gtk.RadioButton.new_from_widget(auto)
    for radio, heading, description in (
        (auto, 'Auto-detect saved result', 'Watch for saved image files and processing sidecars'),
        (manual, 'Manual return', 'Ask before returning a saved result to GIMP'),
    ):
        item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        item.set_margin_start(10)
        item.set_margin_end(10)
        item.set_margin_top(8)
        item.set_margin_bottom(8)
        radio.set_tooltip_text(description)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.pack_start(_shellout_label(heading), False, False, 0)
        text_box.pack_start(_shellout_label(description, 'shellout-muted'), False, False, 0)
        radio.add(text_box)
        item.pack_start(radio, True, True, 0)
        modes.pack_start(item, False, False, 0)
        if radio is auto:
            modes.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
    (manual if draft.get('return_mode') == 'manual' else auto).set_active(True)
    content.pack_start(mode_frame, False, False, 0)

    content.pack_start(_shellout_label('DEFAULT EXPORT FORMAT', 'shellout-section'), False, False, 0)
    fmt = Gtk.ComboBoxText()
    formats = ('.png', '.tif', '.exr', '.jpg', '.webp')
    for ext in formats:
        fmt.append(ext, ext.upper().lstrip('.'))
    fmt.set_active_id(draft.get('default_format', '.png'))
    fmt.set_tooltip_text('Default interchange format for newly added editors; each editor can override it in Settings.')
    content.pack_start(fmt, False, False, 0)

    advanced = Gtk.Expander(label='Advanced')
    advanced.set_tooltip_text('Show save polling, workspace recovery, and activity-log preferences.')
    grid = Gtk.Grid(row_spacing=10, column_spacing=14)
    grid.set_border_width(10)
    poll = Gtk.SpinButton.new_with_range(200, 5000, 100)
    poll.set_value(draft.get('poll_interval_ms', 350))
    poll.set_tooltip_text('Milliseconds between checks for files saved by an external editor.')
    recovery_note = _shellout_label('Recovery workspaces are preserved until manually removed.', 'shellout-muted')
    recovery_note.set_tooltip_text('ShellOut never automatically deletes failed or unfinished edits. Successful disposable workspaces are removed after import.')
    show_log = Gtk.CheckButton(label='Expand session activity log')
    show_log.set_active(bool(draft.get('show_activity_log', False)))
    show_log.set_tooltip_text('Initially expand the session log while waiting for the editor.')
    grid.attach(_shellout_label('Check interval (ms)'), 0, 0, 1, 1)
    grid.attach(poll, 1, 0, 1, 1)
    grid.attach(recovery_note, 0, 1, 2, 1)
    grid.attach(show_log, 0, 2, 2, 1)
    browse = Gtk.Button(label='Browse standalone executable / AppImage…')
    browse.set_tooltip_text('Advanced fallback for an unregistered native executable or AppImage. Normally choose an application by name from the Graphics menu.')
    grid.attach(browse, 0, 3, 2, 1)
    browse.connect('clicked', lambda _button: (populate() if _add_manual_program(dlg, draft) else None))
    if sys.platform == 'darwin':
        bundle_button = Gtk.Button(label='Choose macOS .app bundle…')
        bundle_button.set_tooltip_text('Select an application bundle that does not appear in the installed-applications picker. Launch Services will open it.')
        grid.attach(bundle_button, 0, 4, 2, 1)
        bundle_button.connect('clicked', lambda _button: (populate() if _add_macos_bundle(dlg, draft) else None))
    advanced.add(grid)
    content.pack_start(advanced, False, False, 0)

    area.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
    footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    _shellout_style(footer, 'shellout-footer')
    footer.set_margin_start(12)
    footer.set_margin_end(12)
    footer.set_margin_top(10)
    footer.set_margin_bottom(10)
    area.pack_start(footer, False, False, 0)
    reset = Gtk.Button(label='Reset')
    reset.set_tooltip_text('Restore return/export defaults and recommended editor activation; retain user-added editors.')
    cancel = Gtk.Button(label='Cancel')
    cancel.set_tooltip_text('Discard Settings changes and return to the launcher.')
    apply = Gtk.Button(label='Apply')
    _shellout_style(apply, 'shellout-primary')
    apply.set_tooltip_text('Save application activation and ShellOut preferences.')
    footer.pack_start(reset, False, False, 0)
    footer.pack_start(Gtk.Label(label=''), True, True, 0)
    footer.pack_start(cancel, False, False, 0)
    footer.pack_start(apply, False, False, 0)

    def reset_clicked(_button):
        draft['return_mode'] = 'auto'
        draft['default_format'] = '.png'
        draft['poll_interval_ms'] = 350
        draft['show_activity_log'] = False
        for app in draft.get('apps', {}).values():
            if not app.get('custom'):
                profile = _profile_for(app.get('name', ''), app.get('executable', ''), app.get('desktop_id', ''))
                app['enabled'] = bool(profile and not app.get('likely_viewer'))
                if profile:
                    app['ext'] = profile['ext']
                    app['behavior'] = profile['behavior']
        auto.set_active(True)
        fmt.set_active_id('.png')
        poll.set_value(350)
        show_log.set_active(False)
        populate()

    reset.connect('clicked', reset_clicked)
    cancel.connect('clicked', lambda _button: dlg.response(Gtk.ResponseType.CANCEL))
    apply.connect('clicked', lambda _button: dlg.response(Gtk.ResponseType.OK))
    _screen_limited(dlg, width=790, desired_height=340 + min(8, len(draft.get('apps', {}))) * 53)
    dlg.show_all()
    result = dlg.run()
    if result == Gtk.ResponseType.OK:
        draft['return_mode'] = 'manual' if manual.get_active() else 'auto'
        draft['default_format'] = fmt.get_active_id() or '.png'
        draft['poll_interval_ms'] = poll.get_value_as_int()
        draft['show_activity_log'] = show_log.get_active()
        try:
            save_settings(draft)
        except OSError as exc:
            show_message(Gtk.MessageType.ERROR, 'Could not save ShellOut Settings', str(exc), dlg)
            result = Gtk.ResponseType.CANCEL
    dlg.destroy()
    return draft if result == Gtk.ResponseType.OK else None


def launcher_dialog(settings):
    """One Tools > ShellOut popup; only enabled editors, one Open with action."""
    settings = sync_discovery(copy.deepcopy(settings))
    app_infos = _shellout_appinfo_map()
    dlg = _prepare_dialog(Gtk.Dialog(title='ShellOut', modal=True, use_header_bar=False))
    area = dlg.get_content_area()
    area.set_border_width(0)
    area.set_spacing(0)

    header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    header.set_margin_start(16)
    header.set_margin_end(16)
    header.set_margin_top(14)
    header.set_margin_bottom(12)
    header.pack_start(_shellout_label('ShellOut', 'shellout-heading'), False, False, 0)
    header.pack_start(_shellout_label('Choose an external editor for the selected layer', 'shellout-muted'), False, False, 0)
    area.pack_start(header, False, False, 0)
    area.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_overlay_scrolling(False)
    scroller.set_hexpand(True)
    scroller.set_vexpand(True)
    area.pack_start(scroller, True, True, 0)
    listing = Gtk.ListBox()
    listing.set_selection_mode(Gtk.SelectionMode.SINGLE)
    frame = Gtk.Frame()
    frame.set_shadow_type(Gtk.ShadowType.NONE)
    _shellout_style(frame, 'shellout-panel')
    frame.add(listing)
    contents = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
    contents.set_margin_start(14)
    contents.set_margin_end(20)
    contents.set_margin_top(12)
    contents.set_margin_bottom(12)
    contents.pack_start(_shellout_label('AVAILABLE EDITORS', 'shellout-section'), False, False, 0)
    contents.pack_start(frame, False, False, 0)
    scroller.add(contents)

    footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    _shellout_style(footer, 'shellout-footer')
    footer.set_margin_start(12)
    footer.set_margin_end(12)
    footer.set_margin_top(10)
    footer.set_margin_bottom(10)
    area.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
    area.pack_start(footer, False, False, 0)
    settings_button = Gtk.Button(label='Settings…')
    settings_button.set_tooltip_text('Manage installed applications, including disabled editors, and edit global preferences.')
    cancel = Gtk.Button(label='Cancel')
    cancel.set_tooltip_text('Close ShellOut without launching an editor.')
    launch = Gtk.Button(label='Open with')
    launch.set_tooltip_text('Send the selected GIMP layer to the highlighted external editor.')
    _shellout_style(launch, 'shellout-primary')
    launch.set_sensitive(False)
    footer.pack_start(Gtk.Label(label=''), True, True, 0)
    footer.pack_start(settings_button, False, False, 0)
    footer.pack_start(cancel, False, False, 0)
    footer.pack_start(launch, False, False, 0)

    by_row = {}
    chosen = {'id': None}

    def selected_changed(_widget, row):
        app = by_row.get(row)
        launch.set_sensitive(bool(app and _shellout_available(app, app_infos)))

    listing.connect('row-selected', selected_changed)

    def populate(preselect=None):
        by_row.clear()
        for child in listing.get_children():
            listing.remove(child)
        apps = enabled_apps(settings)
        for index, app in enumerate(apps):
            row = _shellout_list_row(app, index, app_infos)
            listing.add(row)
            by_row[row] = app
            if preselect and app.get('id') == preselect and _shellout_available(app, app_infos):
                listing.select_row(row)
        if not apps:
            placeholder = Gtk.ListBoxRow()
            placeholder.set_selectable(False)
            message = _shellout_label('No editors enabled. Open Settings to add one from the system menu.',
                                      'shellout-muted')
            message.set_margin_top(18)
            message.set_margin_bottom(18)
            message.set_margin_start(10)
            message.set_margin_end(10)
            message.set_line_wrap(True)
            placeholder.add(message)
            listing.add(placeholder)
        listing.show_all()
        if not listing.get_selected_row():
            launch.set_sensitive(False)

    populate()

    def edit_settings(_button):
        nonlocal settings
        current = by_row.get(listing.get_selected_row())
        selected_id = current.get('id') if current else None
        updated = settings_dialog(settings, parent=dlg)
        if updated is not None:
            settings = updated
            app_infos.clear()
            app_infos.update(_shellout_appinfo_map())
            populate(selected_id)

    def launch_selected(_button):
        selected = by_row.get(listing.get_selected_row())
        if not selected or not _shellout_available(selected, app_infos):
            return
        try:
            # Record fresh discovery before closing, so run() can load this
            # selection even when it was installed after the previous session.
            save_settings(settings)
        except OSError as exc:
            show_message(Gtk.MessageType.ERROR, 'Could not save ShellOut application', str(exc), dlg)
            return
        chosen['id'] = selected.get('id')
        dlg.response(Gtk.ResponseType.OK)

    settings_button.connect('clicked', edit_settings)
    cancel.connect('clicked', lambda _button: dlg.response(Gtk.ResponseType.CANCEL))
    launch.connect('clicked', launch_selected)
    _screen_limited(dlg, width=570, desired_height=200 + min(9, len(enabled_apps(settings))) * 59)
    dlg.show_all()
    result = dlg.run()
    dlg.destroy()
    return chosen['id'] if result == Gtk.ResponseType.OK else None


class ShellOutPlugin(Gimp.PlugIn):
    def __init__(self):
        super().__init__()
        # Startup registration never scans apps, PATH, or launches processes.
        # The desktop registry is scanned only when ShellOut's UI opens.
        self.settings = load_settings()


    def do_set_i18n(self, proc_name):
        return False

    def do_query_procedures(self):
        return ["python-fu-shellout"]

    def do_init_procedures(self):
        return ["python-fu-shellout"]

    def do_create_procedure(self, name):
        if name != "python-fu-shellout":
            return None
        procedure = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, self.run, None)
        procedure.set_menu_label("ShellOut…")
        procedure.set_documentation(
            "Launch an external graphics editor",
            "Choose an enabled graphics editor and return saved output as a new GIMP layer.",
            name,
        )
        procedure.set_attribution("Zarir Madon / OpenAI", "GPL-3.0", "2026")
        procedure.set_image_types("*")
        procedure.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)
        procedure.add_menu_path(MENU_PATH)
        return procedure

    def run(self, procedure, run_mode, image, drawables, config, run_data):
        if run_mode != Gimp.RunMode.INTERACTIVE:
            return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR, None)
        # Official libgimpui guidance: initialize UI in the run process before
        # using GTK/libgimpui. Query/init registration remains UI-free and cheap.
        GimpUi.init(PLUGIN_ID)
        if image is None or not drawables or len(drawables) != 1:
            show_message(Gtk.MessageType.WARNING, "ShellOut", "Select exactly one image layer first.")
            return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, None)
        app_id = launcher_dialog(load_settings())
        if not app_id:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, None)
        settings = load_settings()
        app = settings.get('apps', {}).get(app_id)
        if not app or not app.get('enabled'):
            show_message(Gtk.MessageType.ERROR, "ShellOut", "This application is not enabled or no longer available.")
            return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, None)
        ok = shellout(image, drawables, app)
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS if ok else Gimp.PDBStatusType.CANCEL, None)


if __name__ == "__main__":
    Gimp.main(ShellOutPlugin.__gtype__, sys.argv)
