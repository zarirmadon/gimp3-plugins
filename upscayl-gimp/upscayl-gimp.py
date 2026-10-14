#!/usr/bin/env python3
"""
Upscayl for GIMP — GIMP 3.2 Python plug-in
Runs the Upscayl CLI binary (upscayl-bin) against the active layer and
imports the result as a new layer.

Copyright (C) 2026 Zarir Madon

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

Install: copy this whole folder to your GIMP 3.0 plug-ins directory, e.g.
  ~/.config/GIMP/3.2/plug-ins/upscayl-gimp/upscayl-gimp.py
Make the .py executable: chmod +x upscayl-gimp.py
Restart GIMP. Filters > Enhance > Upscayl...
"""

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback

import gi
gi.require_version('Gimp', '3.0')
gi.require_version('GimpUi', '3.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gimp, GimpUi, Gtk, GLib, Gio

PLUGIN_ID = "plug-in-upscayl"
PLUGIN_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Install detection
# ---------------------------------------------------------------------------
# Each entry: (label, binary_glob_candidates, models_dir_candidates)
# Paths are checked in order; first binary that exists+is executable wins
# per source. All found sources are offered; the best is preselected.

FLATPAK_APP_ID = "org.upscayl.Upscayl"

# Curated, known-compatible model sources — real .param/.bin ncnn-vulkan
# pairs that this plugin's CLI backend can actually load. Not every model
# on the internet is compatible (many "AI upscaler" models are PyTorch/
# ONNX and need conversion first, out of scope here); these are sources
# specifically confirmed to host the right format. Verified 2026-09.
MODEL_SOURCE_LINKS = [
    (
        "Upscayl custom-models repo",
        "https://github.com/upscayl/custom-models",
        "Upscayl's own official extra-models repo — includes 2x/3x-native "
        "models (the bundled defaults are 4x-only), e.g. realesr-animevideov3-x2.",
    ),
    (
        "Real-ESRGAN-ncnn-vulkan additional models",
        "https://github.com/TransparentLC/realesrgan-gui/releases/tag/additional-models",
        "A curated list built for the same CLI this plugin uses. Note: a few "
        "of these need a patched binary due to disabled ncnn operators — "
        "read each model's notes before relying on it.",
    ),
    (
        "Upscale Wiki Model Database (mirror)",
        "https://huggingface.co/tumuyan2/realsr-models",
        "The largest general catalog of ncnn-compatible super-resolution "
        "models, mirrored on Hugging Face — includes specialty models "
        "(anime, text, book-scan, sharpen-only) beyond Upscayl's defaults.",
    ),
]


def _expand(paths):
    return [os.path.expanduser(os.path.expandvars(p)) for p in paths]


def candidate_sources():
    """Return a list of dicts describing every Upscayl install we can find.

    Each dict: {source, binary, models_dir, extra_model_dirs}
    'source' is one of: system, flatpak, appimage(=apk on some distros'
    slang, but really AppImage/zip-extracted), snap.
    """
    found = []

    # --- system package (deb/rpm/AUR upscayl-bin, or 'upscayl-cli'/'upscayl-ncnn' symlink) ---
    # Confirmed directly from a real Arch/paru install of AUR upscayl-bin
    # 2.15.0-10 (pacman -Ql upscayl-bin): CLI command on PATH is
    # `upscayl-ncnn`, wrapping the actual binary at
    # /usr/share/upscayl/bin/upscayl-bin, with models at
    # /usr/share/upscayl/models. Prior AUR comments/ebuilds referenced
    # upscayl-cli and /opt/Upscayl paths — the packaging has since changed,
    # so all of these are kept as candidates rather than replaced.
    system_bin_names = ["upscayl-ncnn", "upscayl-cli", "upscayl-bin", "upscayl-realesrgan"]
    system_bin_dirs = _expand([
        "/usr/bin", "/usr/local/bin", "/usr/share/upscayl/bin",
        "/opt/Upscayl/resources/bin", "/opt/upscayl/resources/bin",
    ])
    system_model_dirs = _expand([
        "/usr/share/upscayl/models", "/usr/bin/models",
        "/opt/Upscayl/resources/models", "/opt/upscayl/resources/models",
    ])
    for d in system_bin_dirs:
        for name in system_bin_names:
            p = os.path.join(d, name)
            if os.path.isfile(p) and os.access(p, os.X_OK):
                models = next((m for m in system_model_dirs if os.path.isdir(m)), None)
                found.append({
                    "source": "system",
                    "label": f"System install ({p})",
                    "binary": p,
                    "models_dir": models,
                })
                break

    # Also trust a PATH lookup, in case it's installed somewhere non-standard.
    for name in system_bin_names:
        p = shutil.which(name)
        if p and not any(f["binary"] == p for f in found):
            models = next((m for m in system_model_dirs if os.path.isdir(m)), None)
            found.append({
                "source": "system",
                "label": f"System install (PATH: {p})",
                "binary": p,
                "models_dir": models,
            })

    # --- Flatpak ---
    flatpak_roots = _expand([
        "/var/lib/flatpak/app/" + FLATPAK_APP_ID,
        "~/.local/share/flatpak/app/" + FLATPAK_APP_ID,
    ])
    for root in flatpak_roots:
        if not os.path.isdir(root):
            continue
        # Layout: <root>/<arch>/<branch>/active/files/...
        active_links = glob.glob(os.path.join(root, "*", "*", "active"))
        for active in active_links:
            files_dir = os.path.join(active, "files")
            bin_candidates = glob.glob(os.path.join(files_dir, "**", "upscayl-bin"), recursive=True) \
                + glob.glob(os.path.join(files_dir, "**", "upscayl-cli"), recursive=True)
            if not bin_candidates:
                continue
            b = bin_candidates[0]
            model_candidates = glob.glob(os.path.join(files_dir, "**", "models"), recursive=True)

            # `flatpak run --command=NAME` execs NAME as found on the
            # SANDBOX's own PATH (typically /app/bin inside the sandbox) —
            # it is NOT the basename of wherever we happened to find the
            # file when scanning the host filesystem, and there is no
            # guarantee those two names match. The authoritative source
            # for the real in-sandbox command is the app's own metadata
            # file (always present for an installed Flatpak app), which
            # has a plain `command=NAME` line under [Application].
            run_command = None
            metadata_path = os.path.join(active, "metadata")
            if os.path.isfile(metadata_path):
                try:
                    with open(metadata_path, "r") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("command="):
                                run_command = line.split("=", 1)[1].strip()
                                break
                except Exception:
                    pass
            if not run_command:
                # Fall back to the basename we found, but flag it as a
                # guess — this may still fail with the same bwrap error
                # if the sandbox's PATH name differs, and the person
                # should be told why rather than hit a silent failure.
                run_command = os.path.basename(b)

            found.append({
                "source": "flatpak",
                "label": f"Flatpak ({FLATPAK_APP_ID})",
                "binary": b,
                "models_dir": model_candidates[0] if model_candidates else None,
                # Flatpak apps must be launched via `flatpak run`, not the
                # raw binary path (sandboxed), unless we can bind-mount.
                "flatpak_run": True,
                "flatpak_run_command": run_command,
            })

    # --- AppImage / manually extracted zip ("apk" in the user's shorthand —
    #     Linux doesn't have .apk packages; this covers portable installs) ---
    portable_dirs = _expand([
        "~/Applications", "~/.local/opt/Upscayl", "~/upscayl", "~/Downloads",
    ])
    for d in portable_dirs:
        if not os.path.isdir(d):
            continue
        appimages = glob.glob(os.path.join(d, "*[Uu]pscayl*.AppImage"))
        for ai in appimages:
            if os.access(ai, os.X_OK):
                found.append({
                    "source": "appimage",
                    "label": f"AppImage ({os.path.basename(ai)})",
                    "binary": ai,
                    "models_dir": None,  # AppImages self-extract; user must
                                          # point to a models dir manually.
                    "is_appimage": True,
                })

    # --- Snap ---
    snap_bin = shutil.which("upscayl")
    if snap_bin and "/snap/" in snap_bin:
        found.append({
            "source": "snap",
            "label": f"Snap ({snap_bin})",
            "binary": snap_bin,
            "models_dir": None,
        })

    return found


def list_models(models_dir):
    """Return sorted unique model base-names found in a directory.
    Upscayl models are NCNN pairs: <name>.param + <name>.bin
    """
    if not models_dir or not os.path.isdir(models_dir):
        return []
    names = set()
    for f in os.listdir(models_dir):
        base, ext = os.path.splitext(f)
        if ext == ".param":
            if os.path.isfile(os.path.join(models_dir, base + ".bin")):
                names.add(base)
    return sorted(names)


# ---------------------------------------------------------------------------
# Settings persistence (so the dialog remembers your last picks)
# ---------------------------------------------------------------------------

def config_path():
    conf_dir = os.path.join(GLib.get_user_config_dir(), "upscayl-gimp")
    os.makedirs(conf_dir, exist_ok=True)
    return os.path.join(conf_dir, "settings.txt")


def load_settings():
    path = config_path()
    data = {}
    if os.path.isfile(path):
        with open(path, "r") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    data[k] = v
    return data


def save_settings(d):
    with open(config_path(), "w") as f:
        for k, v in d.items():
            f.write(f"{k}={v}\n")


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class UpscaylDialog(Gtk.Dialog):
    def __init__(self, image, drawable, parent_title="Upscayl"):
        super().__init__(title="Upscayl", modal=True, use_header_bar=False)
        self.image = image
        self.drawable = drawable
        self.set_default_size(560, -1)
        self.sources = candidate_sources()
        self.settings = load_settings()
        self.result = None

        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)

        # Bundled models, separator, then alphabetically sorted custom models.
        model_row = self._labeled_row("Model:", "Choose a bundled Upscayl model or a custom NCNN model. Custom models appear below the separator.")
        self.model_combo = Gtk.ComboBoxText()
        self.model_combo.set_tooltip_text("Bundled models first; custom models alphabetically below the separator.")
        model_row.pack_start(self.model_combo, True, True, 0)
        box.pack_start(model_row, False, False, 0)

        # Four direct scale choices matching the approved mockup.
        scale_row = self._labeled_row("Scale:", "1× enhancement, 2×, 4×, or two consecutive 4× passes (16×).")
        self.scale_buttons = {}
        saved_scale = "Double Upscale" if self.settings.get("double_upscale") == "1" else self.settings.get("scale", "4x")
        group = None
        for name, caption in (("1x", "Enhance"), ("2x", "Double"), ("4x", "Quadruple"), ("Double Upscale", "16× · Double")):
            btn = Gtk.RadioButton.new_with_label_from_widget(group, caption)
            if group is None: group = btn
            btn.set_tooltip_text("Run at " + ("16× using two 4× passes" if name == "Double Upscale" else name + " final scale"))
            btn.connect("toggled", self._on_scale_changed)
            self.scale_buttons[name] = btn
            scale_row.pack_start(btn, False, False, 5)
        self.scale_buttons[saved_scale if saved_scale in self.scale_buttons else "4x"].set_active(True)
        box.pack_start(scale_row, False, False, 0)

        # Input and output boxes are identical in structure and width.
        dim_title = Gtk.Label(label="DIMENSIONS", xalign=0)
        dim_title.set_tooltip_text("Input layer pixels and final output pixels. Output is editable only with Custom output size enabled.")
        box.pack_start(dim_title, False, False, 0)
        dims = Gtk.Grid(column_spacing=8, row_spacing=6)
        dims.set_column_homogeneous(False)
        box.pack_start(dims, False, False, 0)

        def dim_panel(title, w, h, editable):
            frame = Gtk.Frame()
            frame.set_hexpand(True)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            inner.set_border_width(12)
            frame.add(inner)
            label = Gtk.Label(label=title, xalign=0)
            inner.pack_start(label, False, False, 0)
            fields = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            we, he = Gtk.Entry(), Gtk.Entry()
            for entry, value in ((we, w), (he, h)):
                entry.set_text(str(value)); entry.set_width_chars(7)
                entry.set_max_length(7)
                entry.set_hexpand(True)
                entry.set_sensitive(editable)
                fields.pack_start(entry, True, True, 0)
                if entry is we: fields.pack_start(Gtk.Label(label="×"), False, False, 0)
            inner.pack_start(fields, False, False, 0)
            meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            ppi = Gtk.Label(label="PPI", xalign=0)
            ratio = Gtk.Label(label="3:2", xalign=1)
            meta.pack_start(ppi, True, True, 0)
            meta.pack_start(ratio, False, False, 0)
            inner.pack_start(meta, False, False, 0)
            return frame, we, he, ppi, ratio

        iw, ih = drawable.get_width(), drawable.get_height()
        left, self.input_width, self.input_height, self.input_ppi, self.input_ratio = dim_panel("INPUT (LAYER)", iw, ih, False)
        right, self.width_entry, self.height_entry, self.ppi_label, self.output_ratio = dim_panel("OUTPUT", iw * 4, ih * 4, False)
        dims.attach(left, 0, 0, 1, 1)
        self.chain_button = Gtk.ToggleButton(label="🔗")
        self.chain_button.set_active(self.settings.get("chain", "1") != "0")
        self.chain_button.set_tooltip_text("Linked: preserve the input aspect ratio when editing output width or height. Unlinked: edit both dimensions independently.")
        self.chain_button.connect("toggled", self._on_chain_toggled)
        dims.attach(self.chain_button, 1, 0, 1, 1)
        dims.attach(right, 2, 0, 1, 1)
        self.custom_output_check = Gtk.CheckButton(label="Custom output size")
        self.custom_output_check.set_tooltip_text("Enable manual final output dimensions; without it, output is calculated from the selected scale.")
        self.custom_output_check.set_active(self.settings.get("dimension_mode", "current") == "custom")
        self.custom_output_check.connect("toggled", self._on_custom_output_toggled)
        # Checkbox below/outside the output frame, left-aligned with that frame.
        dims.attach(self.custom_output_check, 2, 1, 1, 1)
        self.width_entry.connect("changed", lambda w: self._on_output_edited("width"))
        self.height_entry.connect("changed", lambda w: self._on_output_edited("height"))
        self._dimension_guard = False
        self._custom_values = (
            self.settings.get("custom_width", str(iw * 4)),
            self.settings.get("custom_height", str(ih * 4)),
        )
        self.width_entry.set_tooltip_text("Final output width in pixels. With the chain linked, height follows the input aspect ratio.")
        self.height_entry.set_tooltip_text("Final output height in pixels. With the chain linked, width follows the input aspect ratio.")

        output_row = self._labeled_row("Place result:", "New layer expands the existing image canvas; New image opens the result separately.")
        self.output_layer_radio = Gtk.RadioButton.new_with_label_from_widget(None, "New layer")
        self.output_image_radio = Gtk.RadioButton.new_with_label_from_widget(self.output_layer_radio, "New image")
        self.output_layer_radio.set_tooltip_text("Add a new layer and expand the existing canvas to fit the output.")
        self.output_image_radio.set_tooltip_text("Open the upscale in a separate GIMP image.")
        self.output_image_radio.set_active(self.settings.get("output", "layer") == "image")
        output_row.pack_start(self.output_layer_radio, False, False, 0)
        output_row.pack_start(self.output_image_radio, False, False, 12)
        box.pack_start(output_row, False, False, 0)

        # Keep advanced settings below the main image controls.
        advanced = Gtk.Expander(label="Advanced options")
        advbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        advbox.set_border_width(8)
        advanced.add(advbox)
        box.pack_start(advanced, False, False, 0)
        custom_row = self._labeled_row("Custom models:", "Select a folder containing matching .param and .bin files.")
        self._model_folder = self.settings.get("custom_model_dir", "")
        self.modeldir_chooser = Gtk.Button(label="Choose custom models folder…")
        self.modeldir_chooser.set_tooltip_text("Open a folder chooser below the dialog rather than a popup extending above the screen.")
        self.modeldir_chooser.connect("clicked", self._choose_model_folder)
        custom_row.pack_start(self.modeldir_chooser, True, True, 0)
        browse_btn = Gtk.MenuButton(label="More models…")
        browse_menu = Gtk.Menu()
        for label, url, note in MODEL_SOURCE_LINKS:
            item = Gtk.MenuItem(label=label)
            item.set_tooltip_text(note)
            item.connect("activate", self._on_open_model_source, url)
            browse_menu.append(item)
        browse_menu.show_all(); browse_btn.set_popup(browse_menu)
        custom_row.pack_start(browse_btn, False, False, 0)
        advbox.pack_start(custom_row, False, False, 0)

        tile_row = self._labeled_row("Tile size:", "NCNN tile size. 0 = automatic.")
        self.tile_entry = Gtk.Entry(); self.tile_entry.set_width_chars(8)
        self.tile_entry.set_text(self.settings.get("tile_size", "0")); self.tile_entry.set_placeholder_text("0 = auto")
        tile_row.pack_start(self.tile_entry, False, False, 0); advbox.pack_start(tile_row, False, False, 0)

        threads_row = self._labeled_row("Threads:", "NCNN load:process:save thread counts. Blank uses the backend default.")
        self.threads_entry = Gtk.Entry(); self.threads_entry.set_width_chars(12)
        self.threads_entry.set_text(self.settings.get("threads", "")); self.threads_entry.set_placeholder_text("default (1:2:2)")
        threads_row.pack_start(self.threads_entry, False, False, 0); advbox.pack_start(threads_row, False, False, 0)

        tta_row = self._labeled_row("TTA:", "Test-Time Augmentation. Higher processing cost; off by default.")
        self.tta_check = Gtk.CheckButton(label="Enable")
        self.tta_check.set_active(self.settings.get("tta", "0") == "1")
        tta_row.pack_start(self.tta_check, False, False, 0); advbox.pack_start(tta_row, False, False, 0)

        # Diagnostics is deliberately the final settings row.
        diag_row = self._labeled_row("Diagnostics:", "Re-scan the Upscayl installation and model availability.")
        diag_btn = Gtk.Button.new_with_label("Check Installation…")
        diag_btn.connect("clicked", self._on_check_installation)
        self.diag_status = Gtk.Label(xalign=0)
        diag_row.pack_start(diag_btn, False, False, 0)
        diag_row.pack_start(self.diag_status, True, True, 8)
        box.pack_start(diag_row, False, False, 0)

        box.show_all()
        self.add_button("Reset", 1001)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok_btn = self.add_button("Upscayl", Gtk.ResponseType.OK)
        ok_btn.get_style_context().add_class("suggested-action")

        self._refresh_models()
        self._set_ppi_label()
        self._on_chain_toggled(self.chain_button)
        self._on_custom_output_toggled(self.custom_output_check)
        self._refresh_dimensions()

    def _labeled_row(self, text, tooltip):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=text, xalign=0)
        label.set_size_request(125, -1)
        label.set_tooltip_text(tooltip)
        row.pack_start(label, False, False, 0)
        return row

    @staticmethod
    def _numbers_from_gi(value):
        if isinstance(value, (tuple, list)):
            vals = [v for v in value if isinstance(v, (int, float)) and not isinstance(v, bool)]
            return vals[-2:] if len(vals) >= 2 else vals
        return []

    def _set_ppi_label(self):
        try:
            vals = self._numbers_from_gi(self.image.get_resolution())
            ppi = f"{vals[0]:.0f} PPI" if len(vals) == 2 else "PPI unavailable"
        except Exception:
            ppi = "PPI unavailable"
        self.ppi_label.set_text(ppi)
        self.input_ppi.set_text(ppi)

    def _on_scale_changed(self, button):
        if not button.get_active() or not hasattr(self, "width_entry"):
            return
        if self.custom_output_check.get_active():
            w, h = self._base_dimensions()
            self._dimension_guard = True
            self.width_entry.set_text(str(w * self._scale_factor()))
            self.height_entry.set_text(str(h * self._scale_factor()))
            self._dimension_guard = False
            self._on_output_edited("width")
        else:
            self._refresh_dimensions()

    def _scale_factor(self):
        for name, btn in self.scale_buttons.items():
            if btn.get_active():
                return {"1x": 1, "2x": 2, "4x": 4, "Double Upscale": 16}[name]
        return 4

    def _scale_label(self):
        return next((name for name, btn in self.scale_buttons.items() if btn.get_active()), "4x")

    def _on_chain_toggled(self, widget):
        widget.set_label("🔗" if widget.get_active() else "⛓")
        if widget.get_active() and self.custom_output_check.get_active():
            self._on_output_edited("width")

    def _on_custom_output_toggled(self, widget):
        enabled = widget.get_active()
        self.width_entry.set_sensitive(enabled)
        self.height_entry.set_sensitive(enabled)
        if enabled:
            self._dimension_guard = True
            base_w, base_h = self._base_dimensions()
            factor = self._scale_factor()
            self.width_entry.set_text(str(base_w * factor))
            self.height_entry.set_text(str(base_h * factor))
            self._custom_values = (str(base_w * factor), str(base_h * factor))
            self._dimension_guard = False
            if self.chain_button.get_active(): self._on_output_edited("width")
        else:
            self._refresh_dimensions()

    def _on_output_edited(self, which):
        if self._dimension_guard or not self.custom_output_check.get_active(): return
        self._dimension_guard = True
        try:
            w = int(self.width_entry.get_text()) if self.width_entry.get_text().strip() else 0
            h = int(self.height_entry.get_text()) if self.height_entry.get_text().strip() else 0
            iw, ih = self.drawable.get_width(), self.drawable.get_height()
            if self.chain_button.get_active():
                if which == "width" and w > 0:
                    h = max(1, round(w * ih / iw))
                    self.height_entry.set_text(str(h))
                elif which == "height" and h > 0:
                    w = max(1, round(h * iw / ih))
                    self.width_entry.set_text(str(w))
            if w > 0 and h > 0:
                self._custom_values = (str(w), str(h))
                self.output_ratio.set_text(f"{w / h:.2f}:1")
            else:
                self.output_ratio.set_text("—")
        except (ValueError, ZeroDivisionError):
            pass
        finally:
            self._dimension_guard = False

    def _base_dimensions(self):
        return self.drawable.get_width(), self.drawable.get_height()

    def _final_dimensions(self):
        if self.custom_output_check.get_active():
            try:
                w, h = int(self.width_entry.get_text()), int(self.height_entry.get_text())
                if not (1 <= w <= 200000 and 1 <= h <= 200000): raise ValueError()
                return w, h
            except ValueError:
                raise ValueError("Custom output width and height must be positive pixel values (up to 200000).")
        w, h = self._base_dimensions()
        return w * self._scale_factor(), h * self._scale_factor()

    def _refresh_dimensions(self):
        if not hasattr(self, "width_entry") or not hasattr(self, "_dimension_guard"): return
        if not self.custom_output_check.get_active():
            w, h = self._base_dimensions()
            factor = self._scale_factor()
            self._dimension_guard = True
            self.width_entry.set_text(str(w * factor))
            self.height_entry.set_text(str(h * factor))
            self._dimension_guard = False
            self.output_ratio.set_text(f"{w / h:.2f}:1")
        w, h = self._base_dimensions()
        self.input_ratio.set_text(f"{w / h:.2f}:1")

    def _current_source(self):
        return self.sources[0] if self.sources else None

    def _on_open_model_source(self, widget, url):
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except Exception as e:
            Gimp.message(f"Couldn't open the browser automatically. Visit:\n{url}\n\n({e})")

    def _choose_model_folder(self, _button):
        chooser = Gtk.FileChooserDialog(
            title="Choose custom models folder",
            transient_for=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        chooser.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Select folder", Gtk.ResponseType.OK)
        chooser.set_default_size(560, 380)
        if self._model_folder and os.path.isdir(self._model_folder):
            chooser.set_current_folder(self._model_folder)
        try:
            if chooser.run() == Gtk.ResponseType.OK:
                self._model_folder = chooser.get_filename() or ""
                self.modeldir_chooser.set_label(self._model_folder or "Choose custom models folder…")
                self._refresh_models()
        finally:
            chooser.destroy()

    def _refresh_models(self):
        previous = self.settings.get("model", "")
        if hasattr(self, "_model_entries") and self.model_combo.get_active() >= 0:
            idx = self.model_combo.get_active()
            if idx < len(self._model_entries) and self._model_entries[idx] is not None:
                previous = self._model_entries[idx][0]
        self.model_combo.remove_all()
        src = self._current_source()
        bundled = sorted(list_models(src.get("models_dir")) if src else [], key=str.casefold)
        custom_dir = self._model_folder
        custom = sorted(list_models(custom_dir), key=str.casefold)
        entries = [(m, src["models_dir"]) for m in bundled]
        if custom: entries.append(None)
        entries.extend((m, custom_dir) for m in custom)
        self._model_entries = entries
        if not entries:
            self.model_combo.append_text("No models found")
            self.model_combo.set_active(0)
            self.model_combo.set_sensitive(False)
            return
        self.model_combo.set_sensitive(True)
        for entry in entries:
            self.model_combo.append_text("──────── Custom models ────────" if entry is None else entry[0])
        idx = next((i for i, entry in enumerate(entries) if entry is not None and entry[0] == previous), 0)
        self.model_combo.set_active(idx)
        self.model_combo.connect("changed", self._on_model_changed) if not getattr(self, "_model_signal_connected", False) else None
        self._model_signal_connected = True

    def _on_model_changed(self, combo):
        idx = combo.get_active()
        if idx < 0 or idx >= len(self._model_entries): return
        if self._model_entries[idx] is None:
            for i, entry in enumerate(self._model_entries):
                if entry is not None and i > idx:
                    combo.set_active(i)
                    return

    def _on_check_installation(self, widget):
        # Comprehensive user-invoked health check. Run the external launch
        # probe off the GTK thread so a broken executable cannot freeze GIMP.
        widget.set_sensitive(False)
        self.diag_status.set_text("Checking executable…")
        custom_dir = self._model_folder

        def finish(markup):
            self.diag_status.set_markup(markup)
            widget.set_sensitive(True)
            return False

        def diagnostic_worker():
            try:
                sources = candidate_sources()
                if not sources:
                    raise RuntimeError("Upscayl / NCNN executable was not found")
                src = sources[0]
                GLib.idle_add(self.diag_status.set_text, "Launching executable test…")
                _probe_executable(src, timeout=8.0)
                GLib.idle_add(self.diag_status.set_text, "Checking model pairs…")
                bundled = len(list_models(src.get("models_dir"))) if src.get("models_dir") else 0
                custom = len(list_models(custom_dir)) if custom_dir else 0
                if bundled + custom < 1:
                    raise RuntimeError("No complete .param + .bin model pair was found")
                self.sources = sources
                GLib.idle_add(self._refresh_models)
                label = GLib.markup_escape_text(src["label"])
                GLib.idle_add(finish, f'<span foreground="#398b3f">Working</span> — {label} · {bundled + custom} model(s)')
            except Exception as exc:
                msg = GLib.markup_escape_text(str(exc))
                GLib.idle_add(finish, f'<span foreground="#c33">Needs attention</span> — {msg}')

        threading.Thread(target=diagnostic_worker, daemon=True).start()

    def reset_controls(self):
        self.scale_buttons["4x"].set_active(True)
        self.custom_output_check.set_active(False)
        self.chain_button.set_active(True)
        self.output_layer_radio.set_active(True)
        self.tile_entry.set_text("0")
        self.threads_entry.set_text("")
        self.tta_check.set_active(False)
        self._refresh_dimensions()

    def get_choice(self):
        src = self._current_source()
        if not src or not self._model_entries or not self.model_combo.get_sensitive():
            return None
        model_idx = self.model_combo.get_active()
        if model_idx < 0 or self._model_entries[model_idx] is None: return None
        model_name, model_dir = self._model_entries[model_idx]
        scale_label = self._scale_label()
        base_w, base_h = self._final_dimensions()
        return {
            "source": src,
            "scale_label": scale_label,
            "double_upscale": scale_label == "Double Upscale",
            "model_name": model_name,
            "model_dir": model_dir,
            "out_format": "png",
            "gpu_id": "",
            "tile_size": self.tile_entry.get_text().strip(),
            "threads": self.threads_entry.get_text().strip(),
            "tta": self.tta_check.get_active(),
            "custom_model_dir": self._model_folder or "",
            "output": "image" if self.output_image_radio.get_active() else "layer",
            "dimension_mode": "custom" if self.custom_output_check.get_active() else "current",
            "chain": self.chain_button.get_active(),
            "base_width": base_w,
            "base_height": base_h,
        }


# ---------------------------------------------------------------------------
# Running the CLI
# ---------------------------------------------------------------------------

def build_command(src, in_path, out_path, scale_pass, model_name, model_dir,
                   gpu_id, tile_size, threads, tta):
    if src.get("flatpak_run"):
        # Use the app's own metadata-declared command name (resolved in
        # candidate_sources()), not a guessed basename — `flatpak run
        # --command=NAME` execs NAME as found on the SANDBOX's PATH,
        # which is not guaranteed to match the filename found by
        # scanning the host filesystem.
        run_cmd = src.get("flatpak_run_command") or os.path.basename(src["binary"])
        # By default a Flatpak sandbox has NO access to host files outside
        # the app itself and ~/.var/app/$FLATPAK_ID — /tmp is NOT shared
        # automatically. --filesystem=PATH grants access for just this
        # invocation (not a permanent override), scoped to the actual
        # temp directory this run uses rather than a blanket /tmp grant.
        tmp_scope = os.path.dirname(in_path)
        cmd = [
            "flatpak", "run",
            f"--filesystem={tmp_scope}",
            "--command=" + run_cmd, FLATPAK_APP_ID,
        ]
    else:
        cmd = [src["binary"]]
    cmd += ["-i", in_path, "-o", out_path, "-s", str(scale_pass), "-n", model_name]
    if model_dir:
        cmd += ["-m", model_dir]
    if gpu_id:
        cmd += ["-g", gpu_id]
    if tile_size and tile_size != "0":
        cmd += ["-t", tile_size]
    if threads:
        cmd += ["-j", threads]
    if tta:
        cmd += ["-x"]
    return cmd


_PERCENT_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*%\s*$")


def _probe_executable(src, should_cancel=None, timeout=8.0):
    """Functionally launch the selected backend with a bounded timeout."""
    if not src or not src.get("binary"):
        raise RuntimeError("no Upscayl/NCNN executable was detected")
    if src.get("flatpak_run"):
        run_cmd = src.get("flatpak_run_command") or os.path.basename(src["binary"])
        cmd = ["flatpak", "run", "--command=" + run_cmd, FLATPAK_APP_ID, "--help"]
    else:
        cmd = [src["binary"], "--help"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        raise RuntimeError(f"could not launch Upscayl / NCNN: {exc}") from exc
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None:
            if should_cancel and should_cancel():
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=1.0)
                raise RuntimeError("cancelled")
            if time.monotonic() >= deadline:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=1.0)
                raise RuntimeError(f"Upscayl / NCNN launch test timed out after {timeout:.0f} seconds")
            time.sleep(0.05)
    finally:
        if proc.poll() is None:
            proc.kill(); proc.wait()
    # Some builds return non-zero for --help despite launching correctly.
    # Reaching a normal process exit is the functional launch check.
    return True


