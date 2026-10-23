#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Zarir Madon (https://www.zarirmadon.com)
"""
rembg-gimp.py — Remove Background for GIMP 3.2
================================================

DESIGN, CREATION AND DEVELOPMENT
    Designed and created by: Zarir Madon
    Website:                 https://www.zarirmadon.com
    AI-assisted coding:      OpenAI GPT-6 (ChatGPT)
    Earlier coding support:  Anthropic Claude Sonnet 5
    Copyright (C) 2026 Zarir Madon.

PLUGIN LICENCE
    Released under the GNU General Public License, version 3.0 only
    (GPL-3.0-only; SPDX: GPL-3.0-only).
    You may use, study, modify and redistribute this plug-in under GPL-3.0.
    This software is provided WITHOUT ANY WARRANTY, including the implied
    warranties of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
    See the accompanying LICENSE file for the full licence terms.

THIRD-PARTY ATTRIBUTIONS AND LICENCES
    rembg             — Daniel Gatis and contributors; MIT License.
                        https://github.com/danielgatis/rembg
    ONNX Runtime      — Microsoft and contributors; MIT License.
                        https://github.com/microsoft/onnxruntime
    Pillow            — Pillow contributors; MIT-CMU License.
                        https://github.com/python-pillow/Pillow
    NumPy             — NumPy contributors; BSD 3-Clause (core project).
                        https://github.com/numpy/numpy
    SciPy             — SciPy contributors; BSD 3-Clause (core project).
                        https://github.com/scipy/scipy
    scikit-image      — scikit-image contributors; predominantly BSD-3-Clause,
                        with additional notices for particular source files.
                        https://github.com/scikit-image/scikit-image
    PyMatting         — PyMatting contributors; MIT License (used in some
                        rembg installations; not called directly here).
                        https://github.com/pymatting/pymatting
    GIMP / libgimp    — GIMP contributors; GPL-3.0-or-later.
                        https://www.gimp.org/

    These are independent upstream projects, not relicensed by this plugin.
    Runtime libraries are imported from the user's installation; they are
    not embedded or bundled in this source file. If distributing dependency
    binaries, retain their corresponding copyright and licence notices.

    Background-removal MODEL WEIGHTS are separate from the rembg library.
    Each model may have its own copyright, usage restrictions and licence;
    rembg's MIT licence does NOT grant rights to every model checkpoint.
    Verify a selected model's own terms before redistributing its weights or
    using it commercially.

Install location:
    ~/.config/GIMP/3.2/plug-ins/rembg-gimp/rembg-gimp.py

The containing folder name MUST match the script name (minus .py) —
GIMP 3.x plugin discovery requires this.

After copying, make it executable:
    chmod +x ~/.config/GIMP/3.2/plug-ins/rembg-gimp/rembg-gimp.py

Then restart GIMP. Find it under: Filters > Distorts > Remove Background...

Dependencies (must be importable by the SAME Python interpreter GIMP uses —
see README.md for how to confirm this and for full CachyOS/Arch-specific
install commands, since a plain `pip install rembg` often pulls in far more
than you need):
    sudo pacman -S --needed python-numpy python-pillow python-pip
    sudo pacman -S --needed python-onnxruntime-cuda   # or -rocm for AMD, or -cpu for no GPU
    python3 -m pip install --break-system-packages --no-deps rembg
    # Optional dependencies depend on the installed rembg version; inspect its metadata.

Models are downloaded only on explicit user request via model management.
They are cached by rembg; checkpoint locations depend on the installed rembg
version and environment. (Older rembg versions used ~/.u2net/ — that
directory is still read for models downloaded previously, so upgrading
never re-downloads what you already have.)

Settings (model choice, edge mode, and all parameters) persist across GIMP
sessions in ~/.config/GIMP/3.2/rembg-gimp-settings.json.
"""

import gi
gi.require_version('Gimp', '3.0')
gi.require_version('GimpUi', '3.0')
gi.require_version('Gegl', '0.4')
from gi.repository import Gimp, GimpUi, GObject, GLib, Gegl, Gio, Gtk, GdkPixbuf, Gdk

import sys
import os
import io
import json
import threading
import tempfile
import time
import subprocess
import signal
import urllib.request
import urllib.error


# ---------------------------------------------------------------------------
# Model catalogue — name, on-disk/download size, and a plain-language guide
# for the selector help popup. Sizes are approximate.
# ---------------------------------------------------------------------------
MODELS = [
    {
        "id": "u2net",
        "label": "u2net (general purpose, MIT-licensed)",
        "size": "~176 MB",
        "help": (
            "The original general-purpose model. Good all-round choice for "
            "people, animals, and everyday objects on varied backgrounds. "
            "Slightly softer/blockier edges than isnet-general-use or "
            "bria-rmbg on fine detail (hair, fur), but no commercial "
            "licensing caveat — a safe default for commercial work."
        ),
    },
    {
        "id": "u2netp",
        "label": "u2netp (lightweight/fast)",
        "size": "~4.7 MB",
        "help": (
            "A pruned, much smaller/faster version of u2net. Noticeably "
            "lower quality edges — use this only for quick previews, batch "
            "throughput on weak hardware, or drafts, not final output."
        ),
    },
    {
        "id": "u2net_human_seg",
        "label": "u2net_human_seg (people, full body)",
        "size": "~176 MB",
        "help": (
            "Trained specifically on human subjects. Better than generic "
            "u2net for portraits and full-body shots — cleaner edges around "
            "skin, clothing folds. Not suitable for non-human subjects."
        ),
    },
    {
        "id": "u2net_cloth_seg",
        "label": "u2net_cloth_seg (clothing segmentation)",
        "size": "~176 MB",
        "help": (
            "Segments clothing items (upper body, lower body, full body "
            "garments) rather than doing simple foreground/background "
            "removal. Niche — use only if you specifically need garment "
            "masks, e.g. for e-commerce product shots."
        ),
    },
    {
        "id": "silueta",
        "label": "silueta (compact general purpose)",
        "size": "~43 MB",
        "help": (
            "Similar use case to u2net but a much smaller model file with "
            "a modest quality trade-off. Reasonable middle ground between "
            "u2netp's speed and u2net's quality."
        ),
    },
    {
        "id": "isnet-general-use",
        "label": "isnet-general-use (higher quality general)",
        "size": "~176 MB",
        "help": (
            "Newer architecture than u2net; sharp, clean edges on general "
            "subjects — product photography, objects, animals. A good "
            "middle ground between u2net's speed and bria-rmbg's size, "
            "with no commercial licensing caveat."
        ),
    },
    {
        "id": "isnet-anime",
        "label": "isnet-anime (anime/illustration)",
        "size": "~176 MB",
        "help": (
            "Specialised for anime-style flat-shaded illustrations and "
            "character art. Handles hard cel-shaded edges and hair much "
            "better than the general models on this kind of source. Poor "
            "choice for photographic content. If this still leaves gaps in "
            "hair spikes or thin outline strokes, try the ViTMatte edge "
            "mode below rather than switching models."
        ),
    },
    {
        "id": "birefnet-general",
        "label": "birefnet-general (high-precision general)",
        "size": "~880 MB",
        "help": (
            "State-of-the-art edge precision for general subjects — best "
            "choice for demanding work (fine hair strands, semi-transparent "
            "or fuzzy edges) if you don't mind a large download and slower "
            "inference. MIT-licensed, unlike bria-rmbg. Overkill for quick/"
            "batch work."
        ),
    },
    {
        "id": "birefnet-general-lite",
        "label": "birefnet-general-lite (BiRefNet, smaller/faster)",
        "size": "~170 MB (approx.)",
        "help": (
            "A lighter BiRefNet variant for general subjects — most of "
            "birefnet-general's edge-quality improvement over u2net at a "
            "fraction of the download/runtime cost. Good choice if "
            "birefnet-general felt too slow but isnet-general-use wasn't "
            "quite good enough on tricky edges."
        ),
    },
    {
        "id": "birefnet-portrait",
        "label": "birefnet-portrait (high-precision portraits)",
        "size": "~880 MB",
        "help": (
            "BiRefNet variant tuned specifically for portrait/headshot "
            "subjects. The best quality option for hair-strand-level "
            "portrait cutouts, at the cost of a large download and slower "
            "processing. Pair with the ViTMatte edge mode below for the "
            "most detail on wispy hair."
        ),
    },
    {
        "id": "birefnet-dis",
        "label": "birefnet-dis (dichotomous segmentation)",
        "size": "~880 MB (approx.)",
        "help": (
            "BiRefNet trained for dichotomous image segmentation — very "
            "precise foreground/background separation on complex, "
            "high-detail subjects. Niche/specialist choice; try "
            "birefnet-general first unless you have a specific reason to "
            "reach for this one."
        ),
    },
    {
        "id": "birefnet-hrsod",
        "label": "birefnet-hrsod (high-resolution salient objects)",
        "size": "~880 MB (approx.)",
        "help": (
            "BiRefNet trained for high-resolution salient object "
            "detection — tuned for large, detailed source images where "
            "the main subject should be identified precisely at full "
            "resolution. Specialist choice for very large source photos."
        ),
    },
    {
        "id": "birefnet-cod",
        "label": "birefnet-cod (concealed/camouflaged objects)",
        "size": "~880 MB (approx.)",
        "help": (
            "BiRefNet trained for concealed object detection (COD) — "
            "designed to find subjects that visually blend into their "
            "background (camouflage-style low contrast). Unlikely to be "
            "useful for typical product/portrait/art cutouts; a research/"
            "niche model included for completeness."
        ),
    },
    {
        "id": "birefnet-massive",
        "label": "birefnet-massive (large training set)",
        "size": "~880 MB (approx.)",
        "help": (
            "BiRefNet trained on a larger, more varied dataset than "
            "birefnet-general. May generalise slightly better to unusual "
            "subjects at the same size/speed cost. Worth trying if "
            "birefnet-general's result is close but not quite right."
        ),
    },
]

MODELS.sort(key=lambda item: item["label"].casefold())

MODEL_IDS = [m["id"] for m in MODELS]
MODEL_LABELS = [m["label"] for m in MODELS]


# ---------------------------------------------------------------------------
# Edge modes — rembg's four ways to turn a mask into a cutout. These are
# mutually exclusive (rembg applies at most one), not stackable.
# ---------------------------------------------------------------------------
EDGE_MODES = [
    {
        "id": "naive",
        "label": "Naive (default, fastest)",
        "help": (
            "No edge refinement. Best for hard-edged subjects (products, "
            "logos, screenshots, flat illustration) or when the background "
            "was already close in color to the subject — there's nothing "
            "to correct, so refinement buys nothing. Free."
        ),
    },
    {
        "id": "decontaminate",
        "label": "Decontaminate (fix color fringing, cheap)",
        "help": (
            "Fixes a colored halo left around hair/fur/soft edges when the "
            "subject was shot against a strongly colored background (green "
            "grass, blue sky) — that color bleeds into semi-transparent "
            "edge pixels and stays visible as a rim after cutout. This "
            "only corrects color, never the mask shape/coverage. Cheap "
            "enough to leave on for a whole batch. Does NOT fix holes or "
            "gaps in the mask — for that, use ViTMatte."
        ),
    },
    {
        "id": "vitmatte",
        "label": "ViTMatte (best detail recovery, slow + extra download)",
        "help": (
            "Solves the same problem as ViTMatte — wrong mask shape, "
            "holes, clipped hair strands — but predicts the alpha with a "
            "neural network instead of a solver, so it recovers more wispy "
            "detail and cannot fail to converge. Costs an extra ~110-380MB "
            "download on first use (checkpoint-dependent) and runs slower "
            "than ViTMatte. If you're seeing gaps punched through "
            "hair spikes or thin outline strokes on anime/illustration "
            "art, THIS is usually the most reliable fix — try it before "
            "switching models."
        ),
    },
]
EDGE_MODE_IDS = [m["id"] for m in EDGE_MODES]