def preflight_dependencies(src, model_name, model_dir, tmpdir, on_status=None):
    def status(msg):
        if on_status:
            on_status(msg)
        try:
            Gimp.progress_set_text(msg)
        except Exception:
            pass

    status("Checking Upscayl executable…")
    binary = src.get("binary") if src else None
    if not binary:
        raise RuntimeError("Dependency check failed: no Upscayl/NCNN executable was detected.")
    if not src.get("flatpak_run") and (not os.path.isfile(binary) or not os.access(binary, os.X_OK)):
        raise RuntimeError(f"Dependency check failed: Upscayl executable is missing or not executable: {binary}")

    status("Checking model files…")
    if not model_dir or not os.path.isdir(model_dir):
        raise RuntimeError(f"Dependency check failed: model folder is unavailable: {model_dir or '(none)'}")
    param = os.path.join(model_dir, model_name + ".param")
    weights = os.path.join(model_dir, model_name + ".bin")
    missing = [x for x in (param, weights) if not os.path.isfile(x)]
    if missing:
        raise RuntimeError("Dependency check failed: incomplete NCNN model pair: " + ", ".join(missing))

    status("Checking temporary workspace…")
    probe = os.path.join(tmpdir, ".write-test")
    try:
        with open(probe, "wb") as f:
            f.write(b"ok")
        os.remove(probe)
    except Exception as exc:
        raise RuntimeError(f"Dependency check failed: temporary workspace is not writable: {exc}") from exc

    status("Dependencies ready")