VITMATTE_CHECKPOINTS = [
    {
        "id": "small-distinctions-646",
        "label": "small-distinctions-646 (default, best quality/byte)",
        "size": "~110 MB",
    },
    {
        "id": "small-composition-1k",
        "label": "small-composition-1k (synthetic composites)",
        "size": "~110 MB",
    },
    {
        "id": "base-distinctions-646",
        "label": "base-distinctions-646 (more detail, ~2.5x runtime)",
        "size": "~380 MB",
    },
    {
        "id": "base-composition-1k",
        "label": "base-composition-1k (larger, synthetic training set)",
        "size": "~380 MB",
    },
]
VITMATTE_CHECKPOINT_IDS = [c["id"] for c in VITMATTE_CHECKPOINTS]


# ---------------------------------------------------------------------------
# Models folder location — mirrors rembg's OWN path resolution logic
# exactly (see rembg/sessions/base.py: legacy_home / rembg_home), rather
# than hardcoding ~/.rembg. rembg checks, in order: U2NET_HOME env var
# (legacy override, still honoured), then REMBG_HOME env var, then
# XDG_DATA_HOME/rembg, then falls back to ~/.rembg. Individual model
# files live under <home>/models/<model-name>/, not directly in <home>.
# Getting this wrong would send the "Open models folder" button to an
# empty or nonexistent directory for anyone who has these env vars set.
# ---------------------------------------------------------------------------

def get_rembg_models_root():
    if os.getenv("U2NET_HOME"):
        return os.path.expanduser(os.getenv("U2NET_HOME"))
    xdg = os.getenv("XDG_DATA_HOME")
    default = os.path.join(xdg, "rembg") if xdg else os.path.join("~", ".rembg")
    home = os.path.expanduser(os.getenv("REMBG_HOME", default))
    models_dir = os.path.join(home, "models")
    return models_dir


def get_rembg_legacy_models_root():
    """The pre-move flat directory (~/.u2net by default) — still read by
    rembg for models downloaded before the ~/.rembg/models/ layout, so
    worth offering as a fallback if the primary folder doesn't exist yet
    (e.g. no model has been downloaded through this plugin session)."""
    return os.path.expanduser(
        os.getenv("U2NET_HOME",
                  os.path.join(os.getenv("XDG_DATA_HOME", "~"), ".u2net")))


def model_cache_candidates(model_id):
    """Locate existing ONNX checkpoints without triggering rembg downloads."""
    roots = [get_rembg_models_root(), get_rembg_legacy_models_root(),
             os.path.expanduser('~/.rembg/models'), os.path.expanduser('~/.u2net')]
    seen = set()
    for root in roots:
        root = os.path.realpath(os.path.expanduser(root))
        if root in seen or not os.path.isdir(root):
            continue
        seen.add(root)
        # Current rembg stores models in per-model folders; older versions
        # stored checkpoints directly under ~/.u2net.
        folders = [os.path.join(root, model_id), root]
        for folder in folders:
            if not os.path.isdir(folder):
                continue
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            for name in names:
                low = name.lower()
                if not low.endswith('.onnx'):
                    continue
                if folder == root and not (low.startswith(model_id.lower()) or
                                           (model_id == 'u2net' and low == 'u2net.onnx')):
                    continue
                candidate = os.path.join(folder, name)
                if os.path.isfile(candidate) and os.path.getsize(candidate) > 1024 * 1024:
                    yield candidate


def find_cached_model(model_id):
    return next(model_cache_candidates(model_id), None)


def resolve_raster_layer(image, drawables):
    """Accept the selected layer or its mask, preserving the owning layer."""
    if not drawables or len(drawables) != 1:
        return None
    selected = drawables[0]
    if isinstance(selected, Gimp.Layer):
        return None if selected.is_group() else selected
    def walk(layers):
        for item in layers:
            if isinstance(item, Gimp.Layer):
                mask = item.get_mask()
                if mask is not None and (mask == selected or
                        mask.get_id() == selected.get_id()):
                    return item
                if item.is_group():
                    found = walk(item.get_children())
                    if found is not None:
                        return found
        return None
    return walk(image.get_layers())


def open_folder_in_file_manager(path):
    """
    Opens `path` in the system's default file manager. Creates the
    directory first if it doesn't exist yet (e.g. no model downloaded
    through this plugin session), so the user gets a real, browsable
    empty folder rather than an error. Uses GIMP/GLib's own
    Gio.AppInfo.launch_default_for_uri, which is already cross-platform
    (Linux/macOS/Windows) rather than hand-rolling per-OS subprocess
    calls (xdg-open / open / explorer).
    """
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        return False, f"Could not create/access folder: {e}"
    try:
        uri = Gio.File.new_for_path(path).get_uri()
        Gio.AppInfo.launch_default_for_uri(uri, None)
        return True, None
    except GLib.Error as e:
        return False, f"Could not open file manager: {e}"


# ---------------------------------------------------------------------------
# Settings persistence — remembers the last-used model and all parameters
# across GIMP sessions. Stored as plain JSON next to GIMP's own config, not
# inside the plugin folder (which some package managers may treat as
# read-only or wipe on plugin update).
# ---------------------------------------------------------------------------

SETTINGS_PATH = os.path.join(
    GLib.get_user_config_dir(), "GIMP", "3.2", "rembg-gimp-settings.json")

DEFAULT_SETTINGS = {
    "model_id": "birefnet-general",
    "edge_mode": "naive",
    "alpha_matting_foreground_threshold": 240,
    "alpha_matting_background_threshold": 10,
    "alpha_matting_erode_size": 10,
    "vitmatte_model": "small-distinctions-646",
    "post_process_mask": False,
    "only_mask": False,
    "shadow_preservation": "Off",
    "quality": "Fine",
    "edge_style": "Fine",
    "output_mode": "New layer",
    "advanced_expanded": True,
}


def load_settings():
    try:
        with open(SETTINGS_PATH, "r") as f:
            saved = json.load(f)
        settings = dict(DEFAULT_SETTINGS)
        settings.update({k: v for k, v in saved.items()
                          if k in DEFAULT_SETTINGS})
        # Guard against a stale saved model_id/edge_mode/vitmatte_model that
        # no longer exists (e.g. after this plugin is updated and a model
        # is renamed or removed) — fall back to the default rather than
        # crashing the dialog on an invalid combo index.
        if settings["model_id"] not in MODEL_IDS:
            settings["model_id"] = DEFAULT_SETTINGS["model_id"]
        if settings["edge_mode"] not in EDGE_MODE_IDS:
            settings["edge_mode"] = DEFAULT_SETTINGS["edge_mode"]
        if settings["vitmatte_model"] not in VITMATTE_CHECKPOINT_IDS:
            settings["vitmatte_model"] = DEFAULT_SETTINGS["vitmatte_model"]
        if settings.get("quality") not in ("Fast", "Std", "Fine"):
            settings["quality"] = DEFAULT_SETTINGS["quality"]
        if settings.get("edge_style") not in ("Fine", "Smooth", "Crisp", "Feathered"):
            settings["edge_style"] = DEFAULT_SETTINGS["edge_style"]
        if settings.get("output_mode") not in ("New layer", "New image"):
            settings["output_mode"] = DEFAULT_SETTINGS["output_mode"]
        settings["advanced_expanded"] = bool(settings.get("advanced_expanded", DEFAULT_SETTINGS["advanced_expanded"]))
        return settings
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(DEFAULT_SETTINGS)


def save_settings(settings):
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        # Non-fatal: settings just won't persist this time. Don't interrupt
        # the user's actual background-removal workflow over this.
        pass


# ---------------------------------------------------------------------------
# Parameter definitions with hover help text (rembg's `remove()` kwargs)
# ---------------------------------------------------------------------------
PARAM_HELP = {
    "alpha_matting_foreground_threshold": (
        "0-255. Pixels with an initial mask confidence above this value "
        "are treated as 'definitely foreground' seeds for the matting "
        "algorithm. Higher = more conservative (only very confident "
        "pixels seed the foreground), which can shrink thin details. "
        "Default 240."
    ),
    "alpha_matting_background_threshold": (
        "0-255. Pixels with mask confidence below this value are treated "
        "as 'definitely background' seeds. Lower = more conservative "
        "about calling something background. Default 10."
    ),
    "alpha_matting_erode_size": (
        "Pixel radius used to erode the foreground/background seed masks "
        "before matting, creating an 'unknown' region for the algorithm to "
        "resolve. Larger values give the matting algorithm more room to "
        "work on soft edges (e.g. hair) but cost more time. Default 10."
    ),
    "only_mask": (
        "Output the alpha mask itself (as a greyscale image) instead of "
        "the cutout RGBA image. Useful if you want to hand-edit the mask "
        "in GIMP before applying it, rather than trusting the automatic "
        "cutout directly."
    ),
    "post_process_mask": (
        "Applies a morphological clean-up pass to the mask (removes small "
        "holes/islands, smooths the boundary slightly). Usually a small "
        "quality improvement with negligible extra cost; occasionally "
        "over-smooths fine detail like individual hair strands."
    ),
    "background_color": (
        "Instead of transparency, composite the cutout over a solid RGBA "
        "colour. Leave disabled to keep a transparent background (normal "
        "use case for compositing in GIMP)."
    ),
}



# ---------------------------------------------------------------------------
# rembg version check — queries PyPI for the latest published version and
# compares against what's installed. Does NOT run pip itself; only tells
# the user what command to run and copies/shows it, since silently
# upgrading a package GIMP has already imported this session is unsafe
# (Python doesn't reliably re-import an upgraded package without a
# restart) and an unattended upgrade could change rembg's own behaviour
# with no one there to notice if something breaks.
# ---------------------------------------------------------------------------

def get_installed_rembg_version():
    try:
        import rembg as _rembg_mod
        return getattr(_rembg_mod, "__version__", "unknown")
    except ImportError:
        return None


def get_latest_pypi_version(package_name, timeout_seconds=6):
    """
    Queries PyPI's JSON API directly rather than shelling out to
    `pip index versions` (which pip itself documents as an experimental
    command that may change/be removed without notice). Returns the
    latest version string, or None if the check fails for any reason
    (no network, PyPI unreachable, unexpected response shape) — a failed
    check should never block or alarm the user, it just means "unknown".
    """
    url = f"https://pypi.org/pypi/{package_name}/json"
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("info", {}).get("version")
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, KeyError, TimeoutError, OSError):
        return None


def build_upgrade_command():
    """
    The exact pip command the user should run themselves. Deliberately
    matches the --no-deps install approach used in the README/header, so
    an upgrade doesn't reopen the large unrelated dependency pull that
    --no-deps was specifically chosen to avoid.
    """
    return f"{sys.executable} -m pip install --break-system-packages --upgrade --no-deps rembg"


# ---------------------------------------------------------------------------
# CPU / GPU execution provider detection
# ---------------------------------------------------------------------------

_GPU_PROVIDERS = (
    "CUDAExecutionProvider",
    "ROCMExecutionProvider",
    "TensorrtExecutionProvider",
    "DmlExecutionProvider",       # DirectML (Windows, irrelevant on CachyOS
                                   # but harmless to check)
    "OpenVINOExecutionProvider",
)

_gpu_notice_shown = False  # only nag once per GIMP session


def detect_execution_provider():
    """
    Returns (active_provider_str, is_gpu_bool, advice_str_or_None).
    advice_str is a ready-to-show install hint, only populated when no GPU
    provider is available at all.
    """
    try:
        import onnxruntime as ort
    except ImportError:
        return "unknown (onnxruntime not importable)", False, None

    available = ort.get_available_providers()
    gpu_available = [p for p in available if p in _GPU_PROVIDERS]

    if gpu_available:
        # onnxruntime uses the first provider in its priority list that's
        # available; rembg lets it pick automatically, so report the
        # highest-priority GPU provider found as "active".
        active = gpu_available[0]
        return active, True, None

    advice = (
        "rembg/onnxruntime is running on CPU only — no GPU execution "
        "provider was found. This works fine but is noticeably slower, "
        "especially with the larger models (isnet, sam, birefnet-*).\n\n"
        "To enable GPU acceleration on CachyOS/Arch:\n"
        "  • NVIDIA (CUDA): sudo pacman -S python-onnxruntime-cuda\n"
        "  • AMD (ROCm):    sudo pacman -S python-onnxruntime-rocm\n"
        "    (or python-onnxruntime-opt-rocm, which adds AVX2 CPU "
        "optimizations alongside ROCm)\n\n"
        "Only one onnxruntime variant should be installed at a time — "
        "remove the plain 'python-onnxruntime' (or pip 'onnxruntime') "
        "first if switching, since they conflict."
    )
    return "CPUExecutionProvider", False, advice


# ---------------------------------------------------------------------------
# Core conversion helpers: GIMP layer <-> PIL Image, bit-depth aware
# ---------------------------------------------------------------------------

def layer_to_pil(layer):
    """Export only the selected layer, never the entire visible composition.

    A disposable image containing a copy of the drawable prevents unrelated
    visible layers from contaminating the model input. The original image,
    layer, precision and offsets are never modified.
    """
    image = layer.get_image()
    precision = image.get_precision()
    is_high_bit_depth = precision not in (
        Gimp.Precision.U8_LINEAR, Gimp.Precision.U8_NON_LINEAR,
        Gimp.Precision.U8_PERCEPTUAL)
    width, height = layer.get_width(), layer.get_height()
    if width <= 0 or height <= 0:
        raise ValueError("The selected layer has no pixel area")
    temp_image = None
    export_path = None
    try:
        # Gimp.Image.new / Layer.new_from_drawable are public GIMP 3 APIs.
        temp_image = Gimp.Image.new(width, height, Gimp.ImageBaseType.RGB)
        temp_layer = Gimp.Layer.new_from_drawable(layer, temp_image)
        temp_image.insert_layer(temp_layer, None, 0)
        offsets = temp_layer.get_offsets()
        temp_layer.set_offsets(0, 0)
        temp_layer.set_visible(True)
        if temp_image.get_precision() != Gimp.Precision.U8_NON_LINEAR:
            temp_image.convert_precision(Gimp.Precision.U8_NON_LINEAR)
        fd, export_path = tempfile.mkstemp(prefix="gimp-rembg-src-", suffix=".png")
        os.close(fd)
        result = Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, temp_image,
                                Gio.File.new_for_path(export_path), None)
        if result is False or not os.path.isfile(export_path) or os.path.getsize(export_path) == 0:
            raise RuntimeError("GIMP could not export the selected layer")
        from PIL import Image as PILImage
        with PILImage.open(export_path) as exported:
            return exported.convert("RGBA"), is_high_bit_depth, precision
    finally:
        if temp_image is not None:
            temp_image.delete()
        if export_path is not None:
            try:
                os.unlink(export_path)
            except FileNotFoundError:
                pass


def pil_mask_to_new_layer(image, original_layer, alpha_mask_pil, layer_name):
    """
    Take the alpha channel produced by rembg (as a PIL 'L' mask, same pixel
    dimensions as the source) and apply it as a new RGBA layer built from
    the ORIGINAL layer's full-precision pixel data, so 16/32-bit colour
    data is preserved even though the mask itself was computed at 8-bit.

    The layer mask remains editable and source pixels are retained.
    """
    new_layer = Gimp.Layer.new_from_drawable(original_layer, image)
    new_layer.set_name(layer_name)
    image.insert_layer(new_layer, None, -1)
    if not new_layer.has_alpha():
        new_layer.add_alpha()

    width = new_layer.get_width()
    height = new_layer.get_height()

    fd, mask_tmp_path = tempfile.mkstemp(prefix="gimp-rembg-mask-", suffix=".png")
    os.close(fd)
    mask_load_image = None
    success = False
    try:
        # Nearest-neighbour resizing is intentional: inference output is
        # expected to match source dimensions, not be silently rescaled.
        if alpha_mask_pil.size != (width, height):
            raise ValueError("Inference mask dimensions do not match source layer")
        alpha_mask_pil.convert("L").save(mask_tmp_path)
        layer_mask = new_layer.create_mask(Gimp.AddMaskType.WHITE)
        new_layer.add_mask(layer_mask)
        mask_load_image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,
                                         Gio.File.new_for_path(mask_tmp_path))
        if mask_load_image is None or not mask_load_image.get_layers():
            raise RuntimeError("Unable to load generated mask into GIMP")
        mask_load_layer = mask_load_image.get_layers()[0]
        Gimp.Selection.all(mask_load_image)
        if not Gimp.edit_copy([mask_load_layer]):
            raise RuntimeError("Could not copy the generated mask")
        pasted_layers = Gimp.edit_paste(layer_mask, False)
        if not pasted_layers:
            raise RuntimeError("Could not paste generated alpha into layer mask")
        Gimp.floating_sel_anchor(pasted_layers[0])
        success = True
    finally:
        if not success:
            try:
                image.remove_layer(new_layer)
            except Exception:
                pass
        if mask_load_image is not None:
            mask_load_image.delete()
        try:
            os.unlink(mask_tmp_path)
        except FileNotFoundError:
            pass

    # NOTE: Gimp.Layer has no apply_mask() method — that was a guess that
    # doesn't exist in the 3.x API. The real equivalent is
    # layer.remove_mask(Gimp.MaskApplyMode.APPLY), which bakes the mask
    # into the layer's alpha channel and deletes the mask object.
    # Deliberately NOT doing that here: leaving the mask live and editable
    # is more useful for a background-removal result, since it lets you
    # hand-touch-up the cutout edge non-destructively afterwards (paint on
    # the mask in black/white) rather than having it already flattened.
    # Clear the selection so it doesn't linger visibly on the new layer.
    Gimp.displays_flush()
    return new_layer


# ---------------------------------------------------------------------------
# Background removal worker (runs off the UI thread)
# ---------------------------------------------------------------------------

# Separate inference process: ONNX/Numba never execute in GIMP's UI process.
_INFERENCE_SCRIPT = r'''
import json, sys, traceback, time
from PIL import Image
from rembg import new_session, remove
def phase(message):
    print("PHASE:" + message, flush=True)
src, dst, model, params_file = sys.argv[1:]
try:
    with open(params_file, encoding="utf-8") as f:
        p = json.load(f)
    phase("Loading model into memory")
    session = new_session(model, providers=["CPUExecutionProvider"])
    phase("Running model inference")
    kw = {"session": session,
          "post_process_mask": bool(p.get("post_process_mask", False)),
          "only_mask": bool(p.get("only_mask", False))}
    edge = p.get("edge_mode", "naive")
    if edge == "decontaminate": kw["decontaminate"] = True
    elif edge == "vitmatte":
        kw.update(vitmatte=True, vitmatte_model=p.get("vitmatte_model", "small-distinctions-646"))
    with Image.open(src) as im:
        result = remove(im.convert("RGBA"), **kw)
    if isinstance(result, list):
        # Clothing segmentation returns multiple category masks. Combine
        # their foreground coverage for background removal.
        from PIL import ImageChops
        masks = [item.convert("L") for item in result]
        if not masks:
            raise ValueError("The segmentation model returned no masks")
        merged = masks[0]
        for mask in masks[1:]:
            merged = ImageChops.lighter(merged, mask)
        if p.get("only_mask", False):
            result = merged
        else:
            foreground = im.convert("RGBA")
            foreground.putalpha(merged)
            result = foreground
    # Edge Style is separate from rembg Edge Refinement. Fine is the
    # default and leaves model fine detail untouched. Other styles apply
    # controlled alpha-only adjustments; RGB stays from original pixels.
    style = p.get("edge_style", "Fine")
    if style in ("Smooth", "Crisp", "Feathered"):
        from PIL import ImageFilter
        mask_only = bool(p.get("only_mask", False))
        rgba = None if mask_only else result.convert("RGBA")
        alpha = result.convert("L") if mask_only else rgba.getchannel("A")
        resolution_factor = max(0.5, min(2.0, min(alpha.size) / 1024.0))
        phase("Refining edge mask")
        if style == "Smooth":
            alpha = alpha.filter(ImageFilter.GaussianBlur(max(0.7, 0.6 * resolution_factor)))
        elif style == "Feathered":
            alpha = alpha.filter(ImageFilter.GaussianBlur(max(1.25, 1.35 * resolution_factor)))
        elif style == "Crisp":
            alpha = alpha.filter(ImageFilter.UnsharpMask(radius=1.0 * resolution_factor,
                                                         percent=80, threshold=2))
        if mask_only:
            result = alpha
        else:
            rgba.putalpha(alpha)
            result = rgba
    # Cast shadows do not belong to the foreground segmentation mask.
    # Recover them independently from the ORIGINAL source, and store them
    # as a dark transparent overlay so that they remain shadows when the
    # subject is placed over another background (rather than pale fringes).
    # This conservative method intentionally only handles light, nearly
    # uniform backgrounds. For complex photographs, don't hallucinate a
    # shadow from background texture or nearby dark objects.
    shadow_mode = p.get("shadow_preservation", "Off")
    if shadow_mode != "Off" and not p.get("only_mask", False):
        phase("Recovering source cast shadows")
        from PIL import ImageFilter
        import numpy as np
        from scipy.ndimage import distance_transform_edt

        with Image.open(src) as source:
            source_rgba = source.convert("RGBA")
        fg_alpha_full = result.convert("RGBA").getchannel("A")
        width, height = source_rgba.size
        # Bound the expensive spatial analysis. Upscaling only the detected
        # shadow alpha afterward prevents 4K+ source dimensions from causing
        # disproportionate mask-analysis cost.
        factor = min(1.0, 1152.0 / max(width, height))
        work_size = (max(1, round(width * factor)),
                     max(1, round(height * factor)))
        small_source = source_rgba.resize(work_size, Image.Resampling.BILINEAR)
        small_alpha = fg_alpha_full.resize(work_size, Image.Resampling.BILINEAR)
        pix = np.asarray(small_source, dtype=np.uint8)
        alpha_arr = np.asarray(small_alpha, dtype=np.uint8)
        sh, sw = pix.shape[:2]
        patch = max(8, min(50, min(sw, sh) // 14))
        corners = ((0, 0), (0, sw - patch),
                   (sh - patch, 0), (sh - patch, sw - patch))
        background_samples = []
        for cy, cx in corners:
            pixels = pix[cy:cy + patch, cx:cx + patch, :3]
            visible_background = (alpha_arr[cy:cy + patch,
                                            cx:cx + patch] < 16)
            opaque_source = (pix[cy:cy + patch, cx:cx + patch, 3] > 245)
            usable = pixels[visible_background & opaque_source]
            if usable.shape[0] < patch * patch * 0.5:
                continue
            median = np.median(usable, axis=0)
            if np.mean(np.abs(usable.astype(np.float32) - median)) > 9:
                continue
            background_samples.append(median)

        if len(background_samples) >= 2:
            samples = np.asarray(background_samples, dtype=np.float32)
            bg = np.median(samples, axis=0)
            bg_luma = float(np.mean(bg))
            # Large disagreement between corners, dark backgrounds, or
            # distinctly chromatic backdrops are too ambiguous to recover
            # using an automatic neutral-shadow model.
            plausible = (bg_luma >= 205 and
                         float(np.max(np.ptp(samples, axis=0))) <= 14 and
                         float(np.ptp(bg)) <= 16 and
                         float(np.count_nonzero(alpha_arr < 16)) >= sw * sh * .07)
            if plausible:
                rgb = pix[:, :, :3].astype(np.float32)
                deficit_rgb = bg.reshape((1, 1, 3)) - rgb
                darkness = np.maximum(0, np.mean(deficit_rgb, axis=2))
                # Neutral cast shadows darken each colour channel similarly.
                # This suppresses saturated illustrations and colored props
                # mistakenly omitted by the foreground model.
                chroma = np.ptp(deficit_rgb, axis=2)
                neutral = np.clip((22.0 - chroma) / 16.0, 0.0, 1.0)
                # The darker a candidate is, the less likely it is a soft
                # cast shadow rather than a different background object.
                soft_region = np.clip((125.0 - darkness) / 42.0, 0.0, 1.0)
                dist = distance_transform_edt(alpha_arr < 128)
                falloff_radius = max(12.0, min(sw, sh) * 0.065)
                vicinity = np.exp(-0.5 * (dist / falloff_radius) ** 2)
                # Genuine shadow regions are mostly outside the subject.
                outside = np.clip((255.0 - alpha_arr.astype(np.float32)) / 190.0,
                                  0.0, 1.0)
                # Preserve the actual observed darkness. The modes change
                # shadow contrast; Off never creates a shadow layer.
                power = {"Subtle": 0.80, "Natural": 1.45,
                         "Strong": 2.65}.get(shadow_mode, 0.0)
                intensity = np.maximum(0.0, darkness - 2.0)
                shadow = intensity * (255.0 / max(1.0, bg_luma - 24.0))
                shadow *= power * neutral * soft_region * vicinity * outside
                shadow *= pix[:, :, 3].astype(np.float32) / 255.0
                shadow_u8 = np.uint8(np.clip(shadow, 0.0, 240.0))
                shadow_a = Image.fromarray(shadow_u8)
                shadow_a = shadow_a.filter(ImageFilter.GaussianBlur(0.8))
                if work_size != (width, height):
                    shadow_a = shadow_a.resize((width, height), Image.Resampling.BICUBIC)
                # Only create a layer if recoverable shadows exist at a
                # visible level; avoid empty layers on clean backgrounds.
                if shadow_a.getbbox() and shadow_a.getextrema()[1] >= 12:
                    shadow_rgba = Image.new("RGBA", (width, height), (24, 23, 24, 0))
                    shadow_rgba.putalpha(shadow_a)
                    shadow_rgba.save(dst + ".shadow.png", format="PNG")
    phase("Encoding result")
    result.save(dst, format="PNG")
except Exception:
    traceback.print_exc()
    sys.exit(1)
'''