def run_upscayl_pass(src, in_path, out_path, scale_pass, model_name, model_dir,
                      gpu_id, tile_size, threads, tta, on_progress=None,
                      should_cancel=None, on_status=None):
    """Runs one Upscayl pass, streaming stdout so on_progress(pct) can be
    called live as each 'NN.NN%' line arrives (upscayl-bin prints one such
    line per tile as it works). on_progress receives a float 0-100.
    should_cancel() is polled between lines; if it returns True the process
    is terminated and a RuntimeError('cancelled') is raised."""
    cmd = build_command(src, in_path, out_path, scale_pass, model_name, model_dir,
                         gpu_id, tile_size, threads, tta)
    if on_status:
        on_status("Launching Upscayl / NCNN…")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, universal_newlines=True,
    )
    if on_status:
        on_status("Waiting for NCNN/Vulkan initialization…")
    stderr_tail = []
    cancel_watch_stop = threading.Event()

    # Cancellation must not depend on NCNN printing another progress line.
    # A tiny watcher observes only the in-memory cancel flag and terminates
    # this child process; it never touches GTK/GIMP objects.
    def _cancel_watch():
        while not cancel_watch_stop.wait(0.10):
            try:
                if should_cancel and should_cancel() and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=3)
                    return
            except Exception:
                return

    cancel_watch = threading.Thread(target=_cancel_watch, daemon=True)
    cancel_watch.start()
    try:
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if line:
                stderr_tail.append(line)
                if len(stderr_tail) > 40:
                    stderr_tail.pop(0)
                m = _PERCENT_RE.match(line)
                if m and on_progress:
                    try:
                        pct = float(m.group(1))
                        if on_status:
                            on_status(f"Processing with NCNN… {pct:.0f}%")
                        on_progress(pct)
                    except Exception:
                        pass
            if should_cancel and should_cancel():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise RuntimeError("cancelled")
    finally:
        cancel_watch_stop.set()
        if proc.poll() is None:
            proc.wait()
        cancel_watch.join(timeout=0.5)

    if should_cancel and should_cancel():
        raise RuntimeError("cancelled")
    if proc.returncode != 0:
        raise RuntimeError(
            f"Upscayl exited with code {proc.returncode}.\n"
            f"Command: {' '.join(cmd)}\n"
            f"output:\n" + "\n".join(stderr_tail[-25:])
        )
    if on_status:
        on_status("Verifying Upscayl output…")
    if not os.path.isfile(out_path):
        raise RuntimeError(
            f"Upscayl reported success but produced no output file.\n"
            f"Command: {' '.join(cmd)}"
        )