class DownwardModelPicker(Gtk.Box):
    """In-panel expanding model list; never floats above or outside the dialog."""
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._labels = []
        self._active = -1
        self._changed = None
        self.button = Gtk.Button(label="Choose model  ▾")
        self.button.set_hexpand(True)
        self.button.set_tooltip_text("Expand the model list directly below this control.")
        self.pack_start(self.button, False, False, 0)
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(130)
        self.pack_start(self.revealer, False, False, 0)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Show the entire catalogue when room permits; otherwise scroll.
        # Avoid GTK popovers, which can flip above the selector.
        display = Gdk.Display.get_default()
        mon = display.get_primary_monitor() if display else None
        work_height = mon.get_workarea().height if mon else 800
        self._max_list_height = max(180, int(work_height * 0.70))
        self.scroll.set_size_request(-1, 190)
        self.items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.scroll.add(self.items)
        self.revealer.add(self.scroll)
        self.button.connect("clicked", self._toggle)

    def _toggle(self, _button):
        self.revealer.set_reveal_child(not self.revealer.get_reveal_child())

    def append_text(self, label):
        index = len(self._labels)
        self._labels.append(label)
        item = Gtk.Button(label=label)
        item.set_relief(Gtk.ReliefStyle.NONE)
        item.set_halign(Gtk.Align.FILL)
        item.set_tooltip_text(label)
        item.connect("clicked", lambda _button, idx=index: self._select(idx))
        self.items.pack_start(item, False, False, 0)
        self.scroll.set_size_request(-1, min(self._max_list_height, 36 * len(self._labels) + 8))
        item.show()

    def _select(self, index):
        self.set_active(index)
        self.revealer.set_reveal_child(False)

    def set_active(self, index):
        if not 0 <= index < len(self._labels):
            return
        changed = index != self._active
        self._active = index
        self.button.set_label(self._labels[index] + "  ▾")
        self.button.set_tooltip_text(self._labels[index])
        if changed and self._changed:
            self._changed(self)

    def get_active(self):
        return self._active

    def connect_changed(self, callback):
        self._changed = callback

def _stop_worker_process(proc, grace_seconds=1.0):
    """Reap a child process and its own POSIX process group; never signal GIMP."""
    if proc is None:
        return
    try:
        if proc.poll() is None:
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                proc.terminate()
            try:
                proc.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    proc.kill()
                proc.wait(timeout=2)
        else:
            proc.wait(timeout=0)
    except (OSError, subprocess.TimeoutExpired, ProcessLookupError, ChildProcessError):
        # Cleanup must not throw from a GTK destroy/response signal.
        pass


class InferenceJob:
    """One cancellable worker, polled by GTK without a nested event loop."""
    def __init__(self, pil_image, model_id, params, finished):
        from PIL import Image
        if not find_cached_model(model_id):
            raise RuntimeError("Model '" + model_id + "' is not cached. Install its checkpoint before inference.")
        self.finished = finished
        self.directory = tempfile.TemporaryDirectory(prefix="gimp-rembg-")
        self.process = None
        self.source_id = None
        self.cancelled = False
        self.started_at = time.monotonic()
        self.phase = "Starting worker"
        try:
            root = self.directory.name
            source, output = (os.path.join(root, x) for x in ("source.png", "result.png"))
            settings, worker = (os.path.join(root, x) for x in ("settings.json", "worker.py"))
            self.output = output
            self.log_path = os.path.join(root, "worker.log")
            pil_image.save(source)
            with open(settings, "w", encoding="utf-8") as f:
                json.dump(params, f)
            with open(worker, "w", encoding="utf-8") as f:
                f.write(_INFERENCE_SCRIPT)
            env = os.environ.copy()
            env.update(NUMBA_NUM_THREADS="1", NUMBA_THREADING_LAYER="workqueue",
                       OMP_NUM_THREADS="4", OPENBLAS_NUM_THREADS="4",
                       MKL_NUM_THREADS="4")
            with open(self.log_path, "w", encoding="utf-8") as log:
                self.process = subprocess.Popen(
                    [sys.executable, worker, source, output, model_id, settings],
                    stdout=log, stderr=subprocess.STDOUT, env=env,
                    start_new_session=(os.name == "posix"))
            self.source_id = GLib.timeout_add(120, self._poll)
        except Exception:
            # A failure after Popen (for example GLib.timeout_add) must not
            # orphan an inference worker when the dialog never opens.
            _stop_worker_process(self.process)
            self.directory.cleanup()
            raise

    def cancel(self):
        """Stop callbacks first; then terminate the isolated worker tree."""
        if self.cancelled:
            return
        self.cancelled = True
        if self.source_id is not None:
            try:
                GLib.source_remove(self.source_id)
            except (ValueError, GLib.Error):
                pass
            self.source_id = None
        # Reaping an inference process can take seconds (SIGTERM grace and
        # SIGKILL fallback). Never wait for it in a GTK response/destroy
        # handler: detach the cleanup into a plain Python thread. The GLib
        # polling source was removed above, so no callback can access the
        # temporary files while cleanup runs.
        proc, directory = self.process, self.directory
        def reap_cancelled_worker():
            try:
                _stop_worker_process(proc)
            finally:
                try:
                    directory.cleanup()
                except OSError:
                    pass
        threading.Thread(target=reap_cancelled_worker,
                         name="rembg-worker-cleanup", daemon=True).start()

    def _poll(self):
        if self.process.poll() is None:
            # Small worker log, read without blocking; reports genuine stages,
            # not fabricated percent-complete values.
            try:
                with open(self.log_path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if line.startswith("PHASE:"):
                            self.phase = line[6:].strip()
            except OSError:
                pass
            return True
        if self.process.poll() is None:
            return True
        self.source_id = None  # GLib removes this callback when it returns False
        result, error = None, None
        try:
            if self.cancelled:
                error = InterruptedError("Inference cancelled")
            elif self.process.returncode != 0 or not os.path.isfile(self.output):
                with open(self.log_path, encoding="utf-8", errors="replace") as f:
                    error = RuntimeError("Inference failed: " + f.read()[-3000:])
            else:
                from PIL import Image
                with Image.open(self.output) as im:
                    result = im.copy()
                shadow_path = self.output + ".shadow.png"
                if os.path.isfile(shadow_path):
                    with Image.open(shadow_path) as shadow_im:
                        result.info["rembg_cast_shadow"] = shadow_im.convert("RGBA")
        except Exception as exc:
            error = exc
        finally:
            self.directory.cleanup()
        try:
            self.finished(result, error)
        except Exception as exc:
            # Never propagate an inference completion exception through a
            # GLib source callback; report it without destabilising GTK.
            try:
                Gimp.message("rembg completion failed: " + str(exc))
            except Exception:
                pass
        return False


# ---------------------------------------------------------------------------
# Dialog UI
# ---------------------------------------------------------------------------

class RembgDialog(Gtk.Dialog):
    """Native GTK layout based on the user's upscayl-gimp.py dialog.

    No Upscayl execution code is imported; rembg remains the backend.
    """
    def __init__(self, image, layer):
        Gtk.Dialog.__init__(self, title="Remove Background — GIMP")
        # GTK3 display API, no deprecated Gdk.Screen monitor methods.
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() if display else None
        if monitor is None and display and display.get_n_monitors():
            monitor = display.get_monitor(0)
        # Target the available monitor work area, not a small fixed dialog.
        # Leave a modest safety margin for the window manager decorations.
        if monitor is not None:
            work = monitor.get_workarea()
            self.set_default_size(min(1240, max(800, int(work.width * .94))),
                                  min(1000, max(650, int(work.height * .94))))
        else:
            self.set_default_size(1160, 850)
        self.set_resizable(True)
        self.image, self.layer = image, layer
        self.result_pil, self._preview_result_pil = None, None
        self._closed, self._job, self._apply_result = False, None, False
        self._pending_start = None
        self._activity_timer = None
        self.settings = load_settings()
        self.preview_pixbuf_before = None
        self.preview_pixbuf_after = None
        self._pending_preview_before = None
        self._thumbnail_source = None
        self._thumbnail_error = None
        self.split_fraction = 0.50
        self.zoom = 1.0
        self.subject = "Person"
        self.quality = self.settings.get("quality", "Fine")
        self.edge_style = self.settings.get("edge_style", "Fine")
        self.output_mode = self.settings.get("output_mode", "New layer")
        self.connect("response", self._on_dialog_response)
        self.connect("destroy", self._on_destroy)
        # Follow GIMP's active GTK theme. Limit custom styling to the selected
        # choice state; do not install an application-wide dark palette.
        css = Gtk.CssProvider()
        css.load_from_data(b"""
        .rb-choice:checked {
            border: 1px solid #299dff;
            box-shadow: 0 0 7px rgba(41,157,255,.65);
        }
        """)
        self.get_style_context().add_provider(
            css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._selection_css = css
        self.reset_btn = self.add_button("Reset", Gtk.ResponseType.NONE)
        self.reset_btn.get_style_context().add_class("rb-secondary")
        self.reset_btn.connect("clicked", self.on_reset_clicked)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL).get_style_context().add_class("rb-secondary")
        self.preview_btn = self.add_button("Preview", Gtk.ResponseType.NONE)
        self.preview_btn.get_style_context().add_class("rb-secondary")
        self.preview_btn.connect("clicked", self.on_preview_clicked)
        self.preview_btn.set_tooltip_text("Generate an approximate preview using a reduced-resolution image")
        self.add_button("Apply  →", Gtk.ResponseType.OK).get_style_context().add_class("rb-primary")
        self.reset_btn.set_tooltip_text("Restore default model, refinement, quality, edge style and output")
        content = self.get_content_area()
        content.get_style_context().add_class("rb-root")
        content.set_border_width(12)
        content.set_spacing(8)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        titlebox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        title = Gtk.Label(label="Remove Background")
        title.set_xalign(0)
        title.get_style_context().add_class("rb-heading")
        titlebox.pack_start(title, False, False, 0)
        subtitle = Gtk.Label(label="Automatically remove the background from your image")
        subtitle.set_xalign(0)
        subtitle.get_style_context().add_class("rb-sub")
        titlebox.pack_start(subtitle, False, False, 0)
        head.pack_start(titlebox, True, True, 8)
        model_status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.model_ready_label = Gtk.Label(label="✓  rembg — checking…")
        self.model_ready_label.get_style_context().add_class("rb-status")
        model_status.pack_start(self.model_ready_label, False, False, 0)
        layer_label = Gtk.Label(label="Layer: " + layer.get_name())
        layer_label.set_tooltip_text("Source: the currently selected GIMP raster layer")
        model_status.pack_start(layer_label, False, False, 0)
        head.pack_start(model_status, False, False, 8)
        content.pack_start(head, False, False, 4)
        body = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_wide_handle(False)
        left_scroll = Gtk.ScrolledWindow()
        left_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
        left_scroll.set_size_request(480, -1)
        left_scroll.set_overlay_scrolling(False)
        left_scroll.set_tooltip_text("Scroll for all options; Advanced settings are at the bottom")
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.set_border_width(6)
        left.get_style_context().add_class("rb-panel")
        left_scroll.add(left)
        self._model_override = True
        model_heading = Gtk.Label(label="MODEL")
        model_heading.set_xalign(0)
        model_heading.get_style_context().add_class("rb-section")
        left.pack_start(model_heading, False, False, 0)
        self.model_combo = DownwardModelPicker()
        for model in MODELS:
            self.model_combo.append_text(model["id"])
        self.model_combo.set_active(MODEL_IDS.index(self.settings["model_id"]) if self.settings["model_id"] in MODEL_IDS else 0)
        self.model_combo.set_tooltip_text("Select a background-removal model. Hover over the description for details.")
        self.model_combo.connect_changed(self._model_overridden)
        left.pack_start(self.model_combo, False, False, 0)
        self.model_description = Gtk.Label()
        self.model_description.set_xalign(0)
        self.model_description.set_line_wrap(True)
        self.model_description.set_lines(2)
        self.model_description.set_ellipsize(3)
        self.model_description.set_max_width_chars(40)
        self.model_description.set_size_request(260, -1)
        self.model_description.get_style_context().add_class("rb-sub")
        left.pack_start(self.model_description, False, False, 0)
        edge_heading = Gtk.Label(label="EDGE REFINEMENT")
        edge_heading.set_xalign(0)
        edge_heading.get_style_context().add_class("rb-section")
        left.pack_start(edge_heading, False, False, 0)
        self.edge_mode_combo = Gtk.ComboBoxText()
        for edge in EDGE_MODES:
            self.edge_mode_combo.append_text(edge["label"])
        self.edge_mode_combo.set_active(EDGE_MODE_IDS.index(self.settings["edge_mode"]) if self.settings["edge_mode"] in EDGE_MODE_IDS else 0)
        self.edge_mode_combo.set_tooltip_text("Choose edge refinement; additional parameters appear in Advanced")
        left.pack_start(self.edge_mode_combo, False, False, 0)
        shadow_label = Gtk.Label(label="SHADOW PRESERVATION")
        shadow_label.set_xalign(0)
        shadow_label.get_style_context().add_class("rb-section")
        left.pack_start(shadow_label, False, False, 0)
        self.shadow_combo = Gtk.ComboBoxText()
        for mode in ("Off", "Subtle", "Natural", "Strong"):
            self.shadow_combo.append_text(mode)
        self.shadow_combo.set_active(("Off", "Subtle", "Natural", "Strong").index(
            self.settings.get("shadow_preservation", "Off")))
        self.shadow_combo.set_tooltip_text(
            "Recover soft cast shadows from light, uniform source backgrounds. "
            "Creates a separate translucent shadow layer beneath the cutout "
            "when a shadow is detected; complex backgrounds are skipped.")
        left.pack_start(self.shadow_combo, False, False, 0)
        self.quality_buttons = self._choice_grid(left, "QUALITY", [
            ("Fast", "Fast"), ("Std", "Standard"), ("Fine", "Fine")],
            3, self.quality, "quality", self._choice_changed,
            "Quality controls the preview inference resolution; final Apply uses full resolution")
        self.edge_buttons = self._choice_grid(left, "EDGE STYLE", [
            ("Fine", "Fine"), ("Smooth", "Smooth"),
            ("Crisp", "Crisp"), ("Feathered", "Feathered")],
            4, self.edge_style, "edge_style", self._choice_changed,
            "Fine preserves the original detailed mask; Smooth softens slightly; Crisp sharpens edges; Feathered softens more")
        self.advanced = Gtk.Expander(label="Advanced")
        self.advanced.set_tooltip_text("All model and mask-processing parameters")
        advanced_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        advanced_box.set_border_width(8)
        self.alpha_matting_params_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.vitmatte_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.vitmatte_combo = Gtk.ComboBoxText()
        for item in VITMATTE_CHECKPOINTS:
            self.vitmatte_combo.append_text(item["label"] + "  [" + item["size"] + "]")
        self.vitmatte_combo.set_active(VITMATTE_CHECKPOINT_IDS.index(self.settings["vitmatte_model"]))
        self.vitmatte_combo.set_tooltip_text("Choose a ViTMatte refinement checkpoint")
        self._advanced_row(self.vitmatte_row, "ViTMatte checkpoint", self.vitmatte_combo)
        advanced_box.pack_start(self.vitmatte_row, False, False, 0)
        self.chk_post_process = self._checkbox_row(advanced_box, "Post-process mask", PARAM_HELP["post_process_mask"], self.settings["post_process_mask"])
        self.chk_only_mask = self._checkbox_row(advanced_box, "Mask only", PARAM_HELP["only_mask"], self.settings["only_mask"])
        self.update_check_btn = Gtk.Button(label="Installation & Diagnostics")
        self.update_check_btn.connect("clicked", self.on_combined_diagnostics_clicked)
        self.update_check_btn.set_tooltip_text("Check dependencies and display INSTALL / UNINSTALL instructions. Update checking is manual only.")
        advanced_box.pack_start(self.update_check_btn, False, False, 0)
        self.manual_update_btn = Gtk.Button(label="Check rembg Updates (online)")
        self.manual_update_btn.set_tooltip_text("Explicitly check PyPI for a newer rembg version; requires internet. No automatic installation.")
        self.manual_update_btn.connect("clicked", self.on_check_updates_clicked)
        advanced_box.pack_start(self.manual_update_btn, False, False, 0)
        self.update_status_label = Gtk.Label(label="")
        self.update_status_label.set_line_wrap(True)
        advanced_box.pack_start(self.update_status_label, False, False, 0)
        self.advanced.add(advanced_box)
        left.pack_start(Gtk.Separator(), False, False, 0)
        self.output_buttons = self._choice_grid(left, "PLACE RESULT", [
            ("New layer", "New layer"),
            ("New image", "New image")], 2, self.output_mode, "output_mode",
            self._choice_changed, "Choose a new masked layer in this image or a separate new image")
        # Same structure as upscayl-gimp.py: advanced options follow the
        # principal controls rather than hiding a final output selector.
        left.pack_start(self.advanced, False, False, 0)

        body.pack1(left_scroll, resize=True, shrink=True)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        right.set_border_width(6)
        right.get_style_context().add_class("rb-panel")
        tabrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tabrow.pack_start(Gtk.Label(label="PREVIEW"), True, True, 0)
        self.view_label = Gtk.Label(label="Before  ◀────▶  After")
        tabrow.pack_end(self.view_label, False, False, 0)
        right.pack_start(tabrow, False, False, 0)
        self.preview_canvas = Gtk.DrawingArea()
        self.preview_canvas.set_size_request(220, 180)
        self.preview_canvas.get_style_context().add_class("rb-preview")
        self.preview_canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON1_MOTION_MASK | Gdk.EventMask.SCROLL_MASK)
        self.preview_canvas.connect("draw", self._draw_preview)
        self.preview_canvas.connect("button-press-event", self._drag_split)
        self.preview_canvas.connect("motion-notify-event", self._drag_split)
        self.preview_canvas.connect("scroll-event", self._scroll_zoom)
        self.preview_canvas.set_tooltip_text("Drag the blue divider to compare before and after; scroll to zoom")
        right.pack_start(self.preview_canvas, True, True, 0)
        zoomrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        for caption, zoom, tooltip in (("−", 0.8, "Zoom out"), ("+", 1.25, "Zoom in"), ("Fit to window", 0, "Fit preview to window")):
            btn = Gtk.Button(label=caption)
            btn.set_tooltip_text(tooltip)
            btn.connect("clicked", self._set_zoom, zoom)
            zoomrow.pack_start(btn, False, False, 0)
        self.zoom_label = Gtk.Label(label="Fit")
        zoomrow.pack_end(self.zoom_label, False, False, 0)
        right.pack_start(zoomrow, False, False, 0)
        body.pack2(right, resize=True, shrink=True)
        content.pack_start(body, True, True, 0)
        self.status_label = Gtk.Label(label="Ready. Preview or Apply starts processing on demand.")
        self.status_label.set_xalign(0)
        content.pack_start(self.status_label, False, False, 3)
        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        content.pack_start(self.progress, False, False, 0)
        self._model_override = True
        self._model_overridden(self.model_combo)
        self._choice_changed(None)
        content.show_all()
        self.advanced.set_expanded(self.settings.get("advanced_expanded", True))
        self.edge_mode_combo.connect("changed", self.on_edge_mode_changed)
        self._update_edge_mode_visibility()
        body.set_position(525)
        # No eager image export, dependency scans or model imports at startup.
        # Large layers must not delay the initial GTK window paint.
        self.model_ready_label.set_text("Ready to preview")
        # Retrieve the selected layer's thumbnail only AFTER the window is
        # mapped. An idle source scheduled during construction can run before
        # the drawing area has a mapped GdkWindow on some GTK/GIMP setups.
        # The bounded GIMP thumbnail API avoids full-resolution PNG export.
        self.connect("map", self._on_preview_window_mapped)

    def _advanced_row(self, parent, caption, widget):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=caption)
        label.set_xalign(0)
        row.pack_start(label, False, False, 0)
        row.pack_start(widget, True, True, 0)
        parent.pack_start(row, False, False, 0)

    def _choice_grid(self, parent, title, choices, columns, active, field, callback, tooltip):
        header = Gtk.Label(label=title)
        header.set_xalign(0)
        header.get_style_context().add_class("rb-section")
        parent.pack_start(header, False, False, 0)
        grid = Gtk.Grid(column_spacing=5, row_spacing=5)
        grid.set_column_homogeneous(True)
        buttons = {}
        group = None
        for i, (value, caption) in enumerate(choices):
            button = Gtk.RadioButton.new_with_label_from_widget(group, caption)
            if group is None:
                group = button
            button.set_mode(True)
            button.set_tooltip_text(tooltip + " — " + value)
            button.get_style_context().add_class("rb-choice")
            button.get_style_context().add_provider(self._selection_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            button.set_hexpand(True)
            button.set_size_request(-1, 30)
            button.connect("toggled", self._on_choice, field, value, callback)
            grid.attach(button, i % columns, i // columns, 1, 1)
            buttons[value] = button
        parent.pack_start(grid, False, False, 0)
        buttons[active].set_active(True)
        return buttons

    def _on_choice(self, button, field, value, callback):
        if button.get_active():
            setattr(self, field, value)
            callback(button)

    def _choice_changed(self, _button):
        # Edge Refinement is independent of Edge Style and Quality. Changing
        # a radio button must never silently reset the selected refinement.
        if hasattr(self, "preview_canvas"):
            self.preview_canvas.queue_draw()

    def _model_overridden(self, _combo):
        self._model_override = True
        idx = self.model_combo.get_active()
        if idx >= 0 and hasattr(self, "model_description"):
            description = MODELS[idx]["help"]
            self.model_description.set_text(description)
            self.model_description.set_tooltip_text(description)

    def on_combined_diagnostics_clicked(self, button):
        self.on_diagnostics_clicked(button)

    def _set_zoom(self, _button, factor):
        self.zoom = 1.0 if factor == 0 else max(0.2, min(8.0, self.zoom * factor))
        self.zoom_label.set_text("Fit" if factor == 0 else str(round(self.zoom * 100)) + "%")
        self.preview_canvas.queue_draw()

    def _scroll_zoom(self, _widget, event):
        self._set_zoom(None, 1.12 if event.direction == Gdk.ScrollDirection.UP else 1 / 1.12)
        return True

    def _drag_split(self, widget, event):
        if event.type == Gdk.EventType.BUTTON_PRESS or event.state & Gdk.ModifierType.BUTTON1_MASK:
            self.split_fraction = max(0.0, min(1.0, event.x / max(1, widget.get_allocated_width())))
            widget.queue_draw()
        return True

    def _draw_preview(self, widget, cr):
        import math
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        # Match the host GTK theme rather than forcing a blue/navy panel.
        # Gtk.StyleContext.get_background_color() is deprecated in GTK3.
        # GTK paints the actual theme background, including themes that use
        # gradients/images rather than a solid CSS background-color.
        try:
            widget.get_style_context().render_background(cr, 0, 0, w, h)
        except Exception:
            # The preview must still paint if an unusual GTK theme rejects
            # render_background; never let theme drawing hide the image.
            pass
        before = self.preview_pixbuf_before
        after = self.preview_pixbuf_after
        if before is None:
            cr.set_source_rgb(0.48, 0.48, 0.48)
            cr.select_font_face("Sans", 0, 0)
            cr.set_font_size(14)
            prompt = ("Source preview unavailable — see status below"
                      if self._thumbnail_error else "Loading source preview…")
            ext = cr.text_extents(prompt)
            cr.move_to(max(8, (w - ext.width) / 2), h / 2)
            cr.show_text(prompt)
            return False
        iw, ih = before.get_width(), before.get_height()
        scale = min(w / max(iw, 1), h / max(ih, 1)) * self.zoom
        dw, dh = iw * scale, ih * scale
        x, y = (w - dw) / 2, (h - dh) / 2
        split = w * self.split_fraction
        cr.save()
        cr.rectangle(0, 0, split if after is not None else w, h)
        cr.clip()
        cr.translate(x, y)
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, before, 0, 0)
        cr.paint()
        cr.restore()
        if after is None:
            return False
        cr.save()
        cr.rectangle(max(split, x), max(0, y), max(0, min(w, x + dw) - max(split, x)), max(0, min(h, y + dh) - max(0, y)))
        cr.clip()
        cell = 13
        for yy in range(max(0, int(y // cell) * cell), min(h, int(y + dh) + cell), cell):
            for xx in range(max(0, int(x // cell) * cell), min(w, int(x + dw) + cell), cell):
                c = 0.78 if (xx // cell + yy // cell) % 2 else 0.91
                cr.set_source_rgb(c, c, c)
                cr.rectangle(xx, yy, cell, cell)
                cr.fill()
        cr.translate(x, y)
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, after or before, 0, 0)
        cr.paint()
        cr.restore()
        cr.set_source_rgb(0.17, 0.64, 1.0)
        cr.set_line_width(3)
        cr.move_to(split, max(0, y))
        cr.line_to(split, min(h, y + dh))
        cr.stroke()
        cr.arc(split, h / 2, 18, 0, 2 * math.pi)
        cr.set_source_rgb(0.17, 0.64, 1.0)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.set_font_size(17)
        cr.move_to(split - 10, h / 2 + 6)
        cr.show_text("↔")
        return False

    def on_reset_clicked(self, _button):
        self.settings = dict(DEFAULT_SETTINGS)
        self.model_combo.set_active(MODEL_IDS.index(self.settings["model_id"]) if self.settings["model_id"] in MODEL_IDS else 0)
        self.edge_mode_combo.set_active(EDGE_MODE_IDS.index(self.settings["edge_mode"]) if self.settings["edge_mode"] in EDGE_MODE_IDS else 0)
        self.vitmatte_combo.set_active(VITMATTE_CHECKPOINT_IDS.index(self.settings["vitmatte_model"]))
        self.shadow_combo.set_active(0)
        self.chk_post_process.set_active(False)
        self.chk_only_mask.set_active(False)
        self._update_edge_mode_visibility()
        self._model_override = True
        self.quality_buttons["Fine"].set_active(True)
        self.edge_buttons["Fine"].set_active(True)
        self.output_buttons["New layer"].set_active(True)
        self._choice_changed(None)
        self._model_overridden(self.model_combo)
        self.status_label.set_text("Settings reset to defaults")

    def _diagnostics_report(self):
        import importlib.util
        import importlib.metadata
        import platform
        modules = (("rembg", "rembg"), ("Pillow", "PIL"),
                   ("numpy", "numpy"), ("onnxruntime", "onnxruntime"),
                   ("pymatting", "pymatting"), ("scikit-image", "skimage"),
                   ("scipy", "scipy"))
        lines = ["Python: " + sys.executable, "OS: " + platform.platform(), ""]
        missing = []
        for package, module in modules:
            try:
                present = importlib.util.find_spec(module) is not None
            except (ImportError, ValueError):
                present = False
            if not present:
                missing.append(package)
            try:
                version = importlib.metadata.version(package) if present else ""
            except importlib.metadata.PackageNotFoundError:
                version = "installed (version unknown)" if present else ""
            lines.append(("Working: " if present else "MISSING: ") + package + (" " + version if version else ""))
        lines.extend(["", "Inference provider: CPUExecutionProvider (explicitly selected)",
                      "CUDA is not attempted by this plugin build.",
                      "Reported CUDA/cuDNN mismatch must be diagnosed separately before enabling GPU inference.",
                      "Installed packages and advertised execution providers do not prove that CUDA can load a model.",
                      "Package presence is not an inference test.", "", "INSTALL (manual; only if needed):"])
        if sys.platform.startswith("linux"):
            distro = ""
            try:
                with open("/etc/os-release", encoding="utf-8") as f:
                    distro = f.read().lower()
            except OSError:
                pass
            if "arch" in distro or "cachyos" in distro or "manjaro" in distro:
                lines.extend(["Arch / CachyOS:",
                    "  sudo pacman -S --needed python-numpy python-pillow python-pymatting python-scikit-image python-scipy",
                    "  pacman -Ss onnxruntime  # inspect available provider variants first",
                    "  /usr/bin/python3 -m pip install --no-deps rembg  # only if pip-managed; follow PEP 668 rules"])
                uninstall = ["Arch / CachyOS:",
                    "  pacman -Q | grep -E 'onnxruntime|cuda|cudnn|python-'",
                    "  /usr/bin/python3 -m pip show rembg  # check ownership first",
                    "  /usr/bin/python3 -m pip uninstall rembg  # only if pip-managed",
                    "  sudo pacman -Rns PACKAGE_NAME  # inspect dependencies first; never blindly remove CUDA"]
            elif "ubuntu" in distro or "debian" in distro or "fedora" in distro:
                lines.extend(["Linux (isolated environment):", "  python3 -m venv ~/rembg-venv",
                    "  ~/rembg-venv/bin/pip install 'rembg[cpu]' pillow numpy",
                    "  Note: GIMP must use this environment for these packages to be visible."])
                uninstall = ["Linux (isolated environment):", "  ~/rembg-venv/bin/pip uninstall rembg onnxruntime",
                    "  rm -r ~/rembg-venv  # only if this dedicated environment is no longer needed"]
            else:
                lines.append("Linux: use the distribution package manager or an isolated Python environment.")
                uninstall = ["Linux: identify package ownership before removing anything."]
        elif sys.platform == "win32":
            lines.extend(["Windows (PowerShell):", "  py -m pip install 'rembg[cpu]' pillow numpy"])
            uninstall = ["Windows (PowerShell):", "  py -m pip uninstall rembg onnxruntime onnxruntime-gpu"]
        elif sys.platform == "darwin":
            lines.extend(["macOS (Terminal):", "  python3 -m pip install 'rembg[cpu]' pillow numpy"])
            uninstall = ["macOS (Terminal):", "  python3 -m pip uninstall rembg onnxruntime"]
        else:
            uninstall = ["Check package ownership before removing any dependency."]
        lines.extend(["", "UNINSTALL (manual; only when necessary):", *uninstall])
        lines.extend(["", "CAUTION: commands are examples, not automatic actions.",
                      "Do not uninstall system CUDA/cuDNN until the installed packages and other consumers are identified.",
                      "The Python interpreter running GIMP must see the installed packages."])
        return "\n".join(lines), missing

    def _startup_diagnostics(self):
        if self._closed:
            return False
        try:
            _, missing = self._diagnostics_report()
            if missing:
                self.status_label.set_text("Missing dependencies — open diagnostics")
                self.model_ready_label.set_text("⚠  rembg needs attention")
                self.on_diagnostics_clicked(None, alert=True)
            else:
                self.status_label.set_text("Ready — dependencies found; CPU inference not yet tested")
                self.model_ready_label.set_text("✓  rembg ready")
        except Exception as exc:
            self.status_label.set_text("Diagnostics failed: " + str(exc))
        return False

    def on_diagnostics_clicked(self, _button, alert=False):
        report, missing = self._diagnostics_report()
        dialog = Gtk.Dialog(title="Remove Background — Installation Diagnostics",
                            transient_for=self, modal=True)
        dialog.set_default_size(780, 540)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        box = dialog.get_content_area()
        box.set_spacing(6)
        summary = Gtk.Label(label=("Missing: " + ", ".join(missing)) if missing else
                            "Dependencies found. CUDA unavailable; CPU mode configured.")
        summary.set_xalign(0)
        summary.set_markup("<span foreground='#ff6666'><b>CHECK FAILED: " + GLib.markup_escape_text(", ".join(missing)) + "</b></span>" if missing else "<span foreground='#78d99a'><b>CHECK PASSED: Dependencies detected (inference not tested)</b></span>")
        box.pack_start(summary, False, False, 6)
        instructions = Gtk.Label()
        instructions.set_xalign(0)
        instructions.set_markup("<b>INSTALL</b>  ·  <b>UNINSTALL</b> instructions appear in the report below")
        box.pack_start(instructions, False, False, 3)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        view = Gtk.TextView()
        view.set_editable(False)
        view.set_monospace(True)
        view.get_buffer().set_text(report)
        scroller.add(view)
        box.pack_start(scroller, True, True, 0)
        copy_btn = Gtk.Button(label="Copy Diagnostics / Commands")
        copy_btn.connect("clicked", lambda *_: Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(report, -1))
        box.pack_start(copy_btn, False, False, 3)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    # -- UI helper builders -------------------------------------------

    def _checkbox_row(self, box, label, help_text, default=False):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        chk = Gtk.CheckButton(label=label)
        chk.set_active(default)
        chk.set_tooltip_text(help_text)
        row.pack_start(chk, True, True, 0)
        box.pack_start(row, False, False, 0)
        return chk

    def _spin_row(self, box, label, help_text, lo, hi, default):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl = Gtk.Label(label=label)
        lbl.set_tooltip_text(help_text)
        adj = Gtk.Adjustment(value=default, lower=lo, upper=hi,
                              step_increment=1, page_increment=10)
        spin = Gtk.SpinButton(adjustment=adj)
        spin.set_tooltip_text(help_text)
        row.pack_start(lbl, True, True, 0)
        row.pack_start(spin, False, False, 0)
        box.pack_start(row, False, False, 0)
        return spin

    def on_edge_mode_changed(self, combo):
        self._update_edge_mode_visibility()

    def _update_edge_mode_visibility(self):
        edge_mode = self._current_edge_mode_id()
        self.alpha_matting_params_box.set_visible(False)
        self.vitmatte_row.set_visible(edge_mode == "vitmatte")

    def on_model_help_clicked(self, btn):
        dlg = Gtk.MessageDialog(
            transient_for=self, flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Which model should I use?"
        )
        dlg.add_button("Download Models…", 1001)
        lines = []
        for m in MODELS:
            lines.append(f"• {m['label']}  [{m['size']}]\n   {m['help']}\n")
        dlg.format_secondary_text("\n".join(lines))
        dlg.set_default_size(600, 500)
        response = dlg.run()
        dlg.destroy()
        if response == 1001:
            self.on_download_models_clicked()

    def on_download_models_clicked(self):
        dlg = Gtk.Dialog(title="Download Models", transient_for=self, modal=True)
        dlg.set_default_size(560, 320)
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
        download_btn = dlg.add_button("Download Selected", Gtk.ResponseType.APPLY)
        area = dlg.get_content_area()
        area.set_spacing(10)
        area.set_border_width(12)
        chooser = Gtk.ComboBoxText()
        for model in MODELS:
            chooser.append_text(model["label"])
        chooser.set_active(max(0, self.model_combo.get_active()))
        area.pack_start(chooser, False, False, 0)
        status = Gtk.Label(xalign=0)
        status.set_line_wrap(True)
        area.pack_start(status, False, False, 0)
        state = {"proc": None, "timer": None, "log": None}

        def refresh(*_):
            idx = chooser.get_active()
            mid = MODEL_IDS[idx] if idx >= 0 else None
            cached = find_cached_model(mid) if mid else None
            if state["proc"] is None:
                download_btn.set_sensitive(mid is not None and cached is None)
                status.set_text(("Checkpoint found: " + cached) if cached else
                                "Download requires confirmation and internet access.")

        def cleanup():
            if state["timer"] is not None:
                GLib.source_remove(state["timer"])
                state["timer"] = None
            proc = state["proc"]
            state["proc"] = None
            _stop_worker_process(proc)
            if state["log"]:
                try:
                    os.unlink(state["log"])
                except OSError:
                    pass
                state["log"] = None

        def poll():
            proc = state["proc"]
            if proc is None or proc.poll() is None:
                return proc is not None
            state["timer"] = None
            rc = proc.returncode
            details = ""
            if rc:
                try:
                    with open(state["log"], encoding="utf-8", errors="replace") as f:
                        details = f.read()[-2000:]
                except OSError:
                    pass
            cleanup()
            refresh()
            if rc:
                status.set_text("Model preparation failed:\n" + details)
            elif not find_cached_model(MODEL_IDS[chooser.get_active()]):
                status.set_text("Download completed; model cache could not be verified.")
            return False

        def respond(_dialog, response):
            if response != Gtk.ResponseType.APPLY:
                cleanup()
                dlg.destroy()
                return
            if state["proc"] is not None:
                return
            idx = chooser.get_active()
            if idx < 0:
                return
            mid = MODEL_IDS[idx]
            if find_cached_model(mid):
                refresh()
                return
            confirm = Gtk.MessageDialog(transient_for=dlg, modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text="Download model " + mid + "?")
            confirm.format_secondary_text("This may download hundreds of megabytes.")
            yes = confirm.run() == Gtk.ResponseType.YES
            confirm.destroy()
            if not yes:
                return
            log = tempfile.NamedTemporaryFile(prefix="rembg-model-", suffix=".log", delete=False)
            state["log"] = log.name
            log.close()
            script = ("from rembg import new_session; new_session(" + repr(mid) +
                      ", providers=['CPUExecutionProvider'])")
            env = os.environ.copy()
            env.update(NUMBA_NUM_THREADS="1", NUMBA_THREADING_LAYER="workqueue", OMP_NUM_THREADS="4", OPENBLAS_NUM_THREADS="4")
            try:
                with open(state["log"], "w") as output:
                    state["proc"] = subprocess.Popen([sys.executable, "-c", script],
                        stdout=output, stderr=subprocess.STDOUT, env=env,
                        start_new_session=(os.name == "posix"))
                status.set_text("Downloading / validating " + mid + "…")
                download_btn.set_sensitive(False)
                state["timer"] = GLib.timeout_add(200, poll)
            except Exception as exc:
                cleanup()
                status.set_text("Unable to start download: " + str(exc))

        chooser.connect("changed", refresh)
        dlg.connect("response", respond)
        dlg.connect("destroy", lambda *_: cleanup())
        refresh()
        dlg.show_all()
        return False

    def on_open_models_folder_clicked(self, btn):
        models_root = get_rembg_models_root()
        # If the primary (current-layout) folder doesn't exist yet, but
        # the legacy ~/.u2net folder does, open that instead — most
        # likely case is a fresh plugin install where no model has been
        # downloaded THROUGH THIS SESSION yet, but older models exist
        # under the legacy path rembg still reads from.
        target = models_root
        if not os.path.isdir(models_root):
            legacy = get_rembg_legacy_models_root()
            if os.path.isdir(legacy):
                target = legacy

        success, error = open_folder_in_file_manager(target)
        if not success:
            Gimp.message(
                f"Couldn't open the models folder automatically.\n\n"
                f"Location: {target}\n\nError: {error}"
            )

    def on_edge_mode_help_clicked(self, btn):
        dlg = Gtk.MessageDialog(
            transient_for=self, flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Which edge mode should I use?"
        )
        lines = []
        for em in EDGE_MODES:
            lines.append(f"• {em['label']}\n   {em['help']}\n")
        dlg.format_secondary_text("\n".join(lines))
        dlg.set_default_size(600, 500)
        dlg.run()
        dlg.destroy()

    def on_check_updates_clicked(self, btn):
        self.update_check_btn.set_sensitive(False)
        self.update_status_label.set_text("Checking...")

        def worker_thread():
            installed = get_installed_rembg_version()
            latest = get_latest_pypi_version("rembg")
            GLib.idle_add(self._on_update_check_done, installed, latest)

        threading.Thread(target=worker_thread, daemon=True).start()

    def _on_update_check_done(self, installed, latest):
        self.update_check_btn.set_sensitive(True)

        if latest is None:
            self.update_status_label.set_text(
                "Couldn't reach PyPI to check (no network, or PyPI "
                "unreachable).")
            return False

        if installed is None:
            self.update_status_label.set_text(
                f"Latest on PyPI: {latest}. Could not read your installed "
                f"version.")
            return False

        if installed == latest:
            self.update_status_label.set_markup(
                "<span foreground='#78d99a'>" + GLib.markup_escape_text(f"Up to date (rembg {installed}).") + "</span>")
            return False

        # An update is available. Never run pip ourselves — show the exact
        # command and let the user run it, since:
        #  1. GIMP has already imported this session's rembg module, and
        #     Python doesn't reliably pick up an upgraded package without
        #     restarting the process that imported it, so an in-place
        #     upgrade wouldn't take effect until GIMP is restarted anyway.
        #  2. Running package-manager commands on the user's system without
        #     an explicit, visible confirmation step isn't something this
        #     plugin should do quietly.
        cmd = build_upgrade_command()
        self.update_status_label.set_markup(
            "<span foreground='#ff6666'>" + GLib.markup_escape_text(f"Update available: {installed} → {latest}") + "</span>")

        dlg = Gtk.MessageDialog(
            transient_for=self, flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=f"rembg update available: {installed} → {latest}"
        )
        dlg.format_secondary_text(
            "Run this command in a terminal, then restart GIMP for it to "
            "take effect:\n\n"
            f"{cmd}\n\n"
            "This upgrades rembg only (--no-deps), matching how it was "
            "originally installed, so it won't pull in unrelated packages. "
            "If you installed rembg differently (e.g. via your distro's "
            "package manager instead of pip), use that same method to "
            "upgrade instead."
        )
        dlg.set_default_size(560, 260)

        # Make the command easy to copy: a selectable, read-only entry
        # field the user can click into and Ctrl+C, since MessageDialog's
        # secondary text often isn't selectable in all GTK themes.
        entry = Gtk.Entry()
        entry.set_text(cmd)
        entry.set_editable(False)
        entry.set_can_focus(True)
        content_area = dlg.get_content_area()
        content_area.pack_end(entry, False, False, 6)
        entry.show()

        dlg.run()
        dlg.destroy()
        return False

    # -- Preview handling ------------------------------------------------

    def _on_preview_window_mapped(self, *_args):
        """Request the initial preview after GTK has mapped the dialog."""
        if self._thumbnail_source is None and self.preview_pixbuf_before is None:
            self._thumbnail_source = GLib.timeout_add(60, self._load_before_preview)
        return False

    def _load_before_preview(self):
        """Load one bounded GIMP layer thumbnail on the GTK main context.

        Uses official Gimp.Drawable thumbnail APIs, followed by the image
        thumbnail only when there is exactly one layer. Neither path exports
        full-resolution pixels or imports rembg, Pillow or ONNX Runtime.
        """
        self._thumbnail_source = None
        if self._closed or self.preview_pixbuf_before is not None:
            return False
        problems = []
        thumb = None
        try:
            thumb = self.layer.get_thumbnail(
                768, 768, Gimp.PixbufTransparency.KEEP_ALPHA)
            if thumb is None:
                problems.append("drawable get_thumbnail returned no image")
        except Exception as exc:
            problems.append("drawable thumbnail: " + str(exc))

        # Some precision/format combinations fail to create a pixbuf even
        # though the underlying GIMP thumbnail data was produced correctly.
        # Construct one from the documented RGB[A] GBytes without copying a
        # full-resolution layer. Keep its GBytes alive through the pixbuf.
        if thumb is None:
            try:
                payload = self.layer.get_thumbnail_data(768, 768)
                data, width, height, bpp = payload
                if data is None or bpp not in (3, 4) or width < 1 or height < 1:
                    raise RuntimeError(f"unsupported thumbnail format: {width}x{height}, {bpp} Bpp")
                thumb = GdkPixbuf.Pixbuf.new_from_bytes(
                    data, GdkPixbuf.Colorspace.RGB, bpp == 4,
                    8, width, height, width * bpp)
                if thumb is None:
                    raise RuntimeError("pixbuf allocation failed")
            except Exception as exc:
                problems.append("drawable thumbnail data: " + str(exc))

        # This fallback is only equivalent to a layer thumbnail when the
        # document has a single layer; never silently show a composite of
        # unrelated layers as the selected-layer source.
        if thumb is None:
            try:
                if len(self.image.get_layers()) == 1:
                    thumb = self.image.get_thumbnail(
                        768, 768, Gimp.PixbufTransparency.KEEP_ALPHA)
                    if thumb is None:
                        problems.append("single-layer image thumbnail returned no image")
            except Exception as exc:
                problems.append("single-layer image thumbnail: " + str(exc))

        if thumb is not None:
            self.preview_pixbuf_before = thumb
            self._thumbnail_error = None
            self.preview_canvas.queue_draw()
        else:
            self._thumbnail_error = "; ".join(problems) or "GIMP returned no thumbnail"
            self.status_label.set_text("Source thumbnail unavailable: " + self._thumbnail_error)
            self.preview_canvas.queue_draw()
            print("rembg-gimp: " + self._thumbnail_error, file=sys.stderr)
        return False

    def _scale_for_preview(self, pixbuf, max_dim=320):
        w, h = pixbuf.get_width(), pixbuf.get_height()
        scale = min(max_dim / w, max_dim / h, 1.0)
        if scale < 1.0:
            pixbuf = pixbuf.scale_simple(int(w * scale), int(h * scale),
                                          GdkPixbuf.InterpType.BILINEAR)
        return pixbuf

    def _current_model_id(self):
        idx = self.model_combo.get_active()
        return MODEL_IDS[idx] if idx >= 0 else DEFAULT_SETTINGS["model_id"]

    def _current_edge_mode_id(self):
        idx = self.edge_mode_combo.get_active()
        return EDGE_MODE_IDS[idx]

    def _current_vitmatte_checkpoint_id(self):
        idx = self.vitmatte_combo.get_active()
        return VITMATTE_CHECKPOINT_IDS[idx]

    def _current_params(self):
        return {
            "edge_mode": self._current_edge_mode_id(),
            "vitmatte_model": self._current_vitmatte_checkpoint_id(),
            "only_mask": self.chk_only_mask.get_active(),
            "shadow_preservation": self.shadow_combo.get_active_text(),
            "post_process_mask": self.chk_post_process.get_active(),
            "edge_style": self.edge_style,
            "quality": self.quality,
            "output_mode": self.output_mode,
            "advanced_expanded": self.advanced.get_expanded(),
        }

    def _save_current_settings(self):
        settings = dict(self._current_params())
        settings["model_id"] = self._current_model_id()
        save_settings(settings)

    def _cancel_pending_start(self):
        if self._pending_start is not None:
            try:
                GLib.source_remove(self._pending_start)
            except (ValueError, GLib.Error):
                pass
            self._pending_start = None
        if self._activity_timer is not None:
            try:
                GLib.source_remove(self._activity_timer)
            except (ValueError, GLib.Error):
                pass
            self._activity_timer = None

    def _activity_tick(self):
        if self._closed or (self._job is None and self._pending_start is None):
            self._activity_timer = None
            return False
        self.progress.pulse()
        if self._job is not None:
            elapsed = int(time.monotonic() - self._job.started_at)
            self.status_label.set_text(f"{self._job.phase} · {elapsed}s elapsed · CPU")
        return True

    def _begin_activity(self):
        if self._activity_timer is None:
            self._activity_timer = GLib.timeout_add(250, self._activity_tick)

    def _on_destroy(self, *_):
        self._save_current_settings()
        self._closed = True
        if self._thumbnail_source is not None:
            try:
                GLib.source_remove(self._thumbnail_source)
            except (ValueError, GLib.Error):
                pass
            self._thumbnail_source = None
        self._cancel_pending_start()
        if self._job is not None:
            self._job.cancel()
            self._job = None

    def _ensure_model_available(self):
        """Stop early with a visible installation offer, before image export."""
        model_id = self._current_model_id()
        if find_cached_model(model_id):
            return True
        question = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text="Model not installed: " + model_id)
        question.format_secondary_text(
            "This checkpoint is required to remove the background. "
            "Open the model installer now? Downloading requires internet access.")
        question.add_button("Not now", Gtk.ResponseType.CANCEL)
        question.add_button("Install model…", Gtk.ResponseType.OK)
        choice = question.run()
        question.destroy()
        if choice == Gtk.ResponseType.OK:
            self.on_download_models_clicked()
        self.status_label.set_text("Model not installed: " + model_id)
        return False

    def _start_apply_idle(self):
        self._pending_start = None
        if self._closed:
            return False
        try:
            self.status_label.set_text("Exporting selected layer at full resolution…")
            source, self._high_bit_depth, _ = layer_to_pil(self.layer)
            if self._closed:
                return False
            self._job = InferenceJob(source, self._apply_model,
                                     self._apply_params, self._apply_finished)
        except Exception as exc:
            self.status_label.set_text("Apply failed: " + str(exc))
            Gimp.message("rembg: " + str(exc))
            self.get_widget_for_response(Gtk.ResponseType.OK).set_sensitive(True)
            self.preview_btn.set_sensitive(True)
        return False

    def _on_dialog_response(self, dialog, response):
        if response == Gtk.ResponseType.NONE:
            dialog.stop_emission_by_name("response")
            return
        if response == Gtk.ResponseType.OK:
            dialog.stop_emission_by_name("response")
            if self._job is not None or self._pending_start is not None:
                return
            if not self._ensure_model_available():
                return
            self._apply_model = self._current_model_id()
            self._apply_params = self._current_params()
            self.progress.set_fraction(0.0)
            self.progress.set_text("Preparing full-resolution image…")
            self.status_label.set_text("Preparing full-resolution source; this may take a moment…")
            self.get_widget_for_response(Gtk.ResponseType.OK).set_sensitive(False)
            self.preview_btn.set_sensitive(False)
            # Let GTK paint the busy state before GIMP's synchronous export.
            self._pending_start = GLib.timeout_add(80, self._start_apply_idle)
            self._begin_activity()
        elif response in (Gtk.ResponseType.CANCEL, Gtk.ResponseType.DELETE_EVENT):
            self._save_current_settings()
            self._cancel_pending_start()
            if self._job is not None:
                job, self._job = self._job, None
                job.cancel()

    def _import_recovered_shadow(self, image, foreground_layer, shadow_image):
        """Add a separate, editable cast-shadow overlay under the cutout."""
        fd, path = tempfile.mkstemp(prefix="gimp-rembg-shadow-", suffix=".png")
        os.close(fd)
        try:
            shadow_image.save(path, format="PNG")
            new_layer = Gimp.file_load_layer(
                Gimp.RunMode.NONINTERACTIVE, image, Gio.File.new_for_path(path))
            if new_layer is None:
                raise RuntimeError("GIMP could not load recovered shadow overlay")
            new_layer.set_name(self.layer.get_name() + " (cast shadows)")
            insert_at = image.get_item_position(foreground_layer) + 1
            parent = foreground_layer.get_parent()
            if not image.insert_layer(new_layer, parent, insert_at):
                raise RuntimeError("GIMP could not insert shadow layer")
            return new_layer
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _apply_finished(self, result, error):
        self._job = None
        self.preview_btn.set_sensitive(True)
        if self._closed:
            return
        self.get_widget_for_response(Gtk.ResponseType.OK).set_sensitive(True)
        if error:
            self.status_label.set_text("Apply failed: " + str(error))
            Gimp.message("rembg: " + str(error))
            return
        image = self.image
        created_image = None
        try:
            alpha = (result.convert("L") if self._apply_params.get("only_mask")
                     else result.convert("RGBA").getchannel("A"))
            recovered_shadow = result.info.get("rembg_cast_shadow")
            new_image_mode = self.output_mode == "New image"
            if new_image_mode:
                image = Gimp.Image.new(self.layer.get_width(), self.layer.get_height(), Gimp.ImageBaseType.RGB)
                created_image = image
                target_layer = Gimp.Layer.new_from_drawable(self.layer, image)
                image.insert_layer(target_layer, None, 0)
                target_layer.set_offsets(0, 0)
            else:
                target_layer = self.layer
            image.undo_group_start()
            before_ids = {item.get_id() for item in image.get_layers()}
            try:
                foreground_layer = pil_mask_to_new_layer(
                    image, target_layer, alpha,
                    self.layer.get_name() + " (bg removed)")
                if recovered_shadow is not None:
                    # The source-colour subject and editable mask stay at
                    # their original precision. Shadow RGB/alpha live in a
                    # separate layer directly BELOW the cutout, so the cast
                    # shadow remains dark even on non-white backgrounds.
                    shadow_layer = self._import_recovered_shadow(
                        image, foreground_layer, recovered_shadow)
            except Exception:
                for item in image.get_layers():
                    if item.get_id() not in before_ids:
                        image.remove_layer(item)
                raise
            finally:
                image.undo_group_end()
            if new_image_mode:
                image.remove_layer(target_layer)
                if Gimp.Display.new(image) is None:
                    raise RuntimeError("Unable to open result image")
                created_image = None
            self._save_current_settings()
            Gimp.displays_flush()
            self._apply_result = True
            self.response(Gtk.ResponseType.ACCEPT)
        except Exception as exc:
            if created_image is not None:
                try:
                    created_image.delete()
                except Exception:
                    pass
            self.status_label.set_text("Mask creation failed: " + str(exc))
            Gimp.message("rembg: " + str(exc))

    def _start_preview_idle(self):
        self._pending_start = None
        if self._closed:
            return False
        try:
            self.status_label.set_text("Exporting selected layer for preview…")
            pil_src, _, _ = layer_to_pil(self.layer)
            preview_limit = {"Fast": 320, "Std": 512, "Fine": 768}.get(self.quality, 512)
            pil_src.thumbnail((preview_limit, preview_limit))
            self._pending_preview_before = pil_src.copy()
            self._job = InferenceJob(pil_src, self._current_model_id(),
                                     self._current_params(), self._preview_finished)
        except Exception as exc:
            self._job = None
            self._on_preview_ready(None, str(exc))
        return False

    def on_preview_clicked(self, btn):
        if self._job is not None or self._pending_start is not None:
            return
        if not self._ensure_model_available():
            return
        self.preview_btn.set_sensitive(False)
        self.get_widget_for_response(Gtk.ResponseType.OK).set_sensitive(False)
        self.progress.set_fraction(0.0)
        self.progress.set_text("Preparing preview…")
        self.status_label.set_text("Preparing source image…")
        self._pending_start = GLib.timeout_add(80, self._start_preview_idle)
        self._begin_activity()

    def _preview_finished(self, result, error):
        self._job = None
        self._on_preview_ready(result, str(error) if error else None)

    def _set_status(self, msg):
        if self._closed:
            return False
        self.status_label.set_text(msg)
        self.progress.pulse()
        return False

    def _on_preview_ready(self, result_pil, error):
        self.get_widget_for_response(Gtk.ResponseType.OK).set_sensitive(True)
        if self._closed:
            return False
        self.preview_btn.set_sensitive(True)
        self.progress.set_fraction(1.0 if not error else 0.0)
        if error:
            self._pending_preview_before = None
            self.progress.set_text("Failed")
            self.status_label.set_text("Preview failed")
            Gimp.message(f"rembg preview failed: {error}")
            return False
        self.progress.set_text("Preview ready")
        provider_str = " · CPU (isolated worker)"
        self.status_label.set_text(f"Preview ready{provider_str}")

        buf = io.BytesIO()
        preview_result = result_pil
        cast_shadow = result_pil.info.get("rembg_cast_shadow")
        if cast_shadow is not None and not self.chk_only_mask.get_active():
            from PIL import Image as PILImage
            preview_result = PILImage.alpha_composite(
                cast_shadow.convert("RGBA"), result_pil.convert("RGBA"))
        preview_result.save(buf, format="PNG")
        buf.seek(0)
        loader = GdkPixbuf.PixbufLoader()
        loader.write(buf.read())
        loader.close()
        pixbuf = loader.get_pixbuf()
        if self._pending_preview_before is not None:
            before_data = io.BytesIO()
            self._pending_preview_before.save(before_data, format="PNG")
            before_loader = GdkPixbuf.PixbufLoader()
            before_loader.write(before_data.getvalue())
            before_loader.close()
            self.preview_pixbuf_before = before_loader.get_pixbuf()
            self._pending_preview_before = None
        if (self.preview_pixbuf_before is not None and
            (pixbuf.get_width(), pixbuf.get_height()) !=
            (self.preview_pixbuf_before.get_width(), self.preview_pixbuf_before.get_height())):
            self.status_label.set_text("Preview dimensions differ; result not displayed")
            return False
        self.preview_pixbuf_after = pixbuf
        self.preview_canvas.queue_draw()
        self._preview_result_pil = result_pil
        return False


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

class RembgPlugin(Gimp.PlugIn):
    def do_set_i18n(self, name):
        return False

    def do_query_procedures(self):
        return ["plug-in-rembg-gimp"]

    def do_create_procedure(self, name):
        procedure = Gimp.ImageProcedure.new(
            self, name, Gimp.PDBProcType.PLUGIN, self.run, None)
        procedure.set_image_types("*")
        procedure.set_menu_label("Remove Background...")
        procedure.add_menu_path("<Image>/Filters/Distorts")
        procedure.set_documentation(
            "Remove background using rembg (U2-Net / IS-Net / BiRefNet / "
            "automatic segmentation models)",
            "Runs the selected image/layer through rembg and inserts the "
            "result as a new layer with an alpha mask.",
            name)
        procedure.set_attribution("Zarir Madon", "Zarir Madon", "2026")
        return procedure

    def run(self, procedure, run_mode, image, drawables, config, run_data):
        if run_mode != Gimp.RunMode.INTERACTIVE:
            return procedure.new_return_values(
                Gimp.PDBStatusType.CALLING_ERROR, GLib.Error())

        GimpUi.init("rembg-gimp")
        layer = resolve_raster_layer(image, drawables)
        if layer is None:
            Gimp.message("Select one raster layer or its attached layer mask before running Remove Background.")
            return procedure.new_return_values(
                Gimp.PDBStatusType.CALLING_ERROR, GLib.Error())

        dialog = RembgDialog(image, layer)
        response = dialog.run()
        success = dialog._apply_result and response == Gtk.ResponseType.ACCEPT
        if dialog._job is not None:
            dialog._job.cancel()
        dialog.destroy()
        return procedure.new_return_values(
            Gimp.PDBStatusType.SUCCESS if success else Gimp.PDBStatusType.CANCEL,
            GLib.Error())


Gimp.main(RembgPlugin.__gtype__, sys.argv)