# ---------------------------------------------------------------------------
# Progress dialog — deliberately minimal
# ---------------------------------------------------------------------------

class ProgressDialog(Gtk.Dialog):
    """Responsive progress UI. NCNN runs in the existing worker thread.
    No hardware polling or diagnostic subprocesses are started here.
    """
    def __init__(self, total_passes, gpu_id=None):
        super().__init__(title="Upscayl — working…", modal=True, use_header_bar=False)
        self.set_default_size(420, -1)
        self.set_deletable(False)
        self.total_passes = max(1, int(total_passes))
        self.current_pass = 1
        self.cancelled = False

        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(14)
        self.status_label = Gtk.Label(xalign=0)
        self.status_label.set_markup("<b>Starting…</b>")
        box.pack_start(self.status_label, False, False, 0)
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.progress_bar.set_text("0%")
        box.pack_start(self.progress_bar, False, False, 0)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.connect("response", self._on_response)
        box.show_all()

    def _on_response(self, dialog, response_id):
        if response_id == Gtk.ResponseType.CANCEL:
            self.cancelled = True

    def is_cancelled(self):
        return self.cancelled

    def set_status(self, text):
        def update():
            self.status_label.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>")
            return False
        GLib.idle_add(update)

    def set_pass(self, pass_num, pass_label):
        def update():
            self.current_pass = pass_num
            self.status_label.set_markup(
                f"<b>{GLib.markup_escape_text(f'Pass {pass_num} of {self.total_passes} ({pass_label})…')}</b>"
            )
            return False
        GLib.idle_add(update)

    def set_progress(self, pct_within_pass):
        overall = ((self.current_pass - 1) + (pct_within_pass / 100.0)) / self.total_passes * 100.0
        overall = max(0.0, min(100.0, overall))
        def update():
            self.progress_bar.set_fraction(overall / 100.0)
            self.progress_bar.set_text(f"{overall:.0f}%")
            return False
        GLib.idle_add(update)

    def finish(self):
        pass


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def run_plugin(procedure, run_mode, image, drawables, config, run_data):
    if run_mode != Gimp.RunMode.INTERACTIVE:
        return procedure.new_return_values(
            Gimp.PDBStatusType.CALLING_ERROR,
            GLib.Error("Upscayl only supports interactive use.")
        )

    if not drawables or len(drawables) != 1:
        msg = "Upscayl requires exactly one selected layer."
        Gimp.message(msg)
        return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR, GLib.Error(msg))

    drawable = drawables[0]
    if not isinstance(drawable, Gimp.Layer):
        Gimp.message("Please select a layer (not a channel or mask) to upscale.")
        return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR, GLib.Error("Upscayl requires a layer, not a channel or mask."))

    GimpUi.init("upscayl-gimp")
    try:
        dialog = UpscaylDialog(image, drawable)
    except Exception:
        tb = traceback.format_exc()
        Gimp.message(f"Upscayl failed to open its dialog:\n\n{tb}")
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error("Upscayl dialog could not be created."))
    while True:
        response = dialog.run()
        if response != 1001:
            break
        # Reset user-adjustable processing controls without closing the dialog.
        dialog.reset_controls()

    if response != Gtk.ResponseType.OK:
        dialog.destroy()
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, None)

    try:
        choice = dialog.get_choice()
    except ValueError as exc:
        dialog.destroy()
        Gimp.message(str(exc))
        return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR, GLib.Error(str(exc)))
    dialog.destroy()

    if choice is None:
        Gimp.message(
            "No usable Upscayl install or model was selected. "
            "Install Upscayl or point to a model folder, then try again."
        )
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error("No usable Upscayl installation or model is selected."))

    if choice["tile_size"] and not choice["tile_size"].isdigit():
        Gimp.message(f"Tile size must be a whole number (got '{choice['tile_size']}').")
        return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR, GLib.Error("Tile size must be a whole number."))

    if choice["threads"]:
        parts = choice["threads"].split(":")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            Gimp.message(
                f"Threads must be in load:proc:save form, e.g. 1:2:2 "
                f"(got '{choice['threads']}')."
            )
            return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR, GLib.Error("Threads must use load:process:save numeric form."))

    save_settings({
        "scale": choice["scale_label"],
        "double_upscale": "1" if choice["double_upscale"] else "0",
        "model": choice["model_name"],
        "custom_model_dir": choice["custom_model_dir"],
        "tile_size": choice["tile_size"] or "0",
        "threads": choice["threads"],
        "tta": "1" if choice["tta"] else "0",
        "output": choice["output"],
        "dimension_mode": choice["dimension_mode"],
        "custom_width": str(choice["base_width"]),
        "chain": "1" if choice["chain"] else "0",
        "custom_height": str(choice["base_height"]),
    })

    fmt = "png"  # always PNG: lossless, and the only export call verified
                 # against current PDB argument requirements. JPEG/WEBP
                 # removed — file-jpeg-save/file-webp-save need many more
                 # mandatory PDB args (quality, smoothing, preset, etc.)
                 # that a lossy intermediate format buys nothing for an
                 # AI upscaler anyway.
    tmpdir = tempfile.mkdtemp(prefix="upscayl-gimp-")
    in_path = os.path.join(tmpdir, f"input.{fmt}")
    pass1_path = os.path.join(tmpdir, f"pass1.{fmt}")
    final_path = os.path.join(tmpdir, f"final.{fmt}")

    try:
        src = choice["source"]
        model = choice["model_name"]
        model_dir = choice["model_dir"]
        Gimp.progress_init("Upscayl: checking dependencies…")
        preflight_dependencies(src, model, model_dir, tmpdir)
        Gimp.progress_set_text("Exporting layer to temporary PNG…")
        # Export ONLY the selected layer's pixels — not the whole
        # composited image — by copying it into its own scratch image.
        # The real open image/layer are never modified.
        width = drawable.get_width()
        height = drawable.get_height()
        scratch = None
        try:
            scratch = Gimp.Image.new(width, height, image.get_base_type())
            copied_layer = Gimp.Layer.new_from_drawable(drawable, scratch)
            scratch.insert_layer(copied_layer, None, -1)
            copied_layer.set_offsets(0, 0)
            scratch.flatten()
            file_obj = Gio.File.new_for_path(in_path)
            saved = Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, scratch, file_obj, None)
            if not saved:
                raise RuntimeError("Gimp.file_save() reported failure exporting the temp PNG for Upscayl.")
        finally:
            if scratch is not None:
                try:
                    scratch.delete()
                except Exception:
                    pass


        gpu_id = choice["gpu_id"]
        tile_size = choice["tile_size"]
        threads = choice["threads"]
        tta = choice["tta"]

        scale_label = choice["scale_label"]
        scale_pass = 2 if scale_label == "2x" else 4
        double = choice["double_upscale"]
        total_passes = 2 if double else 1
        requested_factor = {"1x": 1, "2x": 2, "4x": 4, "Double Upscale": 16}.get(scale_label, 4)
        requested_w = choice["base_width"] if choice["dimension_mode"] == "custom" else width * requested_factor
        requested_h = choice["base_height"] if choice["dimension_mode"] == "custom" else height * requested_factor

        progress_dialog = ProgressDialog(total_passes, gpu_id=gpu_id)
        progress_dialog.show()

        worker_error = {}

        def worker():
            try:
                progress_dialog.set_status("Testing Upscayl / NCNN executable…")
                _probe_executable(src, should_cancel=progress_dialog.is_cancelled, timeout=8.0)
                if double:
                    progress_dialog.set_pass(1, scale_label)
                    run_upscayl_pass(
                        src, in_path, pass1_path, scale_pass, model, model_dir,
                        gpu_id, tile_size, threads, tta,
                        on_progress=progress_dialog.set_progress,
                        should_cancel=progress_dialog.is_cancelled,
                        on_status=progress_dialog.set_status,
                    )
                    progress_dialog.set_pass(2, scale_label)
                    run_upscayl_pass(
                        src, pass1_path, final_path, scale_pass, model, model_dir,
                        gpu_id, tile_size, threads, tta,
                        on_progress=progress_dialog.set_progress,
                        should_cancel=progress_dialog.is_cancelled,
                        on_status=progress_dialog.set_status,
                    )
                else:
                    progress_dialog.set_pass(1, scale_label)
                    run_upscayl_pass(
                        src, in_path, final_path, scale_pass, model, model_dir,
                        gpu_id, tile_size, threads, tta,
                        on_progress=progress_dialog.set_progress,
                        should_cancel=progress_dialog.is_cancelled,
                        on_status=progress_dialog.set_status,
                    )
            except Exception as exc:
                worker_error["exc"] = exc
            finally:
                if not progress_dialog.is_cancelled():
                    GLib.idle_add(progress_dialog.response, Gtk.ResponseType.OK)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        progress_dialog.run()  # nested GTK loop; worker performs NCNN processing

        # On Cancel, the runner's watcher terminates NCNN independently of
        # stdout activity. Do not destroy GTK state or clean temp files until
        # the worker has actually exited. This prevents use-after-destroy and
        # temp-file races.
        if progress_dialog.is_cancelled():
            thread.join()
            progress_dialog.finish()
            progress_dialog.destroy()
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, None)

        thread.join()
        progress_dialog.finish()
        progress_dialog.destroy()

        if progress_dialog.is_cancelled():
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, None)
        if "exc" in worker_error:
            raise worker_error["exc"]

        Gimp.progress_set_text("Importing Upscayl result…")
        total_label = f"{scale_pass * scale_pass}x total ({scale_label} ×2)" if double else scale_label

        if choice["output"] == "image":
            # Open the completed upscale as an independent GIMP image.
            new_image = Gimp.file_load(
                Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(final_path)
            )
            if new_image is None:
                raise RuntimeError("GIMP could not load the Upscayl result as a new image.")
            if new_image.get_width() != requested_w or new_image.get_height() != requested_h:
                new_image.scale(requested_w, requested_h)
            Gimp.Display.new(new_image)
        else:
            # Add result to the current image, then expand the image canvas to
            # the layer extents so the larger upscale is not clipped by the old canvas.
            new_layer = Gimp.file_load_layer(
                Gimp.RunMode.NONINTERACTIVE, image, Gio.File.new_for_path(final_path)
            )
            new_layer.set_name(f"{drawable.get_name()} — Upscayl {total_label} ({model})")
            image.undo_group_start()
            try:
                image.insert_layer(new_layer, None, -1)
                if new_layer.get_width() != requested_w or new_layer.get_height() != requested_h:
                    new_layer.scale(requested_w, requested_h, False)
                image.resize_to_layers()
            finally:
                image.undo_group_end()
        Gimp.displays_flush()
        Gimp.progress_set_text("Upscayl complete")
        Gimp.progress_update(1.0)

    except Exception as e:
        tb = traceback.format_exc()
        Gimp.message(f"Upscayl failed:\n\n{tb}")
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error(str(e)))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, None)


class UpscaylPlugin(Gimp.PlugIn):
    def do_set_i18n(self, name):
        return False  # no translation catalog shipped; skip the lookup/warning

    def do_query_procedures(self):
        return [PLUGIN_ID]

    def do_create_procedure(self, name):
        procedure = Gimp.ImageProcedure.new(
            self, name, Gimp.PDBProcType.PLUGIN, run_plugin, None
        )
        procedure.set_image_types("*")
        procedure.set_sensitivity_mask(
            Gimp.ProcedureSensitivityMask.DRAWABLE
            | Gimp.ProcedureSensitivityMask.DRAWABLES
        )
        procedure.set_menu_label("Upscayl…")
        procedure.add_menu_path("<Image>/Filters/Enhance")
        procedure.set_documentation(
            "Upscale the active layer with Upscayl (AI upscaler)",
            "Runs the Upscayl CLI (upscayl-bin) against the active layer "
            "and adds the result as a new layer. Supports 2x/4x scale "
            "with an optional Double Upscale pass (matching Upscayl's own "
            "GUI toggle), any installed or custom model, tile size, "
            "thread count and TTA mode. Vulkan device selection is left to the backend. Auto-detects "
            "system, Flatpak, or AppImage installs of Upscayl.",
            PLUGIN_ID,
        )
        procedure.set_attribution("Zarir + Claude", "GPL-3.0", "2026")
        return procedure


Gimp.main(UpscaylPlugin.__gtype__, sys.argv)
