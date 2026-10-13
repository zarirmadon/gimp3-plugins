#!/usr/bin/env python3
# colour-grading.py
# SPDX-License-Identifier: GPL-3.0-only
#
# Colour Grading — GIMP 3.2 plugin
# Copyright (C) 2026  Zarir Madon — www.zarirmadon.com
# Designed by Zarir Madon (www.zarirmadon.com)
# Coded by Claude Sonnet 5.5 and OpenAI GPT 5.6
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License only.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import math
import colorsys
import shutil
import json
import sys
import os
import re
import time
from collections import Counter

import gi
gi.require_version('Gdk', '3.0')
gi.require_version('Gimp', '3.0')
gi.require_version('GimpUi', '3.0')
gi.require_version('Gegl', '0.4')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('GLib', '2.0')
gi.require_version('Gtk', '3.0')
gi.require_version('Pango', '1.0')
from gi.repository import Gimp, GimpUi, Gegl, GLib, GObject, Gdk, Gtk, Pango

import cairo

PRESET_FORMATS = ('colour-grading-preset', 'color-grading-preset')
GRADE_PARASITE = 'colour-grading-state-v1'

def _config_dir():
    base = os.path.join(os.path.expanduser('~'), '.config', 'GIMP', '3.2')
    new = os.path.join(base, 'colour-grading')
    old = os.path.join(base, 'color-grading')
    if not os.path.isdir(new) and os.path.isdir(old):
        try: shutil.copytree(old, new)
        except Exception: pass
    os.makedirs(new, exist_ok=True)
    return new
LAST_EDIT_PARASITE = GRADE_PARASITE

DEBUG = bool(os.environ.get('CG_DEBUG'))

def _dbg(*a):
    if DEBUG:
        print('[CG]', *a, file=sys.stderr, flush=True)

def _read_last_edit(layer):
    try:
        para = layer.get_parasite(LAST_EDIT_PARASITE)
        if para is None:
            return None
        data = para.get_data()
        if isinstance(data, tuple):
            data = data[0]
        raw = bytes(data).rstrip(b'\x00')
        obj = json.loads(raw.decode('utf-8'))
        return obj if obj.get('format') in ('colour-grading-last-edit', 'color-grading-last-edit') else None
    except Exception:
        return None

def _use_lab_saturation(cfg):
    for val in ('CIE-Lab', 1):
        try: cfg.set_property('colorspace', val); return True
        except Exception: pass
    return False

RESULT_PARASITE = 'colour-grading-result'
RESULT_LAYER_NAME = 'Colour Grading'

def _next_result_name(image):
    top = 0
    for lyr in _all_layers(image):
        try:
            m = re.match(r'^' + re.escape(RESULT_LAYER_NAME) + r' #(\d+)$', lyr.get_name() or '')
            if m: top = max(top, int(m.group(1)))
        except Exception: pass
    return '%s #%d' % (RESULT_LAYER_NAME, top + 1)

def _all_layers(image):
    out = []
    def walk(items):
        for l in items or []:
            out.append(l)
            try:
                if l.is_group_layer(): walk(l.get_children())
            except Exception: pass
    try: walk(image.get_layers())
    except Exception: pass
    return out


def _write_last_edit(layer, grade):
    try:
        payload = {'format': 'colour-grading-last-edit', 'version': 2,
                   'plugin': 'colour-grading', 'grade': grade}
        blob = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        flags = int(getattr(Gimp, 'PARASITE_PERSISTENT', 1)) | int(getattr(Gimp, 'PARASITE_UNDOABLE', 2))
        layer.attach_parasite(Gimp.Parasite.new(LAST_EDIT_PARASITE, flags, blob))
        return True
    except Exception as exc:
        try:
            Gimp.message('Could not store Colour Grading Last Edit: ' + str(exc))
        except Exception:
            pass
        return False

P = {
    'surface':      (0.118, 0.118, 0.118),
    'track_bg':     (0.094, 0.094, 0.094),
    'bar_track':    (0.070, 0.070, 0.070),
    'xhair':        (1.0,   1.0,   1.0,   0.10),
    'guide':        (1.0,   1.0,   1.0,   0.28),
    'centre_dot':   (1.0,   1.0,   1.0,   0.28),
    'handle_ring':  (1.0,   1.0,   1.0,   0.88),
    'handle_dot':   (1.0,   1.0,   1.0,   1.0),

    'dot_shw_fill':  (0.165, 0.165, 0.165),
    'dot_shw_ring':  (0.745, 0.745, 0.745, 0.85),
    'dot_mid_fill':  (0.314, 0.314, 0.671),
    'dot_mid_ring':  (0.667, 0.667, 0.824, 0.85),
    'dot_hlt_fill':  (0.784, 0.784, 0.784),
    'dot_hlt_ring':  (0.431, 0.431, 0.431, 0.85),

    'bar_r_fill':   (0.780, 0.220, 0.180),
    'bar_g_fill':   (0.220, 0.620, 0.260),
    'bar_b_fill':   (0.200, 0.420, 0.780),
    'bar_zero':     (1.0,   1.0,   1.0, 0.18),
    'bar_txt':      (0.600, 0.600, 0.600),
    'bar_txt_neg':  (1.0,   0.500, 0.400),
    'sat_fill':     (0.780, 0.620, 0.160),
    'contrast_fill':(0.400, 0.680, 0.820),
    'pivot_fill':   (0.680, 0.680, 0.680),

    'hist_r':  (0.824, 0.275, 0.235, 0.45),
    'hist_g':  (0.235, 0.706, 0.314, 0.40),
    'hist_b':  (0.235, 0.392, 0.784, 0.45),
    'hist_div':(1.0,   1.0,   1.0,   0.06),
    'hist_txt':(1.0,   1.0,   1.0,   0.18),
}


PLUGIN_NAME  = 'colour-grading'
MENU_LABEL   = 'Colour Grading'
BUILD_TAG    = 'build 27'

SHADOWS    = 'shadows'
MIDTONES   = 'midtones'
HIGHLIGHTS = 'highlights'
MASTER     = 'master'
BANDS      = (SHADOWS, MIDTONES, HIGHLIGHTS)
BAND_LABEL = {
    SHADOWS:    'Lift',
    MIDTONES:   'Gamma',
    HIGHLIGHTS: 'Gain',
    MASTER:     'Master',
}


DEFAULT_SHADOWS_MID    = 0.50
DEFAULT_HIGHLIGHTS_MID = 0.50
DEFAULT_MIDTONES_LEFT  = 0.33
DEFAULT_MIDTONES_RIGHT = 0.66


CB_SCALE = {SHADOWS: 1.5, MIDTONES: 0.75, HIGHLIGHTS: 1.5, MASTER: 1.0}


def _hsv_to_rgb(h, s, v):
    if s == 0.0:
        return v, v, v
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    i = i % 6
    if i == 0: return v, t, p
    if i == 1: return q, v, p
    if i == 2: return p, v, t
    if i == 3: return p, q, v
    if i == 4: return t, p, v
    return v, p, q

def wheel_to_rgb(hue_deg, saturation):
    r, g, b = _hsv_to_rgb(hue_deg / 360.0, 1.0, 1.0)
    mean = (r + g + b) / 3.0
    return (r - mean) * saturation, (g - mean) * saturation, (b - mean) * saturation

def rgb_to_wheel(r, g, b):
    r, g, b = float(r), float(g), float(b)
    hi, lo = max(r, g, b), min(r, g, b)
    sat = hi - lo
    if sat < 1e-9:
        return 0.0, 0.0
    h, _s, _v = colorsys.rgb_to_hsv(r - lo, g - lo, b - lo)
    return (h * 360.0) % 360.0, min(1.0, sat)


def _parse_thumbnail_data(result):
    vals = list(result) if isinstance(result, (tuple, list)) else [result]
    blob = None; nums = []
    for v in vals:
        if isinstance(v, (int, float)): nums.append(int(v))
        elif blob is None: blob = v
    if blob is None or len(nums) < 3:
        raise RuntimeError('unexpected thumbnail-data result')
    w, h, bpp = nums[-3], nums[-2], nums[-1]
    if hasattr(blob, 'get_data'): raw = bytes(blob.get_data())
    elif hasattr(blob, 'get_bytes'): raw = bytes(blob.get_bytes())
    else: raw = bytes(blob)
    if w <= 0 or h <= 0 or bpp <= 0 or not raw:
        raise RuntimeError('invalid thumbnail (%dx%d bpp=%d bytes=%d)' % (w, h, bpp, len(raw)))
    return w, h, bpp, raw

def _planes_from_raw(w, h, bpp, raw):
    n = w * h
    if bpp >= 3:
        r, g, b = raw[0::bpp][:n], raw[1::bpp][:n], raw[2::bpp][:n]
    else:
        r = g = b = raw[0::bpp][:n]
    if len(r) < n:
        raise RuntimeError('incomplete sample: %d of %d pixels' % (len(r), n))
    return w, h, (bytes(r), bytes(g), bytes(b))

def _planes_from_pixbuf(pb):
    w, h = pb.get_width(), pb.get_height()
    rs, nc = pb.get_rowstride(), pb.get_n_channels()
    px = bytes(pb.get_pixels())
    r = bytearray(); g = bytearray(); b = bytearray()
    for y in range(h):
        row = px[y * rs: y * rs + w * nc]
        if nc >= 3:
            r += row[0::nc]; g += row[1::nc]; b += row[2::nc]
        else:
            r += row[0::nc]; g += row[0::nc]; b += row[0::nc]
    return w, h, (bytes(r), bytes(g), bytes(b))

def grab_rgb_sample(image, preview_layer=None, max_w=320, max_h=240):
    errors = []
    try:
        w0, h0 = image.get_width(), image.get_height()
        k = min(max_w / float(w0), max_h / float(h0), 1.0)
        tw, th = max(1, int(w0 * k)), max(1, int(h0 * k))
        trans = getattr(Gimp, 'PixbufTransparency', None)
        pb = image.get_thumbnail(tw, th, trans.KEEP_ALPHA if trans is not None else 0)
        if pb is None:
            raise RuntimeError('no pixbuf returned')
        w, h, planes = _planes_from_pixbuf(pb)
        return w, h, planes, 'image projection (pixbuf)'
    except Exception as exc:
        errors.append('pixbuf: %s' % exc)
    tmp = None
    try:
        tmp = Gimp.Layer.new_from_visible(image, image, 'Colour Grading scope sample')
        image.insert_layer(tmp, None, 0)
        w, h, planes = _planes_from_raw(*_parse_thumbnail_data(tmp.get_thumbnail_data(max_w, max_h)))
        return w, h, planes, 'baked visible layer'
    except Exception as exc:
        errors.append('baked layer: %s' % exc)
    finally:
        if tmp is not None:
            try: image.remove_layer(tmp)
            except Exception: pass
    try:
        if preview_layer is None:
            raise RuntimeError('no preview layer')
        w, h, planes = _planes_from_raw(*_parse_thumbnail_data(preview_layer.get_thumbnail_data(max_w, max_h)))
        return w, h, planes, 'preview layer (may exclude live filters)'
    except Exception as exc:
        errors.append('preview layer: %s' % exc)
    raise RuntimeError('all sampling routes failed — ' + ' | '.join(errors))

def _intensity_surface(counts, width, height, rgb, a_min=.055, gain=.68, a_max=.82):
    mx = max(counts) or 1
    little = sys.byteorder == 'little'
    lut = [b'\x00\x00\x00\x00']
    for n in range(1, 256):
        a = min(a_max, a_min + gain * math.sqrt(min(n, mx) / mx))
        A = int(a * 255 + .5)
        R, G, B = (int(c * a * 255 + .5) for c in rgb)
        lut.append(bytes((B, G, R, A)) if little else bytes((A, R, G, B)))
    data = bytearray(width * height * 4)
    for i, n in enumerate(counts):
        if n:
            data[i * 4:i * 4 + 4] = lut[n]
    surf = cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, width, height, width * 4)
    return surf, data

def _paint_scaled(cr, surf, x, y, w, h, op=None):
    cr.save()
    cr.translate(x, y)
    cr.scale(w / float(surf.get_width()), h / float(surf.get_height()))
    cr.set_source_surface(surf, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_BILINEAR)
    if op is not None:
        cr.set_operator(op)
    cr.paint()
    cr.restore()


class ScopeDisplay(Gtk.DrawingArea):

    MODES = ('Histogram', 'RGB Waveform', 'RGB Parade', 'Vectorscope')
    _CB = (-0.114572, -0.385428, 0.500000)
    _CR = (0.500000, -0.454153, -0.045847)

    def __init__(self):
        super().__init__()
        self.mode = 'Histogram'
        self.data = None
        self.error = ''
        self.source = ''
        self._hist = None; self._wave = None; self._vec = None; self._keep = []
        self.set_size_request(-1, 300)
        self.connect('draw', self._draw)
        self.set_tooltip_text('Monitoring scope of the graded preview. Histogram shows level distribution, '
                              'Waveform/Parade show level against image position, Vectorscope shows hue and chroma.')

    def set_mode(self, mode):
        self.mode = mode
        self.queue_draw()

    @classmethod
    def _cbcr(cls, r, g, b):
        cb = (cls._CB[0]*r + cls._CB[1]*g + cls._CB[2]*b) / 255.0
        cr = (cls._CR[0]*r + cls._CR[1]*g + cls._CR[2]*b) / 255.0
        return max(-.5, min(.5, cb)), max(-.5, min(.5, cr))

    def refresh(self, image, preview_layer=None):
        t0 = time.monotonic()
        w, h, (r, g, b), label = grab_rgb_sample(image, preview_layer)
        cnt = [Counter(p) for p in (r, g, b)]
        self._hist = [[c.get(i, 0) for i in range(256)] for c in cnt]
        waves = [bytearray(w * 256) for _ in range(3)]
        for y in range(h):
            o = y * w
            for ci, plane in enumerate((r, g, b)):
                wv = waves[ci]
                for x in range(w):
                    q = (255 - plane[o + x]) * w + x
                    if wv[q] < 255: wv[q] += 1
        vs = bytearray(256 * 256)
        cbcr = self._cbcr
        for i in range(w * h):
            cb, crr = cbcr(r[i], g[i], b[i])
            q = max(0, min(255, round(128 - crr * 220))) * 256 + max(0, min(255, round(128 + cb * 220)))
            if vs[q] < 255: vs[q] += 1
        keep = []
        cols = ((1, .18, .18), (.18, 1, .28), (.2, .45, 1))
        self._wave = []
        for ci in range(3):
            s, d = _intensity_surface(waves[ci], w, 256, cols[ci]); self._wave.append(s); keep.append(d)
        s, d = _intensity_surface(vs, 256, 256, (.92, .92, .92), a_min=.06, gain=.80, a_max=.90)
        self._vec = s; keep.append(d); self._keep = keep
        self.data = (w, h); self.error = ''; self.source = label
        self.queue_draw()
        _dbg('scope refresh %dx%d via %s in %.0f ms' % (w, h, label, (time.monotonic() - t0) * 1000))
        return label

    def _bg(self, cr, w, h):
        cr.set_source_rgb(.055, .055, .055); cr.rectangle(0, 0, w, h); cr.fill()
        cr.set_line_width(1); cr.set_source_rgba(1, 1, 1, .10)
        for f in (.25, .5, .75):
            cr.move_to(0, h * f); cr.line_to(w, h * f); cr.stroke()

    def _scope_text(self, cr, text, x, y, alpha=.65):
        cr.set_source_rgba(1, 1, 1, alpha); cr.move_to(x, y); cr.show_text(text)

    def _draw_vectorscope_graticule(self, cr, ox, oy, size):
        cx = ox + size / 2; cy = oy + size / 2
        cr.set_line_width(1)
        for frac in (.25, .50, .75, 1.0):
            cr.set_source_rgba(1, 1, 1, .08 if frac < 1 else .18)
            cr.arc(cx, cy, size * .47 * frac, 0, 2 * math.pi); cr.stroke()
        cr.set_source_rgba(1, 1, 1, .14)
        cr.move_to(cx, oy + size * .03); cr.line_to(cx, oy + size * .97)
        cr.move_to(ox + size * .03, cy); cr.line_to(ox + size * .97, cy); cr.stroke()
        for label, r, g, b in (('R', 255, 0, 0), ('Y', 255, 255, 0), ('G', 0, 255, 0),
                               ('C', 0, 255, 255), ('B', 0, 0, 255), ('M', 255, 0, 255)):
            cb, crv = self._cbcr(r, g, b)
            mag = math.hypot(cb, crv) or 1.0
            rr = size * .405
            tx = cx + (cb / mag) * rr; ty = cy + (-crv / mag) * rr
            cr.set_source_rgba(r / 255, g / 255, b / 255, .72)
            cr.arc(tx, ty, 3.0, 0, 2 * math.pi); cr.stroke()
            cr.move_to(tx + 5, ty + 4); cr.show_text(label)
        theta = math.radians(123.0)
        cr.set_source_rgba(1, .72, .55, .25)
        cr.move_to(cx, cy)
        cr.line_to(cx + math.cos(theta) * size * .43, cy - math.sin(theta) * size * .43); cr.stroke()

    def _draw(self, _w, cr):
        W = self.get_allocated_width(); H = self.get_allocated_height()
        self._bg(cr, W, H)
        if not self.data:
            self._scope_text(cr, self.error or 'Press Update to read the picture.', 18, 32)
            return False
        if self.mode == 'Histogram':
            peak = max(max(a) for a in self._hist) or 1
            for bins, col in zip(self._hist, ((1, .22, .22), (.25, 1, .35), (.25, .5, 1))):
                cr.set_source_rgba(*col, .72); cr.set_line_width(1.35)
                for i, v in enumerate(bins):
                    x = i * (W - 1) / 255.0; y = H - 8 - (H - 18) * math.sqrt(v / peak)
                    if i == 0: cr.move_to(x, y)
                    else: cr.line_to(x, y)
                cr.stroke()
        elif self.mode == 'RGB Waveform':
            for s in self._wave:
                _paint_scaled(cr, s, 0, 0, W, H, cairo.OPERATOR_ADD)
        elif self.mode == 'RGB Parade':
            for i, s in enumerate(self._wave):
                _paint_scaled(cr, s, i * W / 3.0, 0, W / 3.0, H)
                self._scope_text(cr, 'RGB'[i], i * W / 3.0 + 8, 18, .55)
        else:
            size = max(20, min(W, H) - 34); ox = (W - size) / 2; oy = (H - size) / 2
            self._draw_vectorscope_graticule(cr, ox, oy, size)
            _paint_scaled(cr, self._vec, ox, oy, size, size)
            self._scope_text(cr, 'BT.709-style Cb/Cr • grading reference', ox + 8, oy + size - 8, .42)
        return False

class PreviewManager:

    def __init__(self, image, drawable):
        self._image = image
        self._drawable = drawable
        self._preview_layer = None
        self._filters = {}
        self._sat_filter = None
        self._contrast_filter = None
        self._sat_value = 1.0
        self._contrast_value = 0.0
        self._pivot_value = 0.5
        self._undo_open = False
        self._initialised = False

        self._batch_depth = 0
        self._batch_dirty = False
        self._dirty_filters = {}
        self._band_wheel = {b: (0.0, 0.0, 0.0) for b in BANDS}
        self._master_rgb = (0.0, 0.0, 0.0)
        self._range_values = {
            'shadows_mid': DEFAULT_SHADOWS_MID,
            'midtones_left': DEFAULT_MIDTONES_LEFT,
            'midtones_right': DEFAULT_MIDTONES_RIGHT,
            'highlights_mid': DEFAULT_HIGHLIGHTS_MID,
        }

    def begin_batch(self):
        self._batch_depth += 1

    def end_batch(self):
        if self._batch_depth > 0:
            self._batch_depth -= 1
        if self._batch_depth == 0:
            dirty = list(self._dirty_filters.values()); self._dirty_filters.clear()
            for f in dirty:
                try: f.update()
                except Exception: pass
            if dirty or self._batch_dirty:
                self._batch_dirty = False
                Gimp.displays_flush()

    def _upd(self, f):
        if f is None: return
        if self._batch_depth: self._dirty_filters[id(f)] = f
        else: f.update()

    def _display_flush(self):
        if self._batch_depth:
            self._batch_dirty = True
        else:
            Gimp.displays_flush()

    @staticmethod
    def _set_filter_visible(filter_obj, visible):
        if filter_obj is None:
            return
        try:
            if bool(filter_obj.get_visible()) != bool(visible):
                filter_obj.set_visible(bool(visible))
        except Exception:
            pass

    def initialise(self):
        try:
            self._image.undo_group_start()
            self._undo_open = True
            src = self._drawable
            try:
                if src.is_group_layer() is True:
                    raise RuntimeError('Please select a normal layer, not a layer group.')
            except AttributeError:
                pass
            layer = src.copy()
            if layer is None:
                raise RuntimeError('Could not create the Colour Grading preview layer.')
            layer.set_name(_next_result_name(self._image))
            self._image.insert_layer(layer, src.get_parent(), self._image.get_item_position(src))
            layer.set_visible(True)
            self._preview_layer = layer

            transfer = {
                SHADOWS: getattr(Gimp.TransferMode, 'SHADOWS', 0),
                MIDTONES: getattr(Gimp.TransferMode, 'MIDTONES', 1),
                HIGHLIGHTS: getattr(Gimp.TransferMode, 'HIGHLIGHTS', 2),
            }
            for band in BANDS:
                f = Gimp.DrawableFilter.new(layer, 'gimp:color-balance', BAND_LABEL[band])
                if f is None:
                    raise RuntimeError(f'gimp:color-balance is unavailable for {BAND_LABEL[band]}.')
                cfg = f.get_config()
                cfg.set_property('range', transfer[band])
                cfg.set_property('preserve-luminosity', True)
                cfg.set_property('cyan-red', 0.0)
                cfg.set_property('magenta-green', 0.0)
                cfg.set_property('yellow-blue', 0.0)
                f.update()
                f.set_visible(False)
                layer.append_filter(f)
                self._filters[band] = f

            self._sat_filter = Gimp.DrawableFilter.new(layer, 'gegl:saturation', 'Saturation')
            if self._sat_filter is not None:
                cfg = self._sat_filter.get_config()
                cfg.set_property('scale', 1.0)
                _use_lab_saturation(cfg)
                self._sat_filter.update()
                self._sat_filter.set_visible(False)
                layer.append_filter(self._sat_filter)

            self._contrast_chain = []; self._contrast_filter = None

            self._initialised = True
            self._display_flush()
        except Exception as exc:
            self._error_dialog(exc, 'Failed to initialise Colour Grading')
            self.discard()

    def _range_strength(self, band):
        if band == SHADOWS:
            x = max(0.0, min(1.0, float(self._range_values['shadows_mid'])))
            return math.exp((DEFAULT_SHADOWS_MID - x) * 2.0)
        if band == HIGHLIGHTS:
            x = max(0.0, min(1.0, float(self._range_values['highlights_mid'])))
            return math.exp((x - DEFAULT_HIGHLIGHTS_MID) * 2.0)
        left = max(0.0, min(1.0, float(self._range_values['midtones_left'])))
        right = max(0.0, min(1.0, float(self._range_values['midtones_right'])))
        width = max(0.02, right - left)
        default_width = DEFAULT_MIDTONES_RIGHT - DEFAULT_MIDTONES_LEFT
        ratio = width / default_width
        return max(0.25, min(3.0, ratio ** 1.35))


    def _apply_band(self, band):
        f = self._filters.get(band)
        if f is None:
            return
        br,bg,bb=self._band_wheel.get(band,(0.0,0.0,0.0))
        mr,mg,mb=self._master_rgb
        scale=CB_SCALE[band]*self._range_strength(band)
        vr=max(-1.0,min(1.0,float(br)*scale+float(mr)))
        vg=max(-1.0,min(1.0,float(bg)*scale+float(mg)))
        vb=max(-1.0,min(1.0,float(bb)*scale+float(mb)))
        active=any(abs(v)>1e-7 for v in (vr,vg,vb))
        cfg=f.get_config()

        authority=.72
        cfg.set_property('cyan-red', max(-1.0,min(1.0,vr*authority)))
        cfg.set_property('magenta-green', max(-1.0,min(1.0,vg*authority)))
        cfg.set_property('yellow-blue', max(-1.0,min(1.0,vb*authority)))
        self._set_filter_visible(f,active)
        self._upd(f)

    def update_wheels(self, wheel_values):
        if not self._initialised:
            return
        try:
            for band in BANDS:
                _, _, r, g, b = wheel_values[band]
                self._band_wheel[band] = (r, g, b)
                self._apply_band(band)
            self._display_flush()
        except Exception as exc:
            self._error_dialog(exc, 'Failed to update colour controls')


    def update_master_rgb(self, r, g, b):
        if not self._initialised:
            return
        try:
            self._master_rgb = (float(r) * CB_SCALE[MASTER],
                                float(g) * CB_SCALE[MASTER],
                                float(b) * CB_SCALE[MASTER])
            for band in BANDS:
                self._apply_band(band)
            self._display_flush()
        except Exception as exc:
            self._error_dialog(exc, 'Failed to update master control')

    def set_preview_enabled(self, enabled):
        if self._preview_layer is not None:
            self._preview_layer.set_visible(bool(enabled))
            self._display_flush()

    def update_ranges(self, range_values, band=None):
        if not self._initialised:
            return
        try:
            self._range_values.update(range_values)
            for b in (BANDS if band is None else (band,)):
                self._apply_band(b)
            self._display_flush()
        except Exception as exc:
            self._error_dialog(exc, 'Failed to update tonal ranges')

    def update_saturation(self, saturation):
        self._sat_value = float(saturation)
        if not self._initialised or self._sat_filter is None:
            return
        try:
            cfg = self._sat_filter.get_config()
            scale=max(0.0, min(2.0, float(saturation)))
            cfg.set_property('scale', scale)
            self._set_filter_visible(self._sat_filter, abs(scale - 1.0) > 1e-7)
            self._upd(self._sat_filter)
            self._display_flush()
        except Exception as exc:
            self._error_dialog(exc, 'Failed to update saturation')

    def update_contrast(self, contrast, pivot):
        self._contrast_value = float(contrast)
        self._pivot_value = float(pivot)
        if not self._initialised or self._contrast_filter is None:
            return
        try:
            cfg = self._contrast_filter.get_config()

            raw=max(-1.0,min(1.0,float(contrast)))
            shaped=math.copysign(abs(raw)**1.55,raw) if raw else 0.0
            c=2.0**(1.25*shaped)
            pv=max(0.05,min(0.95,float(pivot)))

            brightness=(pv-0.5)*(1.0-c)
            cfg.set_property('contrast',c)
            cfg.set_property('brightness',max(-1.0,min(1.0,brightness)))
            for f in getattr(self, '_contrast_chain', [self._contrast_filter]):
                self._set_filter_visible(f, abs(raw) > 1e-7)
                self._upd(f)
            self._display_flush()
        except Exception as exc:
            self._error_dialog(exc, 'Failed to update contrast / pivot')

    def discard(self):
        try:
            if self._preview_layer is not None:
                try:
                    for f in list(self._preview_layer.get_filters() or []):
                        try:
                            if f is not None and f.is_valid(): f.delete()
                        except Exception:
                            try: f.delete()
                            except Exception: pass
                except Exception:
                    pass
            if self._preview_layer is not None:
                self._image.remove_layer(self._preview_layer)
        except Exception:
            pass
        self._preview_layer = None
        self._filters.clear()
        self._sat_filter = None
        self._contrast_filter = None
        self._initialised = False
        self._close_undo()
        try: self._display_flush()
        except Exception: pass

    def _close_undo(self):
        if self._undo_open:
            try: self._image.undo_group_end()
            except Exception: pass
            self._undo_open = False

    def _error_dialog(self, exc, title):
        try: Gimp.message(f'{title}:\n{exc}')
        except Exception: pass


_M_RGB2XYZ = ((0.4360747, 0.3850649, 0.1430804),
              (0.2225045, 0.7168786, 0.0606169),
              (0.0139322, 0.0971045, 0.7141733))
_M_XYZ2RGB = ((3.1338561, -1.6168667, -0.4906146),
              (-0.9787684, 1.9161415, 0.0334540),
              (0.0719453, -0.2289914, 1.4052427))
_WHITE_D50 = (0.9642, 1.0, 0.8249)

def srgb_to_linear(c):
    c = max(0.0, min(1.0, float(c)))
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

def linear_to_srgb(c):
    c = max(0.0, min(1.0, float(c)))
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055

def _lab_f(t):
    return t ** (1.0 / 3.0) if t > 216.0 / 24389.0 else (24389.0 / 27.0 * t + 16.0) / 116.0

def _lab_finv(t):
    return t ** 3 if t ** 3 > 216.0 / 24389.0 else (116.0 * t - 16.0) * 27.0 / 24389.0

def linear_rgb_to_lch(r, g, b):
    x = sum(_M_RGB2XYZ[0][i] * v for i, v in enumerate((r, g, b)))
    y = sum(_M_RGB2XYZ[1][i] * v for i, v in enumerate((r, g, b)))
    z = sum(_M_RGB2XYZ[2][i] * v for i, v in enumerate((r, g, b)))
    fx, fy, fz = _lab_f(x / _WHITE_D50[0]), _lab_f(y / _WHITE_D50[1]), _lab_f(z / _WHITE_D50[2])
    L = 116.0 * fy - 16.0; a = 500.0 * (fx - fy); bb = 200.0 * (fy - fz)
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360.0

def lch_to_linear_rgb(L, C, h):
    a = C * math.cos(math.radians(h)); bb = C * math.sin(math.radians(h))
    fy = (L + 16.0) / 116.0; fx = fy + a / 500.0; fz = fy - bb / 200.0
    x = _lab_finv(fx) * _WHITE_D50[0]; y = _lab_finv(fy) * _WHITE_D50[1]; z = _lab_finv(fz) * _WHITE_D50[2]
    return tuple(_M_XYZ2RGB[i][0] * x + _M_XYZ2RGB[i][1] * y + _M_XYZ2RGB[i][2] * z for i in range(3))

def lch_swatch(L, C, h):
    lo, hi = 0.0, float(C)
    for _ in range(14):
        mid = (lo + hi) / 2.0
        rgb = lch_to_linear_rgb(L, mid, h)
        if all(-1e-4 <= v <= 1.0001 for v in rgb): lo = mid
        else: hi = mid
    rgb = lch_to_linear_rgb(L, lo, h)
    return tuple(linear_to_srgb(v) for v in rgb)

def tint_gains(hue_deg, strength):
    s = max(0.0, min(1.0, float(strength) / 100.0))
    if s < 1e-7: return None
    rs, gs, bs = colorsys.hsv_to_rgb((float(hue_deg) % 360.0) / 360.0, 1.0, 1.0)
    r, g, b = srgb_to_linear(rs), srgb_to_linear(gs), srgb_to_linear(bs)
    mean = (r + g + b) / 3.0; k = 0.70 * s
    gr, gg, gb = 1.0 + (r - mean) * k, 1.0 + (g - mean) * k, 1.0 + (b - mean) * k
    y = 0.2126 * gr + 0.7152 * gg + 0.0722 * gb
    return gr / y, gg / y, gb / y


PRESET_LOOKS = [
 ('tealorange', 'Teal & Orange', 'Blockbuster look: cool teal shadows, warm orange highlights, skin kept natural.',
  dict(sh=(-.03, .01, .05), hi=(.04, .012, -.03), sat=1.05, con=.22, tone=(-6, 0, 0, 4, 0),
       tc=[(195, 34, 0), (30, 8, 0), (28, 34, 0)], hue=[(72, 0, 12, 0, 22), (269, -14, 8, -6, 28)], gc=(0, 0, 8, 100))),
 ('bleach', 'Bleach Bypass', 'Silvery, desaturated and contrasty, like skipping the bleach step when developing film.',
  dict(sat=.62, con=.38, tone=(-10, -4, 0, 6, 6), tc=[(210, 14, 0), (40, 0, 0), (50, 8, 0)], gc=(-4, 0, 0, 100))),
 ('portrait', 'Portrait Film', 'Warm skin, creamy highlights and gentle pastel greens, in the spirit of classic portrait film.',
  dict(sh=(.01, .008, .012), hi=(.03, .015, -.01), sat=.96, con=-.04, tone=(10, 5, 0, -2, -4),
       tc=[(170, 12, 0), (30, 10, 0), (42, 16, 0)], hue=[(134, -14, -18, 4, 26), (53, 0, 6, 3, 20)], gc=(10, 3, 0, 100))),
 ('slide', 'Vivid Slide', 'Rich, saturated slide-film colour with punchy blues and greens.',
  dict(sat=1.14, con=.30, tone=(-6, 0, 0, 4, 2), hue=[(134, -6, 24, -2, 28), (269, 0, 22, -4, 30), (41, 0, 14, 0, 22)],
       prim={'blue': (0, 18), 'red': (0, 10)}, gc=(2, 0, 12, 100))),
 ('tungsten', 'Night Neon', 'Cinema tungsten-film night look: cool blue shadows with warm red-orange glow in the highlights.',
  dict(sh=(-.03, 0, .06), sat=1.06, con=.24, tone=(-8, -3, 0, 5, 0),
       tc=[(205, 36, 0), (260, 8, 0), (14, 32, 0)], hue=[(41, 6, 12, 0, 24)], gc=(-22, -4, 10, 100))),
 ('fadedforest', 'Faded Forest', 'Moody woodland: greens pulled toward teal and muted, lifted blacks, cool shadows.',
  dict(sat=.9, con=.15, tone=(10, 4, 0, -2, -4), tc=[(150, 26, 0), (40, 6, 0), (60, 10, 0)],
       hue=[(134, 24, -26, -4, 30)], gc=(-4, 0, 0, 100))),
 ('golden', 'Golden Hour Glow', 'Warm sunset light: amber highlights, rose-tinted shadows and a glowing orange cast.',
  dict(sat=1.08, con=.12, tone=(2, 2, 0, 4, 0), tc=[(335, 16, 0), (30, 14, 0), (40, 38, 0)],
       hue=[(72, -4, 14, 3, 24), (100, -8, 10, 3, 22)], gc=(38, 2, 6, 100))),
 ('cross', 'Cross Process', 'Lab-accident colour: blue-magenta shadows and acid yellow-green highlights with hard contrast.',
  dict(sat=1.14, con=.34, tone=(-4, 0, 0, 4, 2), tc=[(250, 32, 0), (300, 6, 0), (65, 30, 0)],
       prim={'green': (-18, 14)}, gc=(0, -6, 10, 100))),
 ('pastel', 'Pastel Dream', 'Soft, airy and lifted: pink-lavender shadows, milky blacks, gentle peach highlights.',
  dict(sat=.92, con=-.12, tone=(26, 12, 4, -4, -8), tc=[(315, 22, 0), (350, 10, 0), (38, 16, 0)],
       hue=[(269, 0, -8, 6, 30)], gc=(4, 3, -4, 100))),
 ('cyberpunk', 'Cyberpunk', 'Neon city: violet-blue shadows, magenta mids and cyan highlights.',
  dict(sat=1.1, con=.3, tone=(-8, -2, 0, 4, 2), tc=[(270, 40, 0), (305, 16, 0), (180, 34, 0)],
       hue=[(327, 0, 24, 0, 28), (301, 6, 16, 0, 28)], gc=(-6, 6, 20, 100))),
 ('autumn', 'Autumn Gold', 'Warm harvest colour: yellows and greens pushed toward gold and orange.',
  dict(sat=1.1, con=.16, tone=(0, 0, 0, 3, 0), tc=[(25, 14, 0), (35, 14, 0), (45, 22, 0)],
       hue=[(100, -14, 20, 2, 26), (134, -34, 10, 0, 30), (41, 0, 12, 0, 22)], gc=(22, 0, 8, 100))),
 ('arctic', 'Arctic Steel', 'Cold, clean and steely: icy blue cast, slightly muted, crisp contrast.',
  dict(sat=.85, con=.20, tone=(-4, 0, 0, 3, 2), tc=[(215, 30, 0), (205, 10, 0), (190, 16, 0)],
       hue=[(269, -6, 10, 0, 30)], gc=(-36, -2, -4, 100))),
 ('sepia', 'Warm Sepia', 'Antique brown monochrome toning, as in old albumen prints.',
  dict(sat=.22, con=.10, tone=(4, 2, 0, -2, -4), tc=[(28, 46, 0), (34, 42, 0), (42, 30, 0)], gc=(8, 0, 0, 100))),
 ('silver', 'Silver Noir', 'Near-monochrome with a cold shadow tone and a faint warm highlight, high contrast.',
  dict(sat=.05, con=.40, tone=(-12, -4, 0, 4, 4), tc=[(215, 14, 0), (40, 0, 0), (45, 10, 0)], gc=(0, 0, 0, 100))),
 ('summer', 'Summer Pop', 'Bright holiday colour: saturated sky and sea, sunny yellows, a touch warm.',
  dict(sat=1.12, con=.16, tone=(4, 2, 0, 4, 0), tc=[(200, 8, 0), (40, 6, 0), (50, 12, 0)],
       hue=[(269, -10, 22, 0, 30), (196, 4, 18, 0, 26), (100, 0, 14, 2, 24)], gc=(8, 0, 10, 100))),
 ('landscape', 'Lush Landscape', 'Deep skies, rich foliage and warm light: a classic landscape finish.',
  dict(sat=1.1, con=.25, tone=(0, 14, 0, 0, -4), tc=[(215, 14, 0), (40, 4, 0), (45, 18, 0)],
       hue=[(134, -8, 26, -2, 28), (269, 0, 20, -4, 30)], gc=(4, 0, 8, 100))),
 ('polaroid', 'Instant Print', 'Faded instant-photo colour: lifted blacks, green shadows, yellow highlights, lower saturation.',
  dict(sat=.86, con=-.08, tone=(22, 8, 0, -3, -6), tc=[(150, 22, 0), (45, 14, 0), (55, 26, 0)], gc=(10, -4, -2, 100))),
 ('moonlight', 'Day for Night', 'Moonlit look: strongly cool and dark with blue shadows and a desaturated palette.',
  dict(sat=.7, con=.22, tone=(-6, -4, -10, -8, -4), tc=[(235, 38, 0), (220, 20, 0), (200, 12, 0)], gc=(-46, 0, -8, 100))),
]
PRESET_INDEX = {k: (label, tip, r) for k, label, tip, r in PRESET_LOOKS}


class ManagedPreviewManager(PreviewManager):

    def __init__(self, image, drawable):
        super().__init__(image, drawable)
        self._tone_state=[]
        self._tonal_colour_state=[]
        self._hue_adjust_state=[]
        self._global_hue=0.0
        self._primaries_filter=None
        self._temperature_filter=None
        self._tint_filter=None
        self._chroma_filter=None
        self._global_sat_filter=None
        self._primaries_state={k:{'hue':0.0,'purity':0.0} for k in ('red','green','blue')}
        self._global_colour_state={'temperature':0.0,'tint':0.0,'chroma':0.0,'saturation':100.0}
        self._stage_warnings=[]
        self._colour_info=None
        self._target_base_buffer=None
        self._last_target_sig=None
        self._target_dirty=False
        self._target_error_shown=False
        self._made=[]; self._ana_root=None; self._ana_src=None; self._ana_nodes={}; self._mask_nodes={}; self._mask_used=set(); self._mask_cache_ok=False

    def _stage_warning(self, text):
        if text not in self._stage_warnings:
            self._stage_warnings.append(text)

    def colour_preflight(self):
        info={'profile':'unknown','precision':'unknown','rgb':False,'space':'GEGL source space','fallback':None}
        try:
            p=self._image.get_effective_color_profile()
            if p is not None:
                info['profile']=p.get_label() or p.get_description() or 'unnamed effective profile'
                info['rgb']=bool(p.is_rgb())
                if not info['rgb']:
                    info['fallback']='Colour Grading creative RGB stages require an RGB effective profile.'
                try:
                    sp=p.get_space(Gimp.ColorRenderingIntent.RELATIVE_COLORIMETRIC)
                    if sp is not None: info['space']='effective ICC/babl RGB space'
                except Exception:
                    info['fallback']='Effective profile found, but its babl space could not be queried.'
            else:
                info['fallback']='No effective profile was returned by GIMP.'
        except Exception as exc:
            info['fallback']='Profile preflight failed: '+str(exc)
        try: info['precision']=str(self._image.get_precision())
        except Exception: pass
        return info


    def initialise(self):
        self._colour_info=self.colour_preflight()
        if self._colour_info.get('fallback'): self._stage_warning('Colour preflight — '+self._colour_info['fallback'])
        super().initialise()
        if not self._initialised or self._preview_layer is None: return
        try:
            self._init_targeted_filters()
            self._init_primary_global_filters()
            self._display_flush()
        except Exception as exc:
            self._error_dialog(exc,'Failed to initialise grading stages')

    def discard(self):
        self._draft_ctx=None
        self._target_base_buffer=None
        self._ana_root=None; self._ana_src=None; self._ana_nodes={}; self._made=[]; self._mask_nodes={}
        super().discard()

    def _init_targeted_filters(self):
        self._last_target_sig=None; self._last_draft_sig=None; self._draft_ctx=None; self._target_dirty=False

        self._target_base_buffer=None; self._ana_root=None; self._ana_src=None; self._ana_nodes={}; self._mask_nodes={}
        try:
            buf=self._preview_layer.get_buffer() if self._preview_layer is not None else None
            if buf is None: raise RuntimeError('preview layer returned no GEGL buffer')
            self._target_base_buffer=buf.dup()
        except Exception as exc:
            self._stage_warning('Targeted renderer — could not snapshot native GEGL buffer: '+str(exc))

    TONE_L_SCALE = 0.40


    CACHE_MAX_PIXELS = 8000000
    MASK_CACHE_PIXELS = 4000000
    MASK_CACHE_KEEP = 4

    def _gop(self,root,name,**props):
        n=root.create_child(name); self._made.append(n)
        for k,v in props.items(): n.set_property(k.replace('_','-'),v)
        return n

    @staticmethod
    def _chain(a,b): a.link(b); return b

    @staticmethod
    def _aux(a,b,pad='aux'): a.connect_to('output',b,pad)

    def _ramp(self,root,node,lo,hi,reverse):
        n=self._chain(node,self._gop(root,'gegl:levels',in_low=float(lo),in_high=float(hi),
                                     out_low=1.0 if reverse else 0.0,out_high=0.0 if reverse else 1.0))
        return self._chain(n,self._gop(root,'gegl:rgb-clip'))

    def _mul(self,root,a,b):
        m=self._gop(root,'gegl:multiply'); self._chain(a,m); self._aux(b,m); return m

    def _add(self,root,a,b):
        m=self._gop(root,'gegl:add'); self._chain(a,m); self._aux(b,m); return m

    def _trapezoid(self,root,val,c,core,outer):
        up=dn=None
        if c-core>1e-6: up=self._ramp(root,val,c-outer,c-core,False)
        if c+core<1.0-1e-6: dn=self._ramp(root,val,c+core,c+outer,True)
        if up is not None and dn is not None: return self._mul(root,up,dn)
        if up is not None: return up
        if dn is not None: return dn
        return self._chain(val,self._gop(root,'gegl:levels',in_low=0.0,in_high=1.0,out_low=1.0,out_high=1.0))

    @staticmethod
    def _widths(rng,spread,max_core):
        core=max(.005,min(max_core,float(rng))); sp=max(0.0,min(1.0,float(spread)))
        return core,min(.5,core+max(.004,sp*(.5-core)))

    def _component(self,root,src,ctx,name):
        key=(id(src),name)
        if key not in ctx:
            if root is self._ana_root and src is self._ana_src:
                n=root.create_child('gegl:component-extract'); n.set_property('component',name); n.set_property('linear',True)
                src.link(n); c=root.create_child('gegl:cache'); n.link(c); self._ana_nodes[name]=c; ctx[key]=c
            else:
                ctx[key]=self._chain(src,self._gop(root,'gegl:component-extract',component=name,linear=True))
        return ctx[key]

    def _luma_mask(self,root,src,ctx,p):
        c=max(0.0,min(1.0,float(p.get('pos',.5)))); core,outer=self._widths(p.get('range',.2),p.get('spread',.5),.49)
        return self._trapezoid(root,self._component(root,src,ctx,'lab-l'),c,core,outer)

    def _hue_mask(self,root,src,ctx,p):
        c=(float(p.get('source_hue',0.0))%360.0)/360.0
        core,outer=self._widths(float(p.get('range',20.0))/360.0,p.get('spread',.5),.24)
        H=self._component(root,src,ctx,'lch-h'); acc=None
        for k in (-1,0,1):
            ck=c+k
            if ck+outer<=0.0 or ck-outer>=1.0: continue
            t=self._trapezoid(root,H,ck,core,outer); acc=t if acc is None else self._add(root,acc,t)
        gate=self._ramp(root,self._component(root,src,ctx,'lch-c'),0.015,0.075,False)
        return self._mul(root,acc,gate)

    def _adjust_chain(self,root,cur,kind,p):
        node=cur
        if kind=='tone':
            node=self._chain(node,self._gop(root,'gegl:hue-chroma',lightness=max(-100.0,min(100.0,float(p.get('brightness',0.0))))*self.TONE_L_SCALE))
        elif kind=='tonal':
            g=tint_gains(p.get('hue',0.0),p.get('strength',0.0))
            if g is not None:
                node=self._chain(node,self._gop(root,'gegl:channel-mixer',preserve_luminosity=False,
                                                rr_gain=g[0],gg_gain=g[1],bb_gain=g[2],
                                                rg_gain=0.0,rb_gain=0.0,gr_gain=0.0,gb_gain=0.0,br_gain=0.0,bg_gain=0.0))
            lum=max(-100.0,min(100.0,float(p.get('luminance',0.0))))
            if abs(lum)>1e-7: node=self._chain(node,self._gop(root,'gegl:hue-chroma',lightness=lum*self.TONE_L_SCALE))
        else:
            shift=max(-180.0,min(180.0,float(p.get('hue',0.0)))); lum=max(-100.0,min(100.0,float(p.get('luminance',0.0))))
            sat=max(-100.0,min(100.0,float(p.get('saturation',0.0))))
            if abs(shift)>1e-7 or abs(lum)>1e-7:
                node=self._chain(node,self._gop(root,'gegl:hue-chroma',hue=shift,lightness=lum*self.TONE_L_SCALE))
            if abs(sat)>1e-7:
                node=self._chain(node,self._gop(root,'gegl:saturation',scale=max(0.0,1.0+sat/100.0),colorspace='CIE-Lab'))
        return node

    def _stage_specs(self):
        specs=[]
        for p in self._tone_state:
            if abs(float(p.get('brightness',0.0)))>1e-7: specs.append(('tone',p))
        for p in self._tonal_colour_state:
            if float(p.get('strength',0.0))>1e-7 or abs(float(p.get('luminance',0.0)))>1e-7: specs.append(('tonal',p))
        for p in self._hue_adjust_state:
            if max(abs(float(p.get('hue',0.0))),abs(float(p.get('saturation',0.0))),abs(float(p.get('luminance',0.0))))>1e-7: specs.append(('hue',p))
        if abs(float(self._global_hue))>1e-7: specs.append(('ghue',{'hue':float(self._global_hue)}))
        return specs

    def _build_targeted_graph(self,root,src,specs):
        ctx={(id(src),k):v for k,v in self._ana_nodes.items()} if (root is self._ana_root and src is self._ana_src) else {}
        cur=src
        persistent=(root is self._ana_root and src is self._ana_src)
        for kind,p in specs:
            if kind=='ghue':
                cur=self._adjust_chain(root,cur,'ghue',p)
                continue
            builder=(lambda p=p:self._hue_mask(root,src,ctx,p)) if kind=='hue' else (lambda p=p:self._luma_mask(root,src,ctx,p))
            if persistent and self._mask_cache_ok:
                key=((kind,round(float(p.get('source_hue',0.0)),3),round(float(p.get('range',0.0)),4),round(float(p.get('spread',0.0)),4)) if kind=='hue'
                     else (kind,round(float(p.get('pos',.5)),4),round(float(p.get('range',0.0)),4),round(float(p.get('spread',0.0)),4)))
                hit=self._mask_nodes.get(key)
                if hit is None:
                    mark=len(self._made); m=builder(); c=root.create_child('gegl:cache'); m.link(c)
                    owned=self._made[mark:]+[c]; del self._made[mark:]; hit=self._mask_nodes[key]=(c,owned)
                self._mask_used.add(key); mask=hit[0]
            else:
                mask=builder()
            adj=self._adjust_chain(root,cur,kind,p)
            op=self._gop(root,'gegl:opacity',value=1.0); self._chain(adj,op); self._aux(mask,op)
            ov=self._gop(root,'gegl:over'); self._chain(cur,ov); self._aux(op,ov)
            cur=ov
        return cur

    @staticmethod
    def _blit_node_to_buffer(node, target, extent):
        try:
            node.blit_buffer(target,extent,0,Gegl.AbyssPolicy.NONE)
        except TypeError:
            node.blit_buffer(target,extent)

    def _contrast_params(self):
        raw=max(-1.0,min(1.0,float(self._contrast_value)))
        if abs(raw)<=1e-7: return None
        shaped=math.copysign(abs(raw)**1.55,raw)
        c=2.0**(1.25*shaped); pv=max(0.05,min(0.95,float(self._pivot_value)))
        return c,max(-1.0,min(1.0,(pv-0.5)*(1.0-c)))

    def _contrast_nodes(self,root,node):
        prm=self._contrast_params()
        if prm is None: return node
        c,br=prm
        node=self._chain(node,self._gop(root,'gegl:gamma',value=1.0/2.2))
        node=self._chain(node,self._gop(root,'gegl:brightness-contrast',contrast=c,brightness=br))
        node=self._chain(node,self._gop(root,'gegl:rgb-clip'))
        return self._chain(node,self._gop(root,'gegl:gamma',value=2.2))

    def set_contrast(self,contrast,pivot):
        self._contrast_value=float(contrast); self._pivot_value=float(pivot)

    def update_contrast(self,contrast,pivot):
        self.set_contrast(contrast,pivot)
        if self._initialised and not self._batch_depth: self._render_targeted()

    def _target_signature(self):
        try:
            return json.dumps([self._tone_state,self._tonal_colour_state,self._hue_adjust_state,self._global_hue,self._contrast_params()],sort_keys=True)
        except Exception:
            return None

    DRAFT_MIN_PIXELS = 400000
    _CTX_KEYS = ('_target_base_buffer','_ana_root','_ana_src','_ana_nodes','_mask_nodes')

    def _draft_context(self,extent):
        ctx=getattr(self,'_draft_ctx',None)
        if ctx is not None: return ctx
        px=extent.width*extent.height
        if px<self.DRAFT_MIN_PIXELS: return None
        f=0.75 if px<=2500000 else 0.4
        w2=max(1,int(math.ceil(extent.width*f))); h2=max(1,int(math.ceil(extent.height*f)))
        base=self._target_base_buffer
        dst=Gegl.Buffer.new("R'G'B'A float",0,0,w2,h2)
        r=Gegl.Node.new(); a=r.create_child('gegl:buffer-source'); a.set_property('buffer',base)
        b=r.create_child('gegl:scale-ratio'); b.set_property('x',w2/float(extent.width)); b.set_property('y',h2/float(extent.height)); b.set_property('sampler','linear'); a.link(b)
        self._blit_node_to_buffer(b,dst,Gegl.Rectangle.new(0,0,w2,h2))
        ctx={'_target_base_buffer':dst,'_ana_root':None,'_ana_src':None,'_ana_nodes':{},'_mask_nodes':{},
             'sx':extent.width/float(w2),'sy':extent.height/float(h2)}
        self._draft_ctx=ctx
        return ctx

    def _swap_ctx(self,ctx):
        for k in self._CTX_KEYS:
            cur=getattr(self,k); setattr(self,k,ctx[k]); ctx[k]=cur

    def _render_targeted(self,draft=False):
        if not self._initialised or self._preview_layer is None or self._target_base_buffer is None:
            return
        ctx=None
        if draft:
            try: ctx=self._draft_context(self._preview_layer.get_buffer().get_extent())
            except Exception as exc:
                _dbg('draft unavailable: %r'%(exc,)); ctx=None
        if ctx is None:
            return self._render_core(False,None)
        self._swap_ctx(ctx)
        try: self._render_core(True,ctx)
        finally: self._swap_ctx(ctx)

    def _render_core(self,draft,ctx):
        sig=self._target_signature()
        if sig is not None and (sig==self._last_target_sig or (draft and sig==getattr(self,'_last_draft_sig',None))):
            return
        specs=self._stage_specs(); has_con=self._contrast_params() is not None
        if not specs and not has_con and not self._target_dirty:
            self._last_target_sig=sig; return
        t0=time.monotonic()
        try:
            target=self._preview_layer.get_buffer()
            if target is None: raise RuntimeError('preview layer returned no writable GEGL buffer')
            extent=target.get_extent()
            be=self._target_base_buffer.get_extent()
            persist=(be.width*be.height)<=self.CACHE_MAX_PIXELS
            if persist:
                if self._ana_root is None:
                    self._ana_root=Gegl.Node.new(); self._ana_src=self._ana_root.create_child('gegl:buffer-source')
                    self._ana_src.set_property('buffer',self._target_base_buffer); self._ana_nodes={}
                root,src=self._ana_root,self._ana_src
            else:
                root=Gegl.Node.new(); src=root.create_child('gegl:buffer-source'); src.set_property('buffer',self._target_base_buffer)
            self._made=[]; self._mask_used=set(); self._mask_cache_ok=persist and (be.width*be.height)<=self.MASK_CACHE_PIXELS
            try:
                current=self._build_targeted_graph(root,src,specs) if specs else src
                current=self._contrast_nodes(root,current)
                if draft: current=self._chain(current,self._gop(root,'gegl:scale-ratio',x=ctx['sx'],y=ctx['sy'],sampler='nearest'))
                self._blit_node_to_buffer(current,target,extent)
            finally:
                if persist:
                    for n in self._made:
                        try: root.remove_child(n)
                        except Exception: pass
                self._made=[]
                if persist:
                    stale=[k for k in self._mask_nodes if k not in self._mask_used]
                    excess=len(self._mask_nodes)-self.MASK_CACHE_KEEP
                    for k in stale[:max(0,excess)]:
                        for n in self._mask_nodes.pop(k)[1]:
                            try: root.remove_child(n)
                            except Exception: pass
            target.flush()
            try: self._preview_layer.update(0,0,self._preview_layer.get_width(),self._preview_layer.get_height())
            except Exception: pass
            self._last_target_sig=None if draft else sig; self._last_draft_sig=sig if draft else None
            self._target_dirty=bool(specs) or has_con
            _dbg('targeted %s render %.0f ms (%d stage(s))'%('draft' if draft else 'full',(time.monotonic()-t0)*1000,len(specs)))
            self._last_target_error=None
        except Exception as exc:
            self._last_target_sig=None; self._last_target_error=str(exc)
            self._stage_warning('Targeted renderer — '+str(exc))
            if not self._target_error_shown:
                self._target_error_shown=True
                try: Gimp.message('Colour Grading targeted preview failed safely: '+str(exc))
                except Exception: pass
        self._display_flush()

    def update_targeted(self,tone_state,tonal_colour_state,hue_state,draft=False,global_hue=None):
        if global_hue is not None: self._global_hue=float(global_hue)
        self._tone_state=[dict(x) for x in tone_state]
        self._tonal_colour_state=[dict(x) for x in tonal_colour_state]
        self._hue_adjust_state=[dict(x) for x in hue_state]
        self._render_targeted(draft)


    def _init_primary_global_filters(self):
        try:
            f=Gimp.DrawableFilter.new(self._preview_layer,'gegl:channel-mixer','RGB Primaries')
            if f is not None:
                cfg=f.get_config(); cfg.set_property('preserve-luminosity',True)
                for prop,val in (('rr-gain',1.0),('rg-gain',0.0),('rb-gain',0.0),('gr-gain',0.0),('gg-gain',1.0),('gb-gain',0.0),('br-gain',0.0),('bg-gain',0.0),('bb-gain',1.0)): cfg.set_property(prop,val)
                f.update(); f.set_visible(False); self._preview_layer.append_filter(f); self._primaries_filter=f
            else: self._stage_warning('RGB Primaries — gegl:channel-mixer unavailable')
        except Exception as exc: self._primaries_filter=None; self._stage_warning('RGB Primaries — gegl:channel-mixer failed: '+str(exc))
        try:
            f=Gimp.DrawableFilter.new(self._preview_layer,'gegl:color-temperature','Temperature')
            if f is not None:
                cfg=f.get_config(); cfg.set_property('original-temperature',6500.0); cfg.set_property('intended-temperature',6500.0); f.update(); f.set_visible(False); self._preview_layer.append_filter(f); self._temperature_filter=f
            else: self._stage_warning('Temperature — gegl:color-temperature unavailable')
        except Exception as exc: self._temperature_filter=None; self._stage_warning('Temperature — gegl:color-temperature failed: '+str(exc))
        try:
            f=Gimp.DrawableFilter.new(self._preview_layer,'gegl:channel-mixer','Global Tint')
            if f is not None:
                cfg=f.get_config(); cfg.set_property('preserve-luminosity',False)
                for prop,val in (('rr-gain',1.0),('rg-gain',0.0),('rb-gain',0.0),('gr-gain',0.0),('gg-gain',1.0),('gb-gain',0.0),('br-gain',0.0),('bg-gain',0.0),('bb-gain',1.0)): cfg.set_property(prop,val)
                f.update(); f.set_visible(False); self._preview_layer.append_filter(f); self._tint_filter=f
            else: self._stage_warning('Global Tint — gegl:channel-mixer unavailable')
        except Exception as exc: self._tint_filter=None; self._stage_warning('Global Tint — gegl:channel-mixer failed: '+str(exc))
        try:
            f=Gimp.DrawableFilter.new(self._preview_layer,'gegl:hue-chroma','Global Chroma')
            if f is not None:
                cfg=f.get_config(); cfg.set_property('hue',0.0); cfg.set_property('chroma',0.0); cfg.set_property('lightness',0.0); f.update(); f.set_visible(False); self._preview_layer.append_filter(f); self._chroma_filter=f
            else: self._stage_warning('Global Chroma — gegl:hue-chroma unavailable')
        except Exception as exc: self._chroma_filter=None; self._stage_warning('Global Chroma — gegl:hue-chroma failed: '+str(exc))
        try:
            f=Gimp.DrawableFilter.new(self._preview_layer,'gegl:saturation','Global Saturation')
            if f is not None:
                cfg=f.get_config(); cfg.set_property('scale',1.0)
                _use_lab_saturation(cfg)
                f.update(); f.set_visible(False); self._preview_layer.append_filter(f); self._global_sat_filter=f
            else: self._stage_warning('Global Saturation — gegl:saturation unavailable')
        except Exception as exc: self._global_sat_filter=None; self._stage_warning('Global Saturation — gegl:saturation failed: '+str(exc))

    @staticmethod
    def _primary_matrix(state):
        m=[[1.0,0.0,0.0],[0.0,1.0,0.0],[0.0,0.0,1.0]]
        for j,key in enumerate(('red','green','blue')):

            hue_deg=max(-180.0,min(180.0,float(state.get(key,{}).get('hue',0.0))))
            radius=max(0.0,min(100.0,float(state.get(key,{}).get('purity',0.0))))/100.0
            strength=radius**1.05
            purity=strength

            home=1.0+0.90*purity
            side=-0.45*purity
            col=[side,side,side]; col[j]=home

            hue=(hue_deg/180.0)*strength
            amount=0.60*hue
            nxt=(j+1)%3; prv=(j-1)%3
            col[nxt]+=amount; col[prv]-=amount

            angle_pressure=max(0.0,(abs(hue_deg)-55.0)/125.0)
            rim_pressure=max(0.0,(radius-0.68)/0.32)
            guard=max(0.0,min(1.0,angle_pressure*rim_pressure))

            guard=guard*guard*(3.0-2.0*guard)
            if guard>0.0:
                safe=[]
                for v in col:
                    if v < 0.0:

                        sv=-0.11*math.tanh((-v)/0.11)
                    else:
                        sv=min(v,1.35)
                    safe.append(sv)
                total=sum(safe)
                if abs(total)>1e-6:
                    safe=[v/total for v in safe]
                col=[col[i]*(1.0-guard)+safe[i]*guard for i in range(3)]

            for i in range(3): m[i][j]=max(-2.0,min(2.0,col[i]))
        return m

    def update_primaries(self,state):
        self._primaries_state={k:{'hue':float(state.get(k,{}).get('hue',0.0)),'purity':float(state.get(k,{}).get('purity',0.0))} for k in ('red','green','blue')}
        f=self._primaries_filter
        if f is None:return
        try:
            m=self._primary_matrix(self._primaries_state); cfg=f.get_config()
            props=(('rr-gain',m[0][0]),('rg-gain',m[0][1]),('rb-gain',m[0][2]),('gr-gain',m[1][0]),('gg-gain',m[1][1]),('gb-gain',m[1][2]),('br-gain',m[2][0]),('bg-gain',m[2][1]),('bb-gain',m[2][2]))
            for prop,val in props: cfg.set_property(prop,val)
            active=any(abs(float(self._primaries_state[k][p])) > 1e-7 for k in ('red','green','blue') for p in ('hue','purity'))
            self._set_filter_visible(f, active)
            self._upd(f); self._display_flush()
        except Exception as exc:self._error_dialog(exc,'Failed to update RGB Primaries')

    @staticmethod
    def _kelvin_pair(t):
        base=1e6/6500.0; t=max(-100.0,min(100.0,float(t)))
        shift=t*1.5 if t>0 else t*2.0
        orig=base; inten=base-shift
        if inten<1e6/12000.0:
            extra=1e6/12000.0-inten; orig+=extra; inten+=extra
        elif inten>1e6/2000.0:
            extra=inten-1e6/2000.0; orig-=extra; inten-=extra
        return 1e6/max(orig,1e6/12000.0),1e6/max(inten,1e6/12000.0)

    def update_global_colour(self,state):
        self._global_colour_state={
            'temperature':float(state.get('temperature',0.0)), 'tint':float(state.get('tint',0.0)),
            'chroma':float(state.get('chroma',0.0)), 'saturation':float(state.get('saturation',100.0))}
        try:
            t=max(-100.0,min(100.0,self._global_colour_state['temperature']))
            if self._temperature_filter is not None:
                k_orig,k_int=self._kelvin_pair(t); cfg=self._temperature_filter.get_config()
                cfg.set_property('original-temperature',k_orig); cfg.set_property('intended-temperature',k_int)
                self._set_filter_visible(self._temperature_filter,abs(t)>1e-7); self._upd(self._temperature_filter)
            if self._tint_filter is not None:
                tint=max(-100.0,min(100.0,self._global_colour_state['tint']))/100.0
                shaped=math.copysign(0.40*abs(tint)+0.60*(abs(tint)**2.0),tint) if tint else 0.0
                cfg=self._tint_filter.get_config(); k=shaped*.70
                vals=(('rr-gain',1.0+k),('rg-gain',0.0),('rb-gain',0.0),
                      ('gr-gain',0.0),('gg-gain',1.0-k),('gb-gain',0.0),
                      ('br-gain',0.0),('bg-gain',0.0),('bb-gain',1.0+k))
                for prop,val in vals: cfg.set_property(prop,val)
                self._set_filter_visible(self._tint_filter,abs(tint)>1e-7); self._upd(self._tint_filter)
            if self._chroma_filter is not None:
                cfg=self._chroma_filter.get_config(); ch=max(-100.0,min(100.0,self._global_colour_state['chroma']))*0.60
                cfg.set_property('chroma',ch); self._set_filter_visible(self._chroma_filter,abs(ch)>1e-7); self._upd(self._chroma_filter)
            if self._global_sat_filter is not None:
                s=max(0.0,min(200.0,self._global_colour_state['saturation']))
                scale=s/100.0 if s<=100.0 else 1.0+(s-100.0)/100.0*0.70
                cfg=self._global_sat_filter.get_config(); cfg.set_property('scale',scale)
                self._set_filter_visible(self._global_sat_filter,abs(scale-1.0)>1e-7); self._upd(self._global_sat_filter)
            self._display_flush()
        except Exception as exc:self._error_dialog(exc,'Failed to update Global Colour')


    def has_active_grade(self, eps=1e-7):
        if any(any(abs(v)>eps for v in self._band_wheel.get(b,(0,0,0))) for b in BANDS): return True
        if any(abs(v)>eps for v in self._master_rgb): return True
        if abs(self._sat_value-1.0)>eps or abs(self._contrast_value)>eps: return True
        if any(abs(float(p.get('brightness',0)))>eps for p in self._tone_state): return True
        if any(float(p.get('strength',0))>eps for p in self._tonal_colour_state): return True
        if any(max(abs(float(p.get('hue',0))),abs(float(p.get('saturation',0))),abs(float(p.get('luminance',0))))>eps for p in self._hue_adjust_state): return True
        if abs(float(self._global_hue))>eps: return True
        if any(abs(float(self._primaries_state[k][p]))>eps for k in ('red','green','blue') for p in ('hue','purity')): return True
        gc=self._global_colour_state
        return (abs(gc.get('temperature',0.0))>eps or abs(gc.get('tint',0.0))>eps or
                abs(gc.get('chroma',0.0))>eps or abs(gc.get('saturation',100.0)-100.0)>eps)


try:
    import numpy as _np
except Exception:
    _np = None

LUT_EXTS = ('.cube', '.png')

def _lut_dict(size, rows, dmin, dmax, path):
    return {'size': size, 'rows': rows, 'dmin': tuple(dmin), 'dmax': tuple(dmax), 'path': path,
            'name': os.path.splitext(os.path.basename(path))[0]}

def parse_cube(path):
    size = None
    dmin, dmax = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
    rows = []
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for raw in f:
            line = raw.strip()
            if not line or line[0] == '#': continue
            up = line.upper()
            if up.startswith('TITLE'): continue
            if up.startswith('LUT_3D_SIZE'): size = int(line.split()[1]); continue
            if up.startswith('LUT_1D_SIZE'): raise ValueError('This is a 1D LUT. Only 3D .cube LUTs are supported.')
            if up.startswith('DOMAIN_MIN'): dmin = tuple(map(float, line.split()[1:4])); continue
            if up.startswith('DOMAIN_MAX'): dmax = tuple(map(float, line.split()[1:4])); continue
            parts = line.split()
            if len(parts) == 3: rows.append((float(parts[0]), float(parts[1]), float(parts[2])))
    if not size or size < 2: raise ValueError('This .cube file has no valid LUT_3D_SIZE.')
    if len(rows) != size ** 3: raise ValueError('Expected %d colour rows for a %d-point LUT, found %d.' % (size ** 3, size, len(rows)))
    for a, b in zip(dmin, dmax):
        if b <= a: raise ValueError('This .cube file has an invalid domain.')
    return _lut_dict(size, rows, dmin, dmax, path)

def parse_hald(path):
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new_from_file(path)
    w, h = pb.get_width(), pb.get_height()
    level = int(round(w ** (1.0 / 3.0)))
    if w != h or level ** 3 != w or level < 2:
        raise ValueError('This picture is not a HaldCLUT (it must be square, with a side of 8, 27, 64, 125, 216, 343 or 512 pixels, and so on).')
    ch, rs = pb.get_n_channels(), pb.get_rowstride()
    data = bytes(pb.get_pixels())
    size = level * level
    rows = []
    for y in range(h):
        o = y * rs
        for x in range(w):
            q = o + x * ch
            rows.append((data[q] / 255.0, data[q + 1] / 255.0, data[q + 2] / 255.0))
    return _lut_dict(size, rows, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), path)

def load_lut(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.cube': return parse_cube(path)
    if ext == '.png': return parse_hald(path)
    raise ValueError('Unsupported LUT type: ' + ext)

def is_hald_png(path):
    try:
        from gi.repository import GdkPixbuf
        info = GdkPixbuf.Pixbuf.get_file_info(path)
        w, h = info[1], info[2]
        level = int(round(w ** (1.0 / 3.0)))
        return w == h and level ** 3 == w and level >= 2
    except Exception:
        return False

def scan_luts(folder, limit=400):
    out = []
    if not folder or not os.path.isdir(folder): return out
    base_depth = folder.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(folder):
        if root.count(os.sep) - base_depth >= 3:
            dirs[:] = []
        dirs.sort(key=str.casefold)
        for fn in sorted(files, key=str.casefold):
            ext = os.path.splitext(fn)[1].lower()
            if ext in LUT_EXTS:
                p = os.path.join(root, fn)
                if ext == '.png' and not is_hald_png(p): continue
                out.append(p)
                if len(out) >= limit: return out
    return out

def _apply_numpy(raw, lut, progress):
    np = _np
    px = np.frombuffer(raw, dtype=np.float32).reshape(-1, 4).copy()
    n = lut['size']
    table = np.asarray(lut['rows'], dtype=np.float32).reshape(n, n, n, 3)
    dmin = np.asarray(lut['dmin'], dtype=np.float32)
    span = np.asarray(lut['dmax'], dtype=np.float32) - dmin
    step = 1 << 20
    total = px.shape[0]
    for s in range(0, total, step):
        c = px[s:s + step, :3]
        x = np.clip((c - dmin) / span, 0.0, 1.0) * (n - 1)
        i0 = np.minimum(np.floor(x).astype(np.int32), n - 2)
        f = x - i0
        r0, g0, b0 = i0[:, 0], i0[:, 1], i0[:, 2]
        fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]
        def t(db, dg, dr): return table[b0 + db, g0 + dg, r0 + dr]
        c00 = t(0, 0, 0) * (1 - fr) + t(0, 0, 1) * fr
        c10 = t(0, 1, 0) * (1 - fr) + t(0, 1, 1) * fr
        c01 = t(1, 0, 0) * (1 - fr) + t(1, 0, 1) * fr
        c11 = t(1, 1, 0) * (1 - fr) + t(1, 1, 1) * fr
        c0 = c00 * (1 - fg) + c10 * fg
        c1 = c01 * (1 - fg) + c11 * fg
        px[s:s + step, :3] = c0 * (1 - fb) + c1 * fb
        if progress: progress(min(1.0, (s + step) / float(total)))
    return px.tobytes()

def _apply_python(raw, lut, progress):
    from array import array
    vals = array('f')
    vals.frombytes(raw)
    n = lut['size']
    nn = n * n
    tbl = [v for row in lut['rows'] for v in row]
    d0, d1, d2 = lut['dmin']
    s0 = (n - 1) / (lut['dmax'][0] - d0)
    s1 = (n - 1) / (lut['dmax'][1] - d1)
    s2 = (n - 1) / (lut['dmax'][2] - d2)
    top = n - 1
    cnt = len(vals) // 4
    report = max(1, cnt // 50)
    for p in range(cnt):
        o = p * 4
        x = (vals[o] - d0) * s0
        y = (vals[o + 1] - d1) * s1
        z = (vals[o + 2] - d2) * s2
        x = 0.0 if x < 0.0 else (top if x > top else x)
        y = 0.0 if y < 0.0 else (top if y > top else y)
        z = 0.0 if z < 0.0 else (top if z > top else z)
        ri = int(x); gi = int(y); bi = int(z)
        if ri > n - 2: ri = n - 2
        if gi > n - 2: gi = n - 2
        if bi > n - 2: bi = n - 2
        fr = x - ri; fg = y - gi; fb = z - bi
        q = (bi * nn + gi * n + ri) * 3
        a = q + 3 * n
        b = q + 3 * nn
        c = b + 3 * n
        for k in range(3):
            c00 = tbl[q + k] + (tbl[q + 3 + k] - tbl[q + k]) * fr
            c10 = tbl[a + k] + (tbl[a + 3 + k] - tbl[a + k]) * fr
            c01 = tbl[b + k] + (tbl[b + 3 + k] - tbl[b + k]) * fr
            c11 = tbl[c + k] + (tbl[c + 3 + k] - tbl[c + k]) * fr
            c0 = c00 + (c10 - c00) * fg
            c1 = c01 + (c11 - c01) * fg
            vals[o + k] = c0 + (c1 - c0) * fb
        if progress and p % report == 0: progress(p / float(cnt))
    return vals.tobytes()

def blend_raw(ra, rb, k):
    if _np is not None:
        a = _np.frombuffer(ra, dtype=_np.float32)
        b = _np.frombuffer(rb, dtype=_np.float32)
        return (a + (b - a) * _np.float32(k)).astype(_np.float32).tobytes()
    from array import array
    a = array('f'); a.frombytes(ra)
    b = array('f'); b.frombytes(rb)
    for i in range(len(a)): a[i] += (b[i] - a[i]) * k
    return a.tobytes()

def apply_lut_raw(raw, lut, progress=None):
    if _np is not None: return _apply_numpy(raw, lut, progress)
    return _apply_python(raw, lut, progress)

class LutPreviewManager(ManagedPreviewManager):

    def __init__(self, image, drawable):
        super().__init__(image, drawable)
        self._orig_base = None
        self._lut = None
        self._lut_full = None
        self._lut_strength = 1.0

    def _init_targeted_filters(self):
        super()._init_targeted_filters()
        self._orig_base = self._target_base_buffer
        self._lut = None
        self._lut_full = None

    def discard(self):
        self._orig_base = None
        self._lut_full = None
        self._lut = None
        super().discard()

    def has_active_grade(self, eps=1e-7):
        if self._lut is not None and self._lut_strength > eps: return True
        return super().has_active_grade(eps)

    def _lut_make(self, lut, progress):
        ext = self._orig_base.get_extent()
        rect = Gegl.Rectangle.new(ext.x, ext.y, ext.width, ext.height)
        raw = self._orig_base.get(rect, 1.0, "R'G'B'A float", Gegl.AbyssPolicy.NONE)
        out = apply_lut_raw(bytes(raw), lut, progress)
        dst = Gegl.Buffer.new("R'G'B'A float", ext.x, ext.y, ext.width, ext.height)
        dst.set(rect, "R'G'B'A float", out)
        return dst

    def _lut_blend(self):
        k = self._lut_strength
        if self._lut is None or k <= 0.001:
            self._target_base_buffer = self._orig_base
        elif k >= 0.999:
            self._target_base_buffer = self._lut_full
        else:
            ext = self._orig_base.get_extent()
            rect = Gegl.Rectangle.new(ext.x, ext.y, ext.width, ext.height)
            ra = bytes(self._orig_base.get(rect, 1.0, "R'G'B'A float", Gegl.AbyssPolicy.NONE))
            rb = bytes(self._lut_full.get(rect, 1.0, "R'G'B'A float", Gegl.AbyssPolicy.NONE))
            dst = Gegl.Buffer.new("R'G'B'A float", ext.x, ext.y, ext.width, ext.height)
            dst.set(rect, "R'G'B'A float", blend_raw(ra, rb, k))
            self._target_base_buffer = dst

    def set_lut(self, lut, strength, progress=None):
        if self._orig_base is None or not self._initialised:
            raise RuntimeError('The picture is not ready yet.')
        if lut is None:
            self._lut = None
            self._lut_full = None
        elif lut is not self._lut:
            self._lut_full = self._lut_make(lut, progress)
            self._lut = lut
        self._lut_strength = float(strength)
        self._lut_blend()
        self._draft_ctx = None
        self._ana_root = None; self._ana_src = None; self._ana_nodes = {}; self._mask_nodes = {}; self._made = []
        self._last_target_sig = None; self._last_draft_sig = None; self._target_dirty = True
        self._render_targeted()
        self._display_flush()


_ACCENT = (0.29, 0.64, 1.0)

_CSS = b"""
.cg-dialog { background-color: #2b2b2b; color: #d9d9d9; }
.cg-dialog label { color: #d9d9d9; }
.cg-dialog notebook > header { background-color: #232323; padding: 0; border: none; }
.cg-dialog notebook > header > tabs > tab { padding: 6px 10px; margin: 0 1px; min-width: 0; background-color: #303030; background-image: none; color: #a8a8a8; border: none; border-radius: 6px 6px 0 0; }
.cg-dialog notebook > header > tabs > tab:checked { background-color: #2a2a2a; color: #ffffff; box-shadow: inset 0 2px 0 #4aa3ff; }
.cg-dialog notebook > stack { background-color: #2a2a2a; }
.cg-dialog scrolledwindow { background-color: #2a2a2a; }
.cg-card { background-color: #353535; border-radius: 9px; }
.cg-sec { font-size: 10px; letter-spacing: 1px; font-weight: bold; color: #a9a9a9; }
.cg-hint { font-size: 11px; color: #9a9a9a; }
.cg-name { font-weight: bold; color: #ffffff; }
.cg-name-dim { font-weight: bold; color: #8c8c8c; }
.cg-wname { font-weight: bold; color: #ffffff; padding: 2px 12px; border: 1px solid transparent; border-radius: 6px; }
.cg-wname-dim { font-weight: bold; color: #8c8c8c; padding: 2px 12px; border: 1px solid transparent; border-radius: 6px; }
.cg-name-glow { font-weight: bold; color: #ffffff; padding: 2px 12px; border: 1px solid #4aa3ff; border-radius: 6px; background-color: rgba(74, 163, 255, 0.20); box-shadow: 0 0 6px 1px #4aa3ff, inset 0 0 6px rgba(74, 163, 255, 0.45); }
.cg-read { font-size: 11px; color: #b9b9b9; }
.cg-dialog button { background-image: none; background-color: #3d3d3d; color: #e6e6e6; border: 1px solid #555555; border-radius: 6px; padding: 4px 12px; min-height: 0; box-shadow: none; text-shadow: none; }
.cg-dialog button:hover { background-color: #494949; }
.cg-dialog button.cg-chip { padding: 2px 10px; border-radius: 12px; background-color: #2a2a2a; border-color: #4a4a4a; color: #c8c8c8; font-size: 11px; }
.cg-dialog button.cg-chip:hover { background-color: #383838; }
.cg-dialog button.cg-on, .cg-dialog button.cg-chip.cg-on { background-color: #4aa3ff; border-color: #4aa3ff; color: #0b1b2e; font-weight: bold; }
.cg-dialog button.cg-apply { background-color: #4aa3ff; border-color: #4aa3ff; color: #0b1b2e; font-weight: bold; padding: 5px 20px; }
.cg-dialog button.cg-apply:hover { background-color: #6bb5ff; }
.cg-dialog button.cg-card-btn { padding: 4px; border-radius: 8px; background-color: #2f2f2f; border-color: #484848; }
.cg-dialog button.cg-card-btn.cg-on { background-color: #22324a; border: 2px solid #4aa3ff; color: #ffffff; }
.cg-dialog entry { background-color: #262626; color: #e0e0e0; border: 1px solid #4a4a4a; border-radius: 6px; min-height: 0; padding: 3px 8px; }
.cg-dialog checkbutton label { color: #d9d9d9; }
.cg-foot { background-color: #2b2b2b; border-top: 1px solid #1d1d1d; }
.cg-ok { color: #8fd18f; }
.cg-warn { color: #e8a34a; }
.cg-bad { color: #e87a6a; }
"""

def _apply_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

def _rrect(cr, x, y, w, h, r):
    r = min(r, w / 2.0, h / 2.0)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()

def _text(cr, s, x, y, size=11, rgba=(0.85, 0.85, 0.85, 1.0), align='l', bold=False):
    cr.select_font_face('Sans', cairo.FONT_SLANT_NORMAL,
                        cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    ext = cr.text_extents(s)
    w = ext.width
    if align == 'r': x -= w
    elif align == 'c': x -= w / 2.0
    cr.set_source_rgba(*rgba)
    cr.move_to(x, y)
    cr.show_text(s)
    cr.new_path()
    return w

def _hsv(h, s=1.0, v=1.0):
    return colorsys.hsv_to_rgb((h % 360.0) / 360.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))

def _hbox(spacing=6):
    return Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)

def _vbox(spacing=6):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)

def _sec(text):
    lbl = Gtk.Label(label=text.upper())
    lbl.get_style_context().add_class('cg-sec')
    lbl.set_halign(Gtk.Align.START)
    return lbl

def _hint(text):
    lbl = Gtk.Label(label=text)
    lbl.get_style_context().add_class('cg-hint')
    lbl.set_xalign(0)
    lbl.set_line_wrap(True)
    lbl.set_max_width_chars(60)
    return lbl

def _card(child=None, pad=10):
    box = _vbox(6)
    box.get_style_context().add_class('cg-card')
    box.set_border_width(pad)
    if child is not None:
        box.pack_start(child, False, False, 0)
    return box

def _chip(text, tip=None, cb=None):
    b = Gtk.Button(label=text)
    b.get_style_context().add_class('cg-chip')
    if tip: b.set_tooltip_text(tip)
    if cb: b.connect('clicked', cb)
    return b

def _set_on(widget, on):
    ctx = widget.get_style_context()
    if on: ctx.add_class('cg-on')
    else: ctx.remove_class('cg-on')

class Segmented(Gtk.Box):

    __gsignals__ = {'chosen': (GObject.SignalFlags.RUN_LAST, None, (int,))}

    def __init__(self, labels, tips=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.buttons = []
        for i, text in enumerate(labels):
            b = Gtk.Button(label=text)
            b.get_style_context().add_class('cg-chip')
            if tips: b.set_tooltip_text(tips[i])
            b.connect('clicked', self._clicked, i)
            self.pack_start(b, False, False, 2)
            self.buttons.append(b)
        self.set_active(0)

    def set_active(self, i):
        self.active = i
        for j, b in enumerate(self.buttons):
            _set_on(b, j == i)

    def _clicked(self, _b, i):
        self.set_active(i)
        self.emit('chosen', i)

class GradSlider(Gtk.DrawingArea):

    __gsignals__ = {
        'value-changed': (GObject.SignalFlags.RUN_LAST, None, (float,)),
        'released': (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, label, lo, hi, default, fmt='{:.0f}', stops=None, tip=None, step=1.0, ends=None):
        super().__init__()
        self.label = label
        self.lo, self.hi = float(lo), float(hi)
        self.default = float(default)
        self.value = float(default)
        self.fmt = fmt
        self.stops = stops
        self.step = float(step)
        self.ends = ends
        self._drag = False
        self.set_size_request(-1, 40 if not ends else 46)
        self.set_hexpand(True)
        self.set_can_focus(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.BUTTON1_MOTION_MASK | Gdk.EventMask.SCROLL_MASK |
                        Gdk.EventMask.KEY_PRESS_MASK)
        self.connect('draw', self._draw)
        self.connect('button-press-event', self._press)
        self.connect('button-release-event', self._release)
        self.connect('motion-notify-event', self._motion)
        self.connect('scroll-event', self._scroll)
        self.connect('key-press-event', self._key)
        if tip: self.set_tooltip_text(tip + '\nDouble-click to reset.')

    def set_value_quiet(self, v):
        self.value = max(self.lo, min(self.hi, float(v)))
        self.queue_draw()

    def set_stops(self, stops):
        self.stops = stops
        self.queue_draw()

    def _x(self, v):
        w = self.get_allocated_width()
        return 11 + (w - 22) * (v - self.lo) / (self.hi - self.lo)

    def _draw(self, _w, cr):
        w = self.get_allocated_width()
        y0 = 21
        _text(cr, self.label, 11, 13, 11, (0.82, 0.82, 0.82, 1))
        _text(cr, self.fmt.format(self.value), w - 11, 13, 11, (1, 1, 1, 1), 'r', True)
        _rrect(cr, 11, y0, w - 22, 9, 4.5)
        if self.stops:
            g = cairo.LinearGradient(11, 0, w - 11, 0)
            n = len(self.stops)
            for i, c in enumerate(self.stops):
                g.add_color_stop_rgb(i / float(max(1, n - 1)), *c[:3])
            cr.set_source(g)
        else:
            cr.set_source_rgb(0.11, 0.11, 0.11)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.set_line_width(1)
        cr.stroke()
        if not self.stops:
            a = self._x(self.default if self.lo < self.default < self.hi else self.lo)
            b = self._x(self.value)
            if abs(b - a) > 0.5:
                cr.set_source_rgb(*_ACCENT)
                _rrect(cr, min(a, b), y0 + 1, abs(b - a), 7, 3.5)
                cr.fill()
        if self.lo < self.default < self.hi:
            dx = self._x(self.default)
            cr.set_source_rgba(1, 1, 1, 0.55)
            cr.set_line_width(1)
            cr.move_to(dx, y0 - 3); cr.line_to(dx, y0 + 12); cr.stroke()
        kx = self._x(self.value)
        cr.arc(kx, y0 + 4.5, 7.5, 0, 2 * math.pi)
        cr.set_source_rgba(0, 0, 0, 0.6); cr.fill_preserve()
        cr.set_source_rgb(1, 1, 1); cr.set_line_width(2.2); cr.stroke()
        if self.has_focus() or self._drag:
            cr.arc(kx, y0 + 4.5, 10, 0, 2 * math.pi)
            cr.set_source_rgba(*_ACCENT, 0.9); cr.set_line_width(1.4); cr.stroke()
        if self.ends:
            _text(cr, self.ends[0], 11, 43, 10, (0.7, 0.7, 0.7, 1))
            _text(cr, self.ends[1], w - 11, 43, 10, (0.7, 0.7, 0.7, 1), 'r')
        return False

    def _from_x(self, x):
        w = self.get_allocated_width()
        t = max(0.0, min(1.0, (x - 11) / float(max(1, w - 22))))
        v = self.lo + t * (self.hi - self.lo)
        if self.step > 0: v = round(v / self.step) * self.step
        self._set(v)

    def _set(self, v):
        v = max(self.lo, min(self.hi, float(v)))
        if abs(v - self.value) > 1e-9:
            self.value = v
            self.queue_draw()
            self.emit('value-changed', v)

    def _press(self, _w, e):
        self.grab_focus()
        if e.button != 1: return False
        if e.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self._drag = False
            self._set(self.default)
            self.emit('released')
            return True
        if e.type == Gdk.EventType.BUTTON_PRESS:
            self._drag = True
            self._from_x(e.x)
        return True

    def _motion(self, _w, e):
        if self._drag: self._from_x(e.x)
        return True

    def _release(self, _w, e):
        if self._drag:
            self._drag = False
            self.queue_draw()
            self.emit('released')
        return True

    def _scroll(self, _w, e):
        d = 1 if e.direction == Gdk.ScrollDirection.UP else (-1 if e.direction == Gdk.ScrollDirection.DOWN else 0)
        if d:
            self._set(self.value + d * self.step * (5 if self.step < 1 and self.hi - self.lo > 5 else 1))
            self.emit('released')
        return True

    def _key(self, _w, e):
        k = e.keyval
        big = 10 if e.state & Gdk.ModifierType.SHIFT_MASK else 1
        if k in (Gdk.KEY_Left, Gdk.KEY_Down): self._set(self.value - self.step * big)
        elif k in (Gdk.KEY_Right, Gdk.KEY_Up): self._set(self.value + self.step * big)
        elif k == Gdk.KEY_Home: self._set(self.default)
        else: return False
        self.emit('released')
        return True

_DISC_CACHE = {}

def _disc_surface(size, kind, base):
    key = (size, kind, base if kind == 'shift' else 0)
    surf = _DISC_CACHE.get(key)
    if surf is not None: return surf
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    stride = surf.get_stride()
    buf = surf.get_data()
    R = size / 2.0 - 1.0
    c = size / 2.0
    for y in range(size):
        dy = y + 0.5 - c
        for x in range(size):
            dx = x + 0.5 - c
            d = math.hypot(dx, dy)
            cov = max(0.0, min(1.0, R + 0.5 - d))
            if cov <= 0.0: continue
            r = min(1.0, d / R)
            if kind == 'tint':
                hue = math.degrees(math.atan2(-dy, dx)) % 360.0
                vr, vg, vb = _hsv(hue, 0.92, 1.0)
                t = min(1.0, r * 1.15) ** 0.8
                g = 0.34
                rr, gg, bb = g + (vr - g) * t, g + (vg - g) * t, g + (vb - g) * t
            else:
                shift = (math.degrees(math.atan2(dy, dx)) + 270.0) % 360.0 - 180.0
                rr, gg, bb = _hsv(base + shift, 0.10 + 0.90 * r, 0.98)
            o = y * stride + x * 4
            buf[o] = int(bb * cov * 255); buf[o + 1] = int(gg * cov * 255)
            buf[o + 2] = int(rr * cov * 255); buf[o + 3] = int(cov * 255)
    surf.mark_dirty()
    if len(_DISC_CACHE) > 12: _DISC_CACHE.clear()
    _DISC_CACHE[key] = surf
    return surf

def _mark_name(lbl, state):
    ctx = lbl.get_style_context()
    for c in ('cg-wname', 'cg-wname-dim', 'cg-name-glow', 'cg-name', 'cg-name-dim'): ctx.remove_class(c)
    ctx.add_class({'glow': 'cg-name-glow', 'dim': 'cg-wname-dim'}.get(state, 'cg-wname'))
    lbl.set_halign(Gtk.Align.CENTER)

class Disc(Gtk.DrawingArea):

    __gsignals__ = {
        'changed': (GObject.SignalFlags.RUN_LAST, None, (float, float)),
        'picked': (GObject.SignalFlags.RUN_LAST, None, ()),
        'released': (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, size=116, kind='tint', base=0.0, tip=None):
        super().__init__()
        self.size = size
        self.kind = kind
        self.base = base
        self.angle = 0.0
        self.radius = 0.0
        self.active = True
        self.glow = False
        self.gamma = 1.8
        self._drag = False
        self.set_size_request(size, size)
        self.set_halign(Gtk.Align.CENTER)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.connect('draw', self._draw)
        self.connect('button-press-event', self._press)
        self.connect('button-release-event', self._release)
        self.connect('motion-notify-event', self._motion)
        if tip: self.set_tooltip_text(tip + '\nDouble-click to reset.')

    def set_value(self, angle, radius):
        self.angle = float(angle)
        self.radius = max(0.0, min(1.0, float(radius))) ** (1.0 / self.gamma)
        self.queue_draw()

    def set_active(self, on):
        if self.active != bool(on):
            self.active = bool(on)
            self.queue_draw()

    def _R(self):
        return min(self.get_allocated_width(), self.get_allocated_height()) / 2.0 - 4.0

    def _pos(self):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        cx, cy, R = w / 2.0, h / 2.0, self._R()
        if self.kind == 'tint':
            a = math.radians(self.angle)
            return cx + math.cos(a) * self.radius * R, cy - math.sin(a) * self.radius * R
        a = math.radians(self.angle - 90.0)
        return cx + math.cos(a) * self.radius * R, cy + math.sin(a) * self.radius * R

    def _colour(self):
        if self.kind == 'tint':
            if self.radius < 0.02: return (0.8, 0.8, 0.8)
            return _hsv(self.angle, 0.35 + 0.65 * self.radius, 1.0)
        return _hsv(self.base + self.angle, 0.10 + 0.90 * self.radius, 0.98)

    def _draw(self, _w, cr):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        cx, cy, R = w / 2.0, h / 2.0, self._R()
        two_pi = 2 * math.pi
        cr.arc(cx, cy, R, 0, two_pi)
        cr.set_source_rgb(0.105, 0.105, 0.105)
        cr.fill()
        px, py = self._pos()
        col = self._colour()
        surf = _disc_surface(self.size, self.kind, self.base)
        s = surf.get_width()
        cr.save()
        cr.arc(cx, cy, R, 0, two_pi)
        cr.new_sub_path()
        cr.arc(cx, cy, R * 0.86, 0, two_pi)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.clip()
        cr.set_source_surface(surf, round(cx - s / 2.0), round(cy - s / 2.0))
        cr.paint()
        cr.restore()
        cr.set_source_rgba(1, 1, 1, 0.13)
        cr.set_line_width(1)
        cr.arc(cx, cy, R * 0.43, 0, two_pi); cr.stroke()
        cr.move_to(cx - R * 0.86, cy); cr.line_to(cx + R * 0.86, cy)
        cr.move_to(cx, cy - R * 0.86); cr.line_to(cx, cy + R * 0.86); cr.stroke()
        if self.radius > 0.01 or self.kind == 'shift':
            ca = -math.radians(self.angle) if self.kind == 'tint' else math.radians(self.angle - 90.0)
            cr.arc(cx, cy, R * 0.93, ca - 0.20, ca + 0.20)
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.set_line_width(2.2)
            cr.stroke()
        if not self.active:
            cr.arc(cx, cy, R, 0, two_pi)
            cr.set_source_rgba(0.12, 0.12, 0.12, 0.50); cr.fill()
        if self.radius > 0.01 or self.kind == 'shift':
            cr.set_source_rgba(1, 1, 1, 0.7)
            cr.set_line_width(1.4)
            cr.set_dash([4.0, 3.0])
            cr.move_to(cx, cy); cr.line_to(px, py); cr.stroke()
            cr.set_dash([])
        if self.kind == 'shift':
            br = _hsv(self.base, 0.9, 1.0)
            cr.arc(cx, cy, 4.5, 0, two_pi)
            cr.set_source_rgb(1, 1, 1); cr.fill_preserve()
            cr.set_source_rgb(*br); cr.set_line_width(2); cr.stroke()
        if self.active or self.glow:
            g = cairo.RadialGradient(px, py, 2, px, py, 15)
            g.add_color_stop_rgba(0, *col, 0.65); g.add_color_stop_rgba(1, *col, 0.0)
            cr.set_source(g); cr.arc(px, py, 15, 0, two_pi); cr.fill()
        cr.arc(px, py, 7.5, 0, two_pi)
        cr.set_source_rgb(*col); cr.fill_preserve()
        cr.set_source_rgb(1, 1, 1); cr.set_line_width(2.2); cr.stroke_preserve()
        cr.set_source_rgba(0, 0, 0, 0.8); cr.set_line_width(0.8); cr.stroke()
        return False

    def _from_xy(self, x, y):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        dx, dy = x - w / 2.0, y - h / 2.0
        R = self._R()
        r = min(1.0, math.hypot(dx, dy) / R)
        if self.kind == 'tint':
            a = math.degrees(math.atan2(-dy, dx)) % 360.0
        else:
            a = (math.degrees(math.atan2(dy, dx)) + 270.0) % 360.0 - 180.0
        if r < 0.02: a = self.angle
        self.angle, self.radius = a, r
        self.queue_draw()
        self.emit('changed', a, r ** self.gamma)

    def _press(self, _w, e):
        if e.button != 1: return False
        if e.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self._drag = False
            self.angle, self.radius = (0.0 if self.kind == 'shift' else self.angle), 0.0
            self.queue_draw()
            self.emit('picked')
            self.emit('changed', self.angle, 0.0)
            self.emit('released')
            return True
        if e.type == Gdk.EventType.BUTTON_PRESS:
            self._drag = True
            self.emit('picked')
            self._from_xy(e.x, e.y)
        return True

    def _motion(self, _w, e):
        if self._drag: self._from_xy(e.x, e.y)
        return True

    def _release(self, _w, e):
        if self._drag:
            self._drag = False
            self.emit('released')
        return True

class ReachStrip(Gtk.DrawingArea):

    __gsignals__ = {'selected': (GObject.SignalFlags.RUN_LAST, None, (str,))}
    _CENTRES = {SHADOWS: 0.14, MIDTONES: 0.5, HIGHLIGHTS: 0.86}
    _NAMES = {SHADOWS: 'Shadows', MIDTONES: 'Midtones', HIGHLIGHTS: 'Highlights'}

    def __init__(self):
        super().__init__()
        self.colours = {b: None for b in BANDS}
        self.reach = {b: 50.0 for b in BANDS}
        self.active = SHADOWS
        self.set_size_request(-1, 62)
        self.set_hexpand(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect('draw', self._draw)
        self.connect('button-press-event', self._press)
        self.set_tooltip_text('Roughly where each colour acts, from black (left) to white (right). '
                              'Taller means a stronger effect (Reach / luminance range). Click a zone to select its wheel.')

    def _draw(self, _w, cr):
        w = self.get_allocated_width()
        x0, x1 = 6, w - 6
        bw = x1 - x0
        by = 40
        g = cairo.LinearGradient(x0, 0, x1, 0)
        g.add_color_stop_rgb(0, 0.02, 0.02, 0.02); g.add_color_stop_rgb(1, 0.96, 0.96, 0.96)
        _rrect(cr, x0, by, bw, 8, 4)
        cr.set_source(g); cr.fill()
        for band in BANDS:
            c = self.colours.get(band)
            col = c if c else (0.55, 0.55, 0.55)
            amp = 8 + 24 * max(0.0, min(1.0, self.reach[band] / 100.0 * 0.8 + 0.2))
            centre = self._CENTRES[band]
            sig = 0.13 if band != MIDTONES else 0.16
            cr.move_to(x0, by)
            steps = 40
            for i in range(steps + 1):
                t = i / float(steps)
                yy = by - amp * math.exp(-((t - centre) ** 2) / (2 * sig * sig))
                cr.line_to(x0 + t * bw, yy)
            cr.line_to(x1, by)
            cr.close_path()
            alpha = 0.78 if c else 0.30
            cr.set_source_rgba(*col, alpha if band == self.active else alpha * 0.55)
            cr.fill_preserve()
            if band == self.active:
                cr.set_source_rgba(*_ACCENT, 1.0); cr.set_line_width(1.8)
            else:
                cr.set_source_rgba(1, 1, 1, 0.18); cr.set_line_width(1)
            cr.stroke()
            _text(cr, self._NAMES[band], x0 + centre * bw, 59, 10,
                  (1, 1, 1, 1) if band == self.active else (0.65, 0.65, 0.65, 1), 'c')
        return False

    def _press(self, _w, e):
        t = (e.x - 6) / float(max(1, self.get_allocated_width() - 12))
        band = SHADOWS if t < 0.33 else (MIDTONES if t < 0.66 else HIGHLIGHTS)
        self.emit('selected', band)
        return True

class ToneStrip(Gtk.DrawingArea):

    __gsignals__ = {
        'selected': (GObject.SignalFlags.RUN_LAST, None, (int,)),
        'moved': (GObject.SignalFlags.RUN_LAST, None, (int, float)),
        'reset': (GObject.SignalFlags.RUN_LAST, None, (int,)),
        'released': (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, state):
        super().__init__()
        self.state = state
        self.sel = 2
        self._drag = -1
        self.set_size_request(-1, 74)
        self.set_hexpand(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.connect('draw', self._draw)
        self.connect('button-press-event', self._press)
        self.connect('button-release-event', self._release)
        self.connect('motion-notify-event', self._motion)
        self.set_tooltip_text('The five tonal zones from black to white. Click a zone to adjust it, drag its dot to '
                              'choose which tones it covers. Double-click a dot to reset that zone.')

    def _x(self, pos):
        return 14 + (self.get_allocated_width() - 28) * pos

    def _draw(self, _w, cr):
        w = self.get_allocated_width()
        ty = 36
        g = cairo.LinearGradient(14, 0, w - 14, 0)
        g.add_color_stop_rgb(0, 0.0, 0.0, 0.0); g.add_color_stop_rgb(1, 1.0, 1.0, 1.0)
        _rrect(cr, 14, ty - 6, w - 28, 12, 6)
        cr.set_source(g); cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.6); cr.set_line_width(1); cr.stroke()
        if 0 <= self.sel < len(self.state):
            p = self.state[self.sel]
            pos, rng, sp = float(p['pos']), float(p['range']), float(p.get('spread', 0.5))
            l, r = self._x(max(0.0, pos - rng)), self._x(min(1.0, pos + rng))
            gg = cairo.LinearGradient(l, 0, r, 0)
            e = 0.5 * (1 - sp) * 0.9
            gg.add_color_stop_rgba(0, *_ACCENT, 0.0); gg.add_color_stop_rgba(max(0.01, e + 0.01), *_ACCENT, 0.55)
            gg.add_color_stop_rgba(min(0.99, 1 - e - 0.01), *_ACCENT, 0.55); gg.add_color_stop_rgba(1, *_ACCENT, 0.0)
            cr.set_source(gg)
            cr.rectangle(l, ty - 15, max(1, r - l), 30)
            cr.fill()
        for i, p in enumerate(self.state):
            x = self._x(float(p['pos']))
            on = i == self.sel
            cr.arc(x, ty, 8 if on else 6, 0, 2 * math.pi)
            cr.set_source_rgb(*(_ACCENT if on else (0.93, 0.93, 0.93))); cr.fill_preserve()
            cr.set_source_rgba(0, 0, 0, 0.85); cr.set_line_width(1.5); cr.stroke()
            b = float(p.get('brightness', 0.0))
            if abs(b) >= 0.5:
                _text(cr, '%+.0f' % b, x, ty - 14, 10, (*_ACCENT, 1.0) if on else (0.85, 0.85, 0.85, 1), 'c', True)
            _text(cr, str(p.get('name', '')), x, ty + 28, 10,
                  (1, 1, 1, 1) if on else (0.65, 0.65, 0.65, 1), 'c')
        return False

    def _hit(self, x):
        best, bd = -1, 14.0
        for i, p in enumerate(self.state):
            d = abs(self._x(float(p['pos'])) - x)
            if d < bd: best, bd = i, d
        return best

    def _press(self, _w, e):
        if e.button != 1: return False
        i = self._hit(e.x)
        if i < 0:
            t = (e.x - 14) / float(max(1, self.get_allocated_width() - 28))
            i = min(range(len(self.state)), key=lambda j: abs(float(self.state[j]['pos']) - t))
        if e.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self._drag = -1
            self.emit('reset', i)
            return True
        self._drag = i
        self.sel = i
        self.emit('selected', i)
        return True

    def _motion(self, _w, e):
        if self._drag >= 0:
            n = len(self.state)
            i = self._drag
            t = (e.x - 14) / float(max(1, self.get_allocated_width() - 28))
            lo = float(self.state[i - 1]['pos']) + 0.02 if i > 0 else 0.02
            hi = float(self.state[i + 1]['pos']) - 0.02 if i < n - 1 else 0.98
            self.emit('moved', i, max(lo, min(hi, t)))
        return True

    def _release(self, _w, e):
        if self._drag >= 0:
            self._drag = -1
            self.emit('released')
        return True

class ToneDial(Gtk.DrawingArea):

    __gsignals__ = {
        'value-changed': (GObject.SignalFlags.RUN_LAST, None, (float,)),
        'released': (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self):
        super().__init__()
        self.value = 0.0
        self._drag = False
        self.set_size_request(228, 180)
        self.set_halign(Gtk.Align.CENTER)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.BUTTON1_MOTION_MASK | Gdk.EventMask.SCROLL_MASK)
        self.connect('draw', self._draw)
        self.connect('button-press-event', self._press)
        self.connect('button-release-event', self._release)
        self.connect('motion-notify-event', self._motion)
        self.connect('scroll-event', self._scroll)
        self.set_tooltip_text('Lightness (CIE lightness): turn the dial right to brighten this zone, left to darken it. '
                              'Double-click to reset.')

    def set_value_quiet(self, v):
        self.value = max(-100.0, min(100.0, float(v)))
        self.queue_draw()

    def _draw(self, _w, cr):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        cx, cy, R = w / 2.0, h / 2.0 + 4, min(w, h) / 2.0 - 16
        a0, a1 = math.radians(-225), math.radians(45)
        cr.set_line_width(11); cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgb(0.12, 0.12, 0.12)
        cr.arc(cx, cy, R, a0, a1); cr.stroke()
        am = math.radians(-90)
        av = math.radians(-90 + self.value / 100.0 * 135.0)
        if abs(self.value) > 0.4:
            cr.set_source_rgb(*_ACCENT)
            if self.value > 0: cr.arc(cx, cy, R, am, av)
            else: cr.arc_negative(cx, cy, R, am, av)
            cr.stroke()
        cr.set_line_cap(cairo.LINE_CAP_BUTT)
        kx, ky = cx + math.cos(av) * R, cy + math.sin(av) * R
        cr.arc(kx, ky, 9, 0, 2 * math.pi)
        cr.set_source_rgb(1, 1, 1); cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.7); cr.set_line_width(1.5); cr.stroke()
        _text(cr, '%+.0f' % self.value if abs(self.value) >= 0.5 else '0', cx, cy + 6, 26, (1, 1, 1, 1), 'c', True)
        _text(cr, 'Lightness', cx, cy + 26, 11, (0.7, 0.7, 0.7, 1), 'c')
        _text(cr, 'Darker', cx - R * 0.72, cy + R * 0.95 + 4, 10, (0.65, 0.65, 0.65, 1), 'c')
        _text(cr, 'Lighter', cx + R * 0.72, cy + R * 0.95 + 4, 10, (0.65, 0.65, 0.65, 1), 'c')
        return False

    def _from_xy(self, x, y):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        dx, dy = x - w / 2.0, y - (h / 2.0 + 4)
        th = math.degrees(math.atan2(dx, -dy))
        th = max(-135.0, min(135.0, th))
        self._set(th / 135.0 * 100.0)

    def _set(self, v):
        v = round(max(-100.0, min(100.0, v)))
        if v != round(self.value):
            self.value = float(v)
            self.queue_draw()
            self.emit('value-changed', self.value)

    def _press(self, _w, e):
        if e.button != 1: return False
        if e.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self._drag = False
            self._set(0.0)
            self.emit('released')
            return True
        self._drag = True
        self._from_xy(e.x, e.y)
        return True

    def _motion(self, _w, e):
        if self._drag: self._from_xy(e.x, e.y)
        return True

    def _release(self, _w, e):
        if self._drag:
            self._drag = False
            self.emit('released')
        return True

    def _scroll(self, _w, e):
        d = 1 if e.direction == Gdk.ScrollDirection.UP else (-1 if e.direction == Gdk.ScrollDirection.DOWN else 0)
        if d:
            self._set(self.value + d * 2)
            self.emit('released')
        return True

_HUE_STOPS = []

def _hue_colour(deg, L=66.0, C=58.0):
    return lch_swatch(L, C, deg % 360.0)

def _hue_stops():
    if not _HUE_STOPS:
        for i in range(0, 361, 15):
            _HUE_STOPS.append(_hue_colour(i))
    return _HUE_STOPS

_RING_CACHE2 = {}
_RING_MARGIN = 16


def _hue_falloff(p):
    core = max(0.005, min(0.24, float(p.get('range', 20.0)) / 360.0))
    sp = max(0.0, min(1.0, float(p.get('spread', 0.5))))
    outer = min(0.5, core + max(0.004, sp * (0.5 - core)))
    return core * 360.0, outer * 360.0


def _hue_ring_surface(size, ring_w):
    key = (size, ring_w)
    surf = _RING_CACHE2.get(key)
    if surf is not None: return surf
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    c = size / 2.0
    mid = c - _RING_MARGIN - ring_w / 2.0
    cr.translate(c, c)
    cr.set_line_width(ring_w)
    for i in range(0, 360, 3):
        cr.set_source_rgb(*_hue_colour(i + 1.5))
        cr.arc_negative(0, 0, mid, -math.radians(i) + 0.012, -math.radians(i + 3) - 0.012)
        cr.stroke()
    _RING_CACHE2[key] = surf
    return surf

class HueTargets(Gtk.DrawingArea):

    __gsignals__ = {
        'add': (GObject.SignalFlags.RUN_LAST, None, (float,)),
        'selected': (GObject.SignalFlags.RUN_LAST, None, (int,)),
        'moved': (GObject.SignalFlags.RUN_LAST, None, (int, float)),
        'reset': (GObject.SignalFlags.RUN_LAST, None, (int,)),
        'released': (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, samples):
        super().__init__()
        self.samples = samples
        self.sel = -1
        self._drag = -1
        self.set_size_request(-1, 66)
        self.set_hexpand(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.connect('draw', self._draw)
        self.connect('button-press-event', self._press)
        self.connect('button-release-event', self._release)
        self.connect('motion-notify-event', self._motion)
        self.set_tooltip_text('All the colours in your picture. Click anywhere to add a target colour, drag a marker '
                              'to move it. Double-click a marker to reset its changes.')

    def _x(self, deg):
        return 12 + (self.get_allocated_width() - 24) * (deg % 360.0) / 360.0

    def _deg(self, x):
        return max(0.0, min(359.9, (x - 12) / float(max(1, self.get_allocated_width() - 24)) * 360.0))

    def _draw(self, _w, cr):
        w = self.get_allocated_width()
        g = cairo.LinearGradient(12, 0, w - 12, 0)
        stops = _hue_stops()
        for i, c in enumerate(stops):
            g.add_color_stop_rgb(i / float(len(stops) - 1), *c)
        _rrect(cr, 12, 20, w - 24, 14, 7)
        cr.set_source(g); cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.6); cr.set_line_width(1); cr.stroke()
        for i, p in enumerate(self.samples):
            x = self._x(float(p['source_hue']))
            on = i == self.sel
            if on:
                core_d, outer_d = _hue_falloff(p)
                span = float(w - 24) / 360.0
                core_px, outer_px = core_d * span, outer_d * span
                for off in (-360.0 * span, 0.0, 360.0 * span):
                    gx = x + off
                    gb = cairo.LinearGradient(gx - outer_px, 0, gx + outer_px, 0)
                    edge = (outer_px - core_px) / (2.0 * outer_px)
                    gb.add_color_stop_rgba(0, 1, 1, 1, 0.0)
                    gb.add_color_stop_rgba(edge, 1, 1, 1, 0.42)
                    gb.add_color_stop_rgba(1.0 - edge, 1, 1, 1, 0.42)
                    gb.add_color_stop_rgba(1, 1, 1, 1, 0.0)
                    cr.set_source(gb)
                    cr.rectangle(max(12.0, gx - outer_px), 16, min(w - 12.0, gx + outer_px) - max(12.0, gx - outer_px), 22)
                    cr.fill()
                _text(cr, '\u00b1%.0f\u00b0' % core_d, x, 12, 10, (1, 1, 1, 0.95), 'c', True)
                tx = self._x(float(p['source_hue']) + float(p.get('hue', 0.0)))
                cr.set_source_rgba(*_ACCENT, 1.0); cr.set_line_width(2)
                cr.move_to(x, 44); cr.line_to(tx, 44); cr.stroke()
                cr.arc(tx, 44, 5.5, 0, 2 * math.pi)
                cr.set_source_rgb(*_hue_colour(float(p['source_hue']) + float(p.get('hue', 0.0))))
                cr.fill_preserve(); cr.set_source_rgb(1, 1, 1); cr.set_line_width(1.8); cr.stroke()
            cr.arc(x, 27, 9 if on else 7, 0, 2 * math.pi)
            cr.set_source_rgb(*_hue_colour(float(p['source_hue']))); cr.fill_preserve()
            cr.set_source_rgb(*(_ACCENT if on else (1, 1, 1))); cr.set_line_width(2.6 if on else 2); cr.stroke()
        if not self.samples:
            _text(cr, 'Click the strip to pick a colour to change', w / 2.0, 56, 11, (0.7, 0.7, 0.7, 1), 'c')
        return False

    def _hit(self, x):
        best, bd = -1, 12.0
        for i, p in enumerate(self.samples):
            d = abs(self._x(float(p['source_hue'])) - x)
            if d < bd: best, bd = i, d
        return best

    def _press(self, _w, e):
        if e.button != 1: return False
        i = self._hit(e.x)
        if e.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            if i >= 0: self.emit('reset', i)
            return True
        if i < 0:
            self.emit('add', self._deg(e.x))
            return True
        self._drag = i
        self.emit('selected', i)
        return True

    def _motion(self, _w, e):
        if self._drag >= 0: self.emit('moved', self._drag, self._deg(e.x))
        return True

    def _release(self, _w, e):
        if self._drag >= 0:
            self._drag = -1
            self.emit('released')
        return True

class HueRing(Gtk.DrawingArea):

    __gsignals__ = {
        'add': (GObject.SignalFlags.RUN_LAST, None, (float,)),
        'selected': (GObject.SignalFlags.RUN_LAST, None, (int,)),
        'moved': (GObject.SignalFlags.RUN_LAST, None, (int, float)),
        'shifted': (GObject.SignalFlags.RUN_LAST, None, (int, float)),
        'reset': (GObject.SignalFlags.RUN_LAST, None, (int,)),
        'released': (GObject.SignalFlags.RUN_LAST, None, ()),
    }
    SIZE = 236
    RING = 14

    def __init__(self, samples):
        super().__init__()
        self.samples = samples
        self.sel = -1
        self._drag = None
        self.set_size_request(self.SIZE, self.SIZE)
        self.set_halign(Gtk.Align.CENTER)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.connect('draw', self._draw)
        self.connect('button-press-event', self._press)
        self.connect('button-release-event', self._release)
        self.connect('motion-notify-event', self._motion)
        self.set_tooltip_text('Colour ring. The hollow dot is the colour you picked, the solid dot is where it will move to. '
                              'Drag the solid dot to shift the colour, drag the hollow dot to choose another colour, '
                              'click the ring to add a target.')

    def _geo(self):
        c = self.SIZE / 2.0
        return c, c, c - _RING_MARGIN - self.RING / 2.0

    def _pt(self, deg, r=None):
        cx, cy, mid = self._geo()
        r = mid if r is None else r
        a = math.radians(deg)
        return cx + math.cos(a) * r, cy - math.sin(a) * r

    def _draw(self, _w, cr):
        cx, cy, mid = self._geo()
        surf = _hue_ring_surface(self.SIZE, self.RING)
        cr.set_source_surface(surf, 0, 0); cr.paint()
        inner = mid - self.RING / 2.0
        if 0 <= self.sel < len(self.samples):
            p = self.samples[self.sel]
            src = float(p['source_hue']); sh = float(p.get('hue', 0.0))
            core_d, outer_d = _hue_falloff(p)
            arc_r = mid + self.RING / 2.0 + 8
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_width(5)
            cr.set_source_rgba(*_ACCENT, 0.35)
            cr.arc_negative(cx, cy, arc_r, -math.radians(src - outer_d), -math.radians(src + outer_d))
            cr.stroke()
            cr.set_source_rgba(*_ACCENT, 1.0)
            cr.arc_negative(cx, cy, arc_r, -math.radians(src - core_d), -math.radians(src + core_d))
            cr.stroke()
            cr.set_line_cap(cairo.LINE_CAP_BUTT)
            cr.set_line_width(3.5); cr.set_source_rgba(1, 1, 1, 0.9)
            if abs(sh) > 0.5:
                a, b = -math.radians(src), -math.radians(src + sh)
                if sh > 0: cr.arc_negative(cx, cy, inner - 13, a, b)
                else: cr.arc(cx, cy, inner - 13, a, b)
                cr.stroke()
            before = _hue_colour(src)
            L = 66.0 + float(p.get('luminance', 0.0)) * 0.25
            C = 58.0 * max(0.0, 1.0 + float(p.get('saturation', 0.0)) / 100.0)
            after = _hue_colour(src + sh, L, C)
            rr = (inner - 20) * 0.92
            cr.arc(cx, cy, rr, math.pi / 2, 1.5 * math.pi)
            cr.close_path(); cr.set_source_rgb(*before); cr.fill()
            cr.arc(cx, cy, rr, -math.pi / 2, math.pi / 2)
            cr.close_path(); cr.set_source_rgb(*after); cr.fill()
            _text(cr, 'Before', cx - rr * 0.5, cy + 4, 10, (0, 0, 0, 0.75), 'c', True)
            _text(cr, 'After', cx + rr * 0.5, cy + 4, 10, (0, 0, 0, 0.75), 'c', True)
        else:
            _text(cr, 'Click the ring to', cx, cy - 2, 11, (0.75, 0.75, 0.75, 1), 'c')
            _text(cr, 'pick a colour', cx, cy + 14, 11, (0.75, 0.75, 0.75, 1), 'c')
        for i, p in enumerate(self.samples):
            on = i == self.sel
            sx, sy = self._pt(float(p['source_hue']))
            cr.arc(sx, sy, 8 if on else 6.5, 0, 2 * math.pi)
            cr.set_source_rgba(0, 0, 0, 0.35); cr.fill_preserve()
            cr.set_source_rgb(*(_ACCENT if on else (1, 1, 1))); cr.set_line_width(2.4); cr.stroke()
            if on:
                tx, ty = self._pt(float(p['source_hue']) + float(p.get('hue', 0.0)), inner - 13)
                cr.arc(tx, ty, 8.5, 0, 2 * math.pi)
                cr.set_source_rgb(*_hue_colour(float(p['source_hue']) + float(p.get('hue', 0.0)))); cr.fill_preserve()
                cr.set_source_rgb(1, 1, 1); cr.set_line_width(2.4); cr.stroke()
        return False

    def _hit(self, x, y):
        best, bd, kind = -1, 13.0, None
        for i, p in enumerate(self.samples):
            sx, sy = self._pt(float(p['source_hue']))
            d = math.hypot(sx - x, sy - y)
            if d < bd: best, bd, kind = i, d, 'src'
            if i == self.sel:
                _, _, mid = self._geo()
                tx, ty = self._pt(float(p['source_hue']) + float(p.get('hue', 0.0)), mid - self.RING / 2.0 - 13)
                d = math.hypot(tx - x, ty - y)
                if d < bd: best, bd, kind = i, d, 'tgt'
        return best, kind

    def _deg_at(self, x, y):
        cx, cy, _ = self._geo()
        return math.degrees(math.atan2(-(y - cy), x - cx)) % 360.0

    def _press(self, _w, e):
        if e.button != 1: return False
        i, kind = self._hit(e.x, e.y)
        if e.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            if i >= 0: self.emit('reset', i)
            return True
        if i >= 0:
            self._drag = (i, kind)
            self.emit('selected', i)
            return True
        cx, cy, mid = self._geo()
        d = math.hypot(e.x - cx, e.y - cy)
        if abs(d - mid) <= self.RING / 2.0 + 4:
            self.emit('add', self._deg_at(e.x, e.y))
        return True

    def _motion(self, _w, e):
        if self._drag:
            i, kind = self._drag
            deg = self._deg_at(e.x, e.y)
            if kind == 'src':
                self.emit('moved', i, deg)
            else:
                src = float(self.samples[i]['source_hue'])
                self.emit('shifted', i, (deg - src + 540.0) % 360.0 - 180.0)
        return True

    def _release(self, _w, e):
        if self._drag:
            self._drag = None
            self.emit('released')
        return True

def _kelvin_gain(orig, inten):
    def rgb(k):
        t = k / 100.0
        r = 255.0 if t <= 66 else 329.698727446 * ((t - 60) ** -0.1332047592)
        g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * ((t - 60) ** -0.0755148492)
        b = 255.0 if t >= 66 else (0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
        return tuple(srgb_to_linear(max(0.0, min(255.0, v)) / 255.0) for v in (r, g, b))
    a, b = rgb(orig), rgb(inten)
    g = [a[i] / max(b[i], 1e-4) for i in range(3)]
    y = 0.2126 * g[0] + 0.7152 * g[1] + 0.0722 * g[2]
    return [v / y for v in g]


class LookGrader:

    def __init__(self, st):
        scale = {SHADOWS: CB_SCALE[SHADOWS], MIDTONES: CB_SCALE[MIDTONES], HIGHLIGHTS: CB_SCALE[HIGHLIGHTS]}
        self.bands = [[float(v) * scale[b] * 0.72 for v in st['bands'].get(b, (0, 0, 0))] for b in BANDS]
        self.master = [float(v) * CB_SCALE[MASTER] * 0.72 for v in st['master']]
        self.balance = any(abs(v) > 1e-7 for row in self.bands for v in row) or any(abs(v) > 1e-7 for v in self.master)
        W = ManagedPreviewManager._widths
        self.stages = []
        for p in st['tone']:
            b = float(p.get('brightness', 0.0))
            if abs(b) > 1e-7:
                core, outer = W(p.get('range', .2), p.get('spread', .5), .49)
                self.stages.append(('tone', (float(p['pos']), core, outer), b * ManagedPreviewManager.TONE_L_SCALE, None))
        for p in st['tonal_colour']:
            g = tint_gains(p.get('hue', 0.0), p.get('strength', 0.0)); lum = float(p.get('luminance', 0.0))
            if g is not None or abs(lum) > 1e-7:
                core, outer = W(p.get('range', .2), p.get('spread', .5), .49)
                self.stages.append(('tonal', (float(p['pos']), core, outer), lum * ManagedPreviewManager.TONE_L_SCALE, g))
        for p in st['hue_adjustments']:
            sh, lum, sat = float(p.get('hue', 0.0)), float(p.get('luminance', 0.0)), float(p.get('saturation', 0.0))
            if max(abs(sh), abs(lum), abs(sat)) > 1e-7:
                core, outer = W(float(p.get('range', 20.0)) / 360.0, p.get('spread', .5), .24)
                self.stages.append(('hue', ((float(p['source_hue']) % 360.0) / 360.0, core, outer),
                                    (sh, lum * ManagedPreviewManager.TONE_L_SCALE, max(0.0, 1.0 + sat / 100.0)), None))
        self.need_lch = bool(self.stages)
        gh = float(st.get('global_hue', 0.0))
        self.ghue = gh if abs(gh) > 1e-7 else 0.0
        c = float(st['contrast'])
        self.contrast = None
        if abs(c) > 1e-7:
            shaped = math.copysign(abs(c) ** 1.55, c)
            k = 2.0 ** (1.25 * shaped)
            self.contrast = (k, max(-1.0, min(1.0, (float(st.get('pivot', .5)) - .5) * (1.0 - k))))
        self.prim = None
        pr = st['primaries']
        if any(abs(float(pr[k][q])) > 1e-7 for k in pr for q in ('hue', 'purity')):
            self.prim = ManagedPreviewManager._primary_matrix(pr)
        self.sat = float(st['saturation'])
        gc = st['global_colour']
        t = max(-100.0, min(100.0, float(gc['temperature'])))
        self.temp = _kelvin_gain(*ManagedPreviewManager._kelvin_pair(t)) if abs(t) > 1e-7 else None
        tint = max(-100.0, min(100.0, float(gc['tint']))) / 100.0
        k = (math.copysign(0.40 * abs(tint) + 0.60 * tint * tint, tint) if tint else 0.0) * 0.70
        self.tint = (1.0 + k, 1.0 - k, 1.0 + k) if abs(k) > 1e-7 else None
        self.chroma = max(-100.0, min(100.0, float(gc['chroma']))) * 0.60
        s = max(0.0, min(200.0, float(gc['saturation'])))
        self.gsat = s / 100.0 if s <= 100.0 else 1.0 + (s - 100.0) / 100.0 * 0.70

    @staticmethod
    def _trap(x, c, core, outer):
        w = 1.0; span = max(1e-9, outer - core)
        if c - core > 1e-6: w *= max(0.0, min(1.0, (x - (c - outer)) / span))
        if c + core < 1.0 - 1e-6: w *= max(0.0, min(1.0, ((c + outer) - x) / span))
        return w

    @staticmethod
    def _lift(rgb, dl):
        y = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
        ln = max(0.0, min(100.0, 116.0 * _lab_f(max(y, 0.0)) - 16.0 + dl))
        yn = _lab_finv((ln + 16.0) / 116.0)
        if y < 1e-6: return (yn, yn, yn)
        k = yn / y
        return (rgb[0] * k, rgb[1] * k, rgb[2] * k)

    @staticmethod
    def _clip(rgb):
        return tuple(max(0.0, min(1.0, v)) for v in rgb)

    def __call__(self, r, g, b):
        if self.balance:
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            ws = max(0.0, min(1.0, (0.333 - lum) / 0.25 + 0.5)) * 0.7
            wm = max(0.0, min(1.0, (lum - 0.333) / 0.25 + 0.5)) * max(0.0, min(1.0, (0.667 - lum) / 0.25 + 0.5)) * 0.7
            wh = max(0.0, min(1.0, (lum + 0.333 - 1.0) / 0.25 + 0.5)) * 0.7
            nr, ng, nb = (max(0.0, min(1.0, c + self.bands[0][i] * ws + self.bands[1][i] * wm + self.bands[2][i] * wh + self.master[i]))
                          for i, c in enumerate((r, g, b)))
            y1 = 0.2126 * nr + 0.7152 * ng + 0.0722 * nb
            if y1 > 1e-6:
                k = lum / y1
                nr, ng, nb = min(1.0, nr * k), min(1.0, ng * k), min(1.0, nb * k)
            r, g, b = nr, ng, nb
        cur = (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b))
        if self.need_lch:
            L0, C0, h0 = linear_rgb_to_lch(*cur)
            for kind, (c, core, outer), val, extra in self.stages:
                if kind == 'hue':
                    gate = max(0.0, min(1.0, (C0 / 200.0 - 0.015) / 0.06))
                    w = 0.0
                    for k in (-1, 0, 1): w += self._trap(h0 / 360.0, c + k, core, outer)
                    w = min(1.0, w) * gate
                else:
                    w = self._trap(L0 / 100.0, c, core, outer)
                if w < 0.003: continue
                if kind == 'tone': adj = self._lift(cur, val)
                elif kind == 'tonal':
                    adj = cur if extra is None else (cur[0] * extra[0], cur[1] * extra[1], cur[2] * extra[2])
                    if abs(val) > 1e-7: adj = self._lift(adj, val)
                else:
                    L, C, h = linear_rgb_to_lch(*cur)
                    adj = self._clip(lch_to_linear_rgb(max(0.0, min(100.0, L + val[1])), C * val[2], h + val[0]))
                cur = tuple(cur[i] + w * (adj[i] - cur[i]) for i in range(3))
        if self.ghue:
            L, C, h = linear_rgb_to_lch(*cur)
            cur = self._clip(lch_to_linear_rgb(L, C, h + self.ghue))
        if self.prim:
            m = self.prim
            cur = tuple(max(0.0, min(1.0, m[i][0] * cur[0] + m[i][1] * cur[1] + m[i][2] * cur[2])) for i in range(3))
        if self.contrast:
            k, br = self.contrast
            cur = tuple((((max(0.0, min(1.0, (max(v, 0.0) ** (1 / 2.2) - 0.5) * k + 0.5 + br))) ** 2.2)) for v in cur)
        if abs(self.sat - 1.0) > 1e-7:
            y = 0.2126 * cur[0] + 0.7152 * cur[1] + 0.0722 * cur[2]
            cur = self._clip(tuple(y + (v - y) * self.sat for v in cur))
        if self.temp: cur = self._clip(tuple(cur[i] * self.temp[i] for i in range(3)))
        if self.tint: cur = self._clip(tuple(cur[i] * self.tint[i] for i in range(3)))
        if abs(self.chroma) > 1e-7 or abs(self.gsat - 1.0) > 1e-7:
            L, C, h = linear_rgb_to_lch(*cur)
            cur = self._clip(lch_to_linear_rgb(L, max(0.0, (C + self.chroma) * self.gsat), h))
        return linear_to_srgb(cur[0]), linear_to_srgb(cur[1]), linear_to_srgb(cur[2])


class LookSwatch(Gtk.DrawingArea):

    def __init__(self, st):
        super().__init__()
        grade = LookGrader(st)
        self.stops = []
        for i in range(9):
            v = 0.05 + 0.92 * i / 8.0
            r, g, b = grade(v, v, v)
            y = 0.2126 * r + 0.7152 * g + 0.0722 * b
            self.stops.append(tuple(max(0.0, min(1.0, y + (c - y) * 2.0)) for c in (r, g, b)))
        self.set_size_request(-1, 30)
        self.connect('draw', self._draw)

    def _draw(self, _w, cr):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        g = cairo.LinearGradient(0, 0, w, 0)
        for i, c in enumerate(self.stops):
            g.add_color_stop_rgb(i / 8.0, *c)
        _rrect(cr, 0, 0, w, h, 5)
        cr.set_source(g); cr.fill()
        return False


PREVIEW_FAST_MS = 80

BAND_ORDER = (SHADOWS, MIDTONES, HIGHLIGHTS, MASTER)
BAND_NAME = {SHADOWS: 'Shadows', MIDTONES: 'Midtones', HIGHLIGHTS: 'Highlights', MASTER: 'Overall'}
BAND_TIP = {
    SHADOWS: 'Shadows (Lift): adds colour to the dark parts of the picture.',
    MIDTONES: 'Midtones (Gamma): adds colour to the middle tones, where skin and most of the scene live.',
    HIGHLIGHTS: 'Highlights (Gain): adds colour to the bright parts of the picture.',
    MASTER: 'Overall (Master / Offset): adds the same colour cast to the whole picture.',
}
LOOK_CATS = {
    'tealorange': 'Cinematic', 'bleach': 'Cinematic', 'tungsten': 'Cinematic', 'portrait': 'Portrait',
    'slide': 'Film', 'cross': 'Film', 'polaroid': 'Film', 'fadedforest': 'Landscape', 'landscape': 'Landscape',
    'golden': 'Warm', 'autumn': 'Warm', 'summer': 'Warm', 'arctic': 'Cool', 'moonlight': 'Cool',
    'sepia': 'Mono', 'silver': 'Mono', 'pastel': 'Creative', 'cyberpunk': 'Creative',
}
LOOK_CAT_ORDER = ('All', 'Cinematic', 'Film', 'Portrait', 'Landscape', 'Warm', 'Cool', 'Mono', 'Creative')
_DEFAULT_RANGES = {'shadows_mid': DEFAULT_SHADOWS_MID, 'midtones_left': DEFAULT_MIDTONES_LEFT,
                   'midtones_right': DEFAULT_MIDTONES_RIGHT, 'highlights_mid': DEFAULT_HIGHLIGHTS_MID}
_MID_WIDTH = DEFAULT_MIDTONES_RIGHT - DEFAULT_MIDTONES_LEFT
_MID_CENTRE = (DEFAULT_MIDTONES_LEFT + DEFAULT_MIDTONES_RIGHT) / 2.0

def _reach_to_ranges(band, v):
    if band == SHADOWS: return {'shadows_mid': 1.0 - v / 100.0}
    if band == HIGHLIGHTS: return {'highlights_mid': v / 100.0}
    ratio = (3.0 ** ((v - 50.0) / 50.0)) ** (1.0 / 1.35)
    half = _MID_WIDTH * ratio / 2.0
    return {'midtones_left': max(0.02, _MID_CENTRE - half), 'midtones_right': min(0.98, _MID_CENTRE + half)}

def _ranges_to_reach(band, r):
    if band == SHADOWS: return max(0.0, min(100.0, 100.0 * (1.0 - float(r['shadows_mid']))))
    if band == HIGHLIGHTS: return max(0.0, min(100.0, 100.0 * float(r['highlights_mid'])))
    width = max(0.02, float(r['midtones_right']) - float(r['midtones_left']))
    m = max(1e-3, (width / _MID_WIDTH) ** 1.35)
    return max(0.0, min(100.0, 50.0 + 50.0 * math.log(m) / math.log(3.0)))

def _hue_name(h):
    h %= 360.0
    for lim, name in ((15, 'Red'), (40, 'Orange'), (70, 'Yellow'), (160, 'Green'), (205, 'Teal'),
                      (255, 'Blue'), (290, 'Purple'), (335, 'Pink')):
        if h < lim: return name
    return 'Red'

def _rainbow(n=7, s=0.9):
    return [_hsv(i * 360.0 / (n - 1), s, 1.0) for i in range(n)]

class ColourGradingDialog(Gtk.Dialog):

    def __init__(self, image, drawable):
        super().__init__(title='Colour Grading — ' + BUILD_TAG, use_header_bar=False)
        self._image = image
        self._drawable = drawable
        self._source_drawable = drawable
        self._ready = False
        self._preview = LutPreviewManager(image, drawable)

        self._wheel_timer = {b: None for b in BANDS}
        self._range_timers = {b: None for b in BANDS}
        self._master_timer = None
        self._sat_timer = None
        self._contrast_timer = None
        self._managed_preview_timer = None
        self._last_render_ms = 0.0
        self._wheel_values = {b: (0.0, 0.0, 0.0, 0.0, 0.0) for b in BANDS}
        self._canonical_rgb = {b: (0.0, 0.0, 0.0) for b in BANDS}
        self._master_canonical_rgb = (0.0, 0.0, 0.0)
        self._preview._band_wheel = {b: (0.0, 0.0, 0.0) for b in BANDS}
        self._hs = {b: (0.0, 0.0) for b in BAND_ORDER}
        self._ranges = dict(_DEFAULT_RANGES)
        self._reach = {b: 50.0 for b in BANDS}
        self._sat_value = 1.0
        self._contrast = 0.0
        self._pivot = 0.5
        self._active = SHADOWS
        self._tone_state = self._neutral_tone_state()
        self._tonal_colour_state = self._neutral_tonal_colour_state()
        self._hue_adjust_state = []
        self._global_hue = 0.0
        self._primaries_state = {k: {'hue': 0.0, 'purity': 0.0} for k in ('red', 'green', 'blue')}
        self._global_colour_state = {'temperature': 0.0, 'tint': 0.0, 'chroma': 0.0, 'saturation': 100.0}
        self._pending_targeted = False
        self._pending_primaries = False
        self._pending_global = False
        self._scopes_dirty = True
        self._loading_state = False
        self._closing_from_window = False
        self._initial_resize_done = False
        self._last_look = None
        self._applied = False
        self._tone_sel = 2
        self._tc_active = 0
        self._hue_sel = -1

        _apply_css()
        self.get_style_context().add_class('cg-dialog')
        self._build_ui()
        self._ready = True
        self.connect('delete-event', self._on_window_delete)
        self.connect('map-event', self._force_initial_window_size)
        GLib.idle_add(self._do_init)

    def _do_init(self):
        GimpUi.window_set_transient(self)
        Gimp.progress_init('Initialising Colour Grading…')
        self._preview.initialise()
        try: Gimp.progress_end()
        except Exception: pass
        self._refresh_colour_diagnostics()
        return False

    def _target_size(self, monitor=None):
        try:
            display = Gdk.Display.get_default()
            if monitor is None and display is not None:
                monitor = display.get_primary_monitor()
                if monitor is None and display.get_n_monitors() > 0: monitor = display.get_monitor(0)
            if monitor is not None:
                wa = monitor.get_workarea()
                return min(560, int(wa.width * 0.9)), max(480, min(940, int(wa.height * 0.84)))
        except Exception:
            pass
        return 540, 780

    def _force_initial_window_size(self, widget, event):
        if not self._initial_resize_done:
            self._initial_resize_done = True
            GLib.idle_add(self._apply_initial_window_size)
        return False

    def _apply_initial_window_size(self):
        try:
            display = Gdk.Display.get_default()
            mon = None
            if display is not None and self.get_window() is not None:
                mon = display.get_monitor_at_window(self.get_window())
            w, h = self._target_size(mon)
            self.resize(w, h)
            if mon is not None:
                wa = mon.get_workarea()
                self.move(wa.x + max(0, wa.width - w - 24), wa.y + max(0, (wa.height - h) // 2))
        except Exception:
            pass
        return False

    def _build_ui(self):
        self.set_border_width(0)
        self.set_resizable(True)
        self.set_default_size(*self._target_size())
        self.set_size_request(470, 420)
        outer = self.get_content_area()
        outer.set_spacing(0)
        nb = Gtk.Notebook()
        nb.set_scrollable(True)
        nb.set_hexpand(True)
        nb.set_vexpand(True)
        self._nb = nb
        for name, builder in (('Balance', self._build_balance), ('Tone', self._build_tone),
                              ('Tonal', self._build_tonal), ('Hue', self._build_hue),
                              ('Colour', self._build_colour), ('Scopes', self._build_scopes),
                              ('Looks', self._build_looks)):
            nb.append_page(self._scrolled(builder()), Gtk.Label(label=name))
        nb.connect('switch-page', self._on_notebook_switch_page)
        outer.pack_start(nb, True, True, 0)
        outer.pack_start(self._build_footer(), False, False, 0)
        self.show_all()
        self._sync_balance_panel()
        self._sync_tone_ui()
        self._sync_tonal_ui()
        self._sync_hue_ui()

    @staticmethod
    def _scrolled(w):
        w.set_border_width(12)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_shadow_type(Gtk.ShadowType.NONE)
        sc.add(w)
        return sc

    def _tab_reset(self, cb, tip='Put everything on this tab back to neutral'):
        b = Gtk.Button(label='Reset tab')
        b.set_tooltip_text(tip)
        b.connect('clicked', cb)
        b.set_valign(Gtk.Align.CENTER)
        return b

    def _head(self, title, reset_cb, *extra):
        h = _hbox(6)
        h.pack_start(_sec(title), False, False, 0)
        if reset_cb is not None:
            h.pack_end(self._tab_reset(reset_cb), False, False, 0)
        for w in extra:
            h.pack_end(w, False, False, 4)
        return h

    def _build_footer(self):
        box = _vbox(6)
        box.get_style_context().add_class('cg-foot')
        box.set_border_width(10)
        r1 = _hbox(10)
        self._global_preview = Gtk.CheckButton(label='Preview')
        self._global_preview.set_active(True)
        self._global_preview.set_tooltip_text('Show or hide the grade on the picture')
        self._global_preview.connect('toggled', self._on_preview_toggled)
        r1.pack_start(self._global_preview, False, False, 0)
        lbl = Gtk.Label(label='Result goes to a new layer: ')
        lbl.get_style_context().add_class('cg-hint')
        name = Gtk.Label()
        try: name.set_markup('<b>%s</b>' % GLib.markup_escape_text(_next_result_name(self._image)))
        except Exception: name.set_markup('<b>%s</b>' % RESULT_LAYER_NAME)
        r1.pack_start(lbl, False, False, 0)
        r1.pack_start(name, False, False, 0)
        r2 = _hbox(6)
        restore = Gtk.Button(label='Restore last edit')
        restore.set_tooltip_text('Load the grade you last applied to this layer')
        restore.connect('clicked', self._restore_last_edit)
        reset = Gtk.Button(label='Reset all')
        reset.set_tooltip_text('Put every control on every tab back to neutral')
        reset.connect('clicked', self._confirm_global_reset)
        cancel = Gtk.Button(label='Cancel')
        cancel.connect('clicked', self._on_cancel)
        apply_ = Gtk.Button(label='OK')
        apply_.set_tooltip_text('Bake the grade into a new layer')
        apply_.get_style_context().add_class('cg-apply')
        apply_.connect('clicked', self._on_ok)
        r2.pack_start(restore, False, False, 0)
        r2.pack_start(reset, False, False, 0)
        r2.pack_end(apply_, False, False, 0)
        r2.pack_end(cancel, False, False, 0)
        box.pack_start(r1, False, False, 0)
        box.pack_start(r2, False, False, 0)
        return box

    def _build_balance(self):
        root = _vbox(8)
        root.pack_start(self._head('Colour balance', self._reset_balance), False, False, 0)
        row = _hbox(6)
        row.set_homogeneous(True)
        self._discs, self._wnames, self._wreads = {}, {}, {}
        for band in BANDS:
            col = _vbox(3)
            nm = Gtk.Label(label=BAND_NAME[band])
            nm.set_tooltip_text(BAND_TIP[band])
            self._wnames[band] = nm
            d = Disc(166, 'tint', tip=BAND_TIP[band])
            d.connect('changed', self._on_disc, band)
            d.connect('picked', lambda _w, b=band: self._set_active_band(b))
            self._discs[band] = d
            rd = Gtk.Label(label='Neutral')
            rd.get_style_context().add_class('cg-read')
            self._wreads[band] = rd
            col.pack_start(nm, False, False, 0)
            col.pack_start(d, False, False, 0)
            col.pack_start(rd, False, False, 0)
            row.pack_start(col, True, True, 0)
        root.pack_start(row, False, False, 0)
        self._reach_strip = ReachStrip()
        self._reach_strip.connect('selected', lambda _w, b: self._set_active_band(b))
        root.pack_start(self._reach_strip, False, False, 0)

        panel = _card()
        self._bal_title = Gtk.Label()
        self._bal_title.set_xalign(0)
        self._bal_title.get_style_context().add_class('cg-name')
        panel.pack_start(self._bal_title, False, False, 0)
        self._s_hue = GradSlider('Colour', 0, 360, 0, '{:.0f}°', _rainbow(),
                                 'Colour (Hue): which colour is added.')
        self._s_amt = GradSlider('Amount', 0, 100, 0, '{:.0f}', [(0.42, 0.42, 0.42), (1, 0.4, 0.3)],
                                 'Amount (Saturation of the wheel): how strongly the colour is added. Small amounts go a long way.')
        self._s_hue.connect('value-changed', self._on_hue_slider)
        self._s_amt.connect('value-changed', self._on_amt_slider)
        panel.pack_start(self._s_hue, False, False, 0)
        panel.pack_start(self._s_amt, False, False, 0)
        self._s_reach = GradSlider('Reach', 0, 100, 50, '{:.0f}', [(0.15, 0.15, 0.15), (0.9, 0.9, 0.9)],
                                   'Reach (luminance range): how strongly this colour acts on its tones. '
                                   'Raise it for a stronger effect, lower it for a gentler one. '
                                   'The tones each wheel covers are fixed by GIMP.')
        self._s_reach.connect('value-changed', self._on_reach)
        panel.pack_start(self._s_reach, False, False, 0)
        root.pack_start(panel, False, False, 0)

        whole = _card()
        whole.pack_start(_sec('Whole picture'), False, False, 0)
        self._s_sat = GradSlider('Saturation', 0, 200, 100, '{:.0f}%', [(0.5, 0.5, 0.5), (0.8, 0.55, 0.35), (1.0, 0.25, 0.1)],
                                 'Saturation: how strong every colour is. 100% is unchanged, 0% is black and white.')
        self._s_sat.connect('value-changed', self._on_sat_slider, 0)
        chips = _hbox(5)
        for text, val, tip in (('Mono', 0, 'Remove all colour'), ('Muted', 70, 'Gentle, understated colour'),
                               ('Natural', 100, 'Colour as shot'), ('Rich', 130, 'Fuller, stronger colour')):
            chips.pack_start(_chip(text, tip, lambda _b, v=val: self._set_saturation_percent(v)), False, False, 0)
        self._s_con = GradSlider('Punch', -100, 100, 0, '{:+.0f}', [(0.3, 0.3, 0.3), (0.55, 0.55, 0.55), (0.9, 0.9, 0.9)],
                                 'Punch (Contrast): makes darks darker and lights lighter (right), or flatter (left).')
        self._s_con.connect('value-changed', self._on_con_slider)
        self._s_piv = GradSlider('Punch midpoint', 0, 100, 50, '{:.0f}', [(0.1, 0.1, 0.1), (0.9, 0.9, 0.9)],
                                 'Punch midpoint (Contrast pivot): the brightness that stays unchanged when you add punch.')
        self._s_piv.connect('value-changed', self._on_piv_slider)
        whole.pack_start(self._s_sat, False, False, 0)
        whole.pack_start(chips, False, False, 0)
        whole.pack_start(self._s_con, False, False, 0)
        whole.pack_start(self._s_piv, False, False, 0)
        root.pack_start(whole, False, False, 0)

        ov = _card()
        ov.pack_start(_sec('Overall colour cast'), False, False, 0)
        orow = _hbox(10)
        ocol = _vbox(3)
        onm = Gtk.Label(label=BAND_NAME[MASTER])
        onm.set_tooltip_text(BAND_TIP[MASTER])
        _mark_name(onm, 'plain')
        self._wnames[MASTER] = onm
        od = Disc(166, 'tint', tip=BAND_TIP[MASTER])
        od.connect('changed', self._on_disc, MASTER)
        self._discs[MASTER] = od
        ord_ = Gtk.Label(label='Neutral')
        ord_.get_style_context().add_class('cg-read')
        self._wreads[MASTER] = ord_
        ocol.pack_start(onm, False, False, 0)
        ocol.pack_start(od, False, False, 0)
        ocol.pack_start(ord_, False, False, 0)
        orow.pack_start(ocol, False, False, 0)
        oright = _vbox(0)
        oright.set_valign(Gtk.Align.CENTER)
        self._o_hue = GradSlider('Colour', 0, 360, 0, '{:.0f}°', _rainbow(), 'Colour (Hue): which colour is added to the whole picture.')
        self._o_amt = GradSlider('Amount', 0, 100, 0, '{:.0f}', [(0.42, 0.42, 0.42), (1, 0.4, 0.3)],
                                 'Amount (Saturation of the wheel): how strongly the colour is added. Small amounts go a long way.')
        self._o_hue.connect('value-changed', self._on_o_hue)
        self._o_amt.connect('value-changed', self._on_o_amt)
        oright.pack_start(self._o_hue, False, False, 0)
        oright.pack_start(self._o_amt, False, False, 0)
        orow.pack_start(oright, True, True, 0)
        ov.pack_start(orow, False, False, 0)
        root.pack_start(ov, False, False, 0)
        return root

    def _sync_overall(self):
        h, s = self._hs[MASTER]
        self._o_hue.set_value_quiet(h)
        self._o_amt.set_value_quiet(s * 100.0)
        self._o_amt.set_stops([(0.42, 0.42, 0.42), _hsv(h, 0.95, 1.0)])
        self._discs[MASTER].set_value(h, s)
        self._sync_readout(MASTER)

    def _on_o_hue(self, _s, v):
        self._hs[MASTER] = (v, self._hs[MASTER][1])
        self._after_wheel(MASTER)
        self._sync_overall()

    def _on_o_amt(self, _s, v):
        self._hs[MASTER] = (self._hs[MASTER][0], v / 100.0)
        self._after_wheel(MASTER)
        self._sync_overall()


    def _set_active_band(self, band):
        self._active = band
        for b in BANDS:
            self._discs[b].set_active(b == band)
            _mark_name(self._wnames[b], 'glow' if b == band else 'dim')
        if band in BANDS: self._reach_strip.active = band
        self._reach_strip.queue_draw()
        self._sync_balance_panel()

    def _sync_balance_panel(self):
        b = self._active
        h, s = self._hs[b]
        self._bal_title.set_text(BAND_NAME[b] + ' colour')
        self._s_hue.set_value_quiet(h)
        self._s_amt.set_value_quiet(s * 100.0)
        self._s_amt.set_stops([(0.42, 0.42, 0.42), _hsv(h, 0.95, 1.0)])
        self._s_reach.set_visible(b != MASTER)
        if b in BANDS: self._s_reach.set_value_quiet(self._reach[b])
        for bb in BANDS:
            self._discs[bb].set_active(bb == b)
            _mark_name(self._wnames[bb], 'glow' if bb == b else 'dim')
        self._sync_overall()
        self._refresh_reach_strip()

    def _refresh_reach_strip(self):
        for band in BANDS:
            h, s = self._hs[band]
            self._reach_strip.colours[band] = _hsv(h, 0.35 + 0.65 * min(1.0, s * 2.0), 1.0) if s > 0.01 else None
            self._reach_strip.reach[band] = self._reach[band]
        self._reach_strip.queue_draw()

    def _sync_readout(self, band):
        h, s = self._hs[band]
        lbl = self._wreads[band]
        if s < 0.005:
            lbl.set_text('Neutral'); lbl.set_tooltip_text('')
        else:
            lbl.set_text('%s · %d' % (_hue_name(h), round(s * 100)))
            lbl.set_tooltip_text('Hue %.0f°  Saturation %.2f' % (h, s))

    def _band_apply(self, band, rgb):
        self._scopes_dirty = True
        if band == MASTER:
            self._master_canonical_rgb = tuple(float(x) for x in rgb)
            if self._master_timer is None:
                self._master_timer = GLib.timeout_add(PREVIEW_FAST_MS, self._flush_master_rgb)
        else:
            self._push_band_colour(band, *rgb)

    def _after_wheel(self, band):
        h, s = self._hs[band]
        self._band_apply(band, wheel_to_rgb(h, s))
        self._sync_readout(band)
        self._refresh_reach_strip()

    def _on_disc(self, _d, angle, r, band):
        self._hs[band] = (angle, r)
        self._after_wheel(band)
        if band == MASTER:
            self._sync_overall()
        elif band == self._active:
            self._s_hue.set_value_quiet(angle)
            self._s_amt.set_value_quiet(r * 100.0)
            self._s_amt.set_stops([(0.42, 0.42, 0.42), _hsv(angle, 0.95, 1.0)])

    def _on_hue_slider(self, _s, v):
        b = self._active
        self._hs[b] = (v, self._hs[b][1])
        self._discs[b].set_value(*self._hs[b])
        self._s_amt.set_stops([(0.42, 0.42, 0.42), _hsv(v, 0.95, 1.0)])
        self._after_wheel(b)

    def _on_amt_slider(self, _s, v):
        b = self._active
        self._hs[b] = (self._hs[b][0], v / 100.0)
        self._discs[b].set_value(*self._hs[b])
        self._after_wheel(b)

    def _on_reach(self, _s, v):
        b = self._active
        if b not in BANDS: return
        self._reach[b] = float(v)
        self._ranges.update(_reach_to_ranges(b, float(v)))
        self._reach_strip.reach[b] = float(v)
        self._reach_strip.queue_draw()
        self._scopes_dirty = True
        self._schedule_range(b)

    def _schedule_range(self, band):
        if self._range_timers[band] is None:
            self._range_timers[band] = GLib.timeout_add(PREVIEW_FAST_MS, self._flush_range, band)

    def _flush_range(self, band):
        self._range_timers[band] = None
        self._preview.update_ranges(dict(self._ranges), band=band)
        return False

    def _set_saturation_percent(self, pct):
        self._s_sat.set_value_quiet(pct)
        self._s_sat2.set_value_quiet(pct)
        self._on_saturation_changed(pct / 100.0)

    def _on_sat_slider(self, s, v, which):
        (self._s_sat2 if which == 0 else self._s_sat).set_value_quiet(v)
        self._on_saturation_changed(v / 100.0)

    def _on_saturation_changed(self, value):
        self._sat_value = float(value)
        self._scopes_dirty = True
        if self._sat_timer is None:
            self._sat_timer = GLib.timeout_add(PREVIEW_FAST_MS, self._flush_saturation_pending)

    def _flush_saturation_pending(self):
        self._sat_timer = None
        self._preview.update_saturation(self._sat_value)
        return False

    def _on_con_slider(self, _s, v):
        self._contrast = v / 100.0
        self._queue_contrast()

    def _on_piv_slider(self, _s, v):
        self._pivot = v / 100.0
        self._queue_contrast()

    def _queue_contrast(self):
        self._scopes_dirty = True
        if self._contrast_timer is None:
            self._contrast_timer = GLib.timeout_add(PREVIEW_FAST_MS, self._flush_contrast_pending)

    def _flush_contrast_pending(self):
        self._contrast_timer = None
        self._preview.set_contrast(self._contrast, self._pivot)
        self._schedule_targeted()
        return False

    @staticmethod
    def _tone_default_range(i):
        return ColourGradingDialog._neutral_tone_state()[i]['range']

    def _build_tone(self):
        root = _vbox(8)
        pick = self._make_pick_button('Pick a pixel on the picture and select the tonal zone closest to its brightness',
                                      self._picked_tone, 'Pick tone')
        root.pack_start(self._head('Tone', self._reset_tone, pick), False, False, 0)
        root.pack_start(_hint('Five zones from black to white. Choose a zone, then turn the dial to lighten or darken only those tones.'),
                        False, False, 0)
        self._tone_strip = ToneStrip(self._tone_state)
        self._tone_strip.connect('selected', lambda _w, i: self._select_tone(i))
        self._tone_strip.connect('moved', lambda _w, i, v: self._tone_point_moved(i, v))
        self._tone_strip.connect('reset', lambda _w, i: self._reset_tone_point(i))
        self._tone_strip.connect('released', self._flush_targeted_release)
        root.pack_start(self._tone_strip, False, False, 0)
        card = _card()
        self._tone_name = Gtk.Label()
        self._tone_name.set_xalign(0)
        self._tone_name.get_style_context().add_class('cg-name')
        card.pack_start(self._tone_name, False, False, 0)
        self._tone_dial = ToneDial()
        self._tone_dial.connect('value-changed', lambda _w, v: self._tone_set('brightness', v))
        self._tone_dial.connect('released', self._flush_targeted_release)
        card.pack_start(self._tone_dial, False, False, 0)
        self._tone_sl = {
            'range': GradSlider('Width', 2, 50, 18, '{:.0f}%', [(0.2, 0.2, 0.2), (0.85, 0.85, 0.85)],
                                'Width (range): how many tones around the dot are changed.'),
            'spread': GradSlider('Softness', 0, 100, 50, '{:.0f}%', [(0.7, 0.7, 0.7), (0.3, 0.3, 0.3)],
                                 'Softness (feather): how gently the change fades out at its edges.'),
        }
        for k, s in self._tone_sl.items():
            s.connect('value-changed', lambda _w, v, key=k: self._tone_set(key, v / 100.0))
            s.connect('released', self._flush_targeted_release)
            card.pack_start(s, False, False, 0)
        root.pack_start(card, False, False, 0)
        return root

    def _sync_tone_ui(self):
        if not hasattr(self, '_tone_sl') or not self._tone_state: return
        i = max(0, min(len(self._tone_state) - 1, self._tone_sel))
        self._tone_sel = i
        p = self._tone_state[i]
        self._tone_strip.sel = i
        self._tone_name.set_markup('<b>%s</b>' % GLib.markup_escape_text(str(p.get('name', 'Tone'))))
        self._tone_dial.set_value_quiet(float(p.get('brightness', 0.0)))
        self._tone_sl['range'].default = self._tone_default_range(i) * 100.0
        self._tone_sl['range'].set_value_quiet(float(p.get('range', 0.18)) * 100.0)
        self._tone_sl['spread'].set_value_quiet(float(p.get('spread', 0.5)) * 100.0)
        self._tone_strip.queue_draw()

    def _select_tone(self, i):
        if not self._tone_state: return
        self._tone_sel = max(0, min(len(self._tone_state) - 1, int(i)))
        self._sync_tone_ui()

    def _tone_set(self, key, v):
        if 0 <= self._tone_sel < len(self._tone_state):
            self._tone_state[self._tone_sel][key] = float(v)
            self._tone_strip.queue_draw()
            self._schedule_targeted()

    def _tone_point_moved(self, i, v):
        if 0 <= i < len(self._tone_state):
            self._tone_state[i]['pos'] = float(v)
            self._tone_strip.queue_draw()
            self._schedule_targeted()

    def _reset_tone_point(self, i):
        if 0 <= i < len(self._tone_state):
            self._tone_state[i].update(self._neutral_tone_state()[i])
            self._select_tone(i)
            self._schedule_targeted()
            self._flush_targeted_release()

    def _picked_tone(self, _button, colour):
        lin = self._picked_linear(colour)
        if lin is None or not self._tone_state: return
        lum = linear_rgb_to_lch(*lin)[0] / 100.0
        self._select_tone(min(range(len(self._tone_state)), key=lambda j: abs(float(self._tone_state[j].get('pos', 0.5)) - lum)))

    def _build_tonal(self):
        root = _vbox(8)
        pick = self._make_pick_button('Pick a colour from the screen: it becomes the tint of the zone that matches its brightness',
                                      self._picked_tonal_colour, 'Pick colour')
        root.pack_start(self._head('Tonal colour', self._reset_tonal, pick), False, False, 0)
        root.pack_start(_hint('Tint shadows, midtones and highlights separately. Drag the dot: the direction chooses the colour, '
                              'the distance from the centre chooses how strong.'), False, False, 0)
        row = _hbox(6)
        row.set_homogeneous(True)
        self._tc_discs, self._tc_names = [], []
        for i, p in enumerate(self._tonal_colour_state):
            col = _vbox(3)
            nm = Gtk.Label(label=p['name'])
            nm.set_tooltip_text('%s zone: tint only the %s of the picture.' % (p['name'], p['name'].lower()))
            d = Disc(166, 'tint', tip='%s tint: direction is the colour, distance is the strength.' % p['name'])
            d.connect('changed', self._on_tc_disc, i)
            d.connect('picked', lambda _w, j=i: self._tc_set_active(j))
            d.connect('released', self._flush_targeted_release)
            col.pack_start(nm, False, False, 0)
            col.pack_start(d, False, False, 0)
            row.pack_start(col, True, True, 0)
            self._tc_discs.append(d)
            self._tc_names.append(nm)
        root.pack_start(row, False, False, 0)
        card = _card()
        self._tc_title = Gtk.Label()
        self._tc_title.set_xalign(0)
        self._tc_title.get_style_context().add_class('cg-name')
        card.pack_start(self._tc_title, False, False, 0)
        specs = (('hue', 'Colour', 0, 360, 0, '{:.0f}°', _rainbow(), 'Colour (Hue): the tint colour for this zone.', 1.0),
                 ('strength', 'Strength', 0, 100, 0, '{:.0f}', [(0.42, 0.42, 0.42), (1, 0.4, 0.3)],
                  'Strength (Saturation): how strongly this zone is tinted. 0 means no tint.', 1.0),
                 ('luminance', 'Lightness', -100, 100, 0, '{:+.0f}', [(0.05, 0.05, 0.05), (0.6, 0.6, 0.6), (1, 1, 1)],
                  'Lightness (Luminance): brightens or darkens only this zone.', 1.0),
                 ('range', 'Width', 2, 50, 20, '{:.0f}%', [(0.2, 0.2, 0.2), (0.85, 0.85, 0.85)],
                  'Width (range): how many tones around this zone are tinted.', 0.01),
                 ('spread', 'Softness', 0, 100, 55, '{:.0f}%', [(0.7, 0.7, 0.7), (0.3, 0.3, 0.3)],
                  'Softness (feather): how gently the tint fades out at its edges.', 0.01))
        self._tc_sl = {}
        for key, label, lo, hi, d0, fmt, stops, tip, scale in specs:
            s = GradSlider(label, lo, hi, d0, fmt, stops, tip)
            s.scale = scale
            s.connect('value-changed', self._on_tc_slider, key)
            s.connect('released', self._flush_targeted_release)
            card.pack_start(s, False, False, 0)
            self._tc_sl[key] = s
        root.pack_start(card, False, False, 0)
        return root

    def _tc_set_active(self, i):
        if self._tc_active != i:
            self._tc_active = i
            self._sync_tonal_ui()

    def _sync_tonal_ui(self):
        if not hasattr(self, '_tc_sl'): return
        neutral = self._neutral_tonal_colour_state()
        for j, (d, nm) in enumerate(zip(self._tc_discs, self._tc_names)):
            p = self._tonal_colour_state[j]
            d.set_value(float(p.get('hue', 0.0)), float(p.get('strength', 0.0)) / 100.0)
            d.set_active(j == self._tc_active)
            _mark_name(nm, 'glow' if j == self._tc_active else 'dim')
        i = self._tc_active
        p = self._tonal_colour_state[i]
        self._tc_title.set_text(p['name'] + ' tint')
        for key, s in self._tc_sl.items():
            v = float(p.get(key, 0.0))
            s.set_value_quiet(v / s.scale if s.scale != 1.0 else v)
            dflt = float(neutral[i].get(key, 0.0))
            s.default = dflt / s.scale if s.scale != 1.0 else dflt
        h = float(p['hue'])
        self._tc_sl['strength'].set_stops([(0.42, 0.42, 0.42), _hsv(h, 0.95, 1.0)])

    def _on_tc_disc(self, _d, angle, r, i):
        self._tc_active = i
        p = self._tonal_colour_state[i]
        if r > 1e-6: p['hue'] = float(angle)
        p['strength'] = float(r) * 100.0
        self._sync_tonal_ui()
        self._schedule_targeted()

    def _on_tc_slider(self, s, v, key):
        p = self._tonal_colour_state[self._tc_active]
        val = float(v) * s.scale
        p[key] = val % 360.0 if key == 'hue' else val
        self._sync_tonal_ui()
        self._schedule_targeted()

    def _picked_tonal_colour(self, _button, colour):
        lin = self._picked_linear(colour)
        if lin is None or not self._tonal_colour_state: return
        L, _C, _h = linear_rgb_to_lch(*lin)
        lum = L / 100.0
        h, sat, _v = colorsys.rgb_to_hsv(*[linear_to_srgb(c) for c in lin])
        i = min(range(len(self._tonal_colour_state)), key=lambda j: abs(float(self._tonal_colour_state[j].get('pos', 0.5)) - lum))
        self._tc_active = i
        p = self._tonal_colour_state[i]
        p['hue'] = (h * 360.0) % 360.0
        p['strength'] = max(float(p.get('strength', 0)), sat * 35.0)
        self._sync_tonal_ui()
        self._schedule_targeted()
        self._flush_targeted_release()

    def _build_hue(self):
        root = _vbox(8)
        pick = self._make_pick_button('Pick a colour anywhere on screen and add it as a target',
                                      self._picked_hue, 'Pick colour')
        delete = Gtk.Button(label='Delete')
        delete.set_tooltip_text('Remove the selected target colour and its changes')
        delete.set_valign(Gtk.Align.CENTER)
        delete.connect('clicked', lambda *_: self._delete_hue_sample())
        root.pack_start(self._head('Hue adjustments', self._reset_hue, delete, pick), False, False, 0)
        root.pack_start(_hint('Change one family of colours without touching the rest, for example make only the greens more golden. '
                              'Click the strip or the ring to choose a colour, then shift it.'), False, False, 0)
        self._hue_strip = HueTargets(self._hue_adjust_state)
        self._hue_strip.connect('add', lambda _w, h: self._add_hue_sample(h))
        self._hue_strip.connect('selected', lambda _w, i: self._select_hue_sample(i))
        self._hue_strip.connect('moved', lambda _w, i, h: self._hue_move_source(i, h))
        self._hue_strip.connect('reset', lambda _w, i: self._reset_hue_target(i))
        self._hue_strip.connect('released', self._flush_targeted_release)
        root.pack_start(self._hue_strip, False, False, 0)
        self._hue_ring = HueRing(self._hue_adjust_state)
        self._hue_ring.connect('add', lambda _w, h: self._add_hue_sample(h))
        self._hue_ring.connect('selected', lambda _w, i: self._select_hue_sample(i))
        self._hue_ring.connect('moved', lambda _w, i, h: self._hue_move_source(i, h))
        self._hue_ring.connect('shifted', lambda _w, i, v: self._hue_shift_dragged(i, v))
        self._hue_ring.connect('reset', lambda _w, i: self._reset_hue_target(i))
        self._hue_ring.connect('released', self._flush_targeted_release)
        root.pack_start(self._hue_ring, False, False, 0)
        self._hue_info = Gtk.Label()
        self._hue_info.set_xalign(0)
        self._hue_info.get_style_context().add_class('cg-name')
        root.pack_start(self._hue_info, False, False, 0)
        card = _card()
        d = self._hue_defaults()
        specs = (('hue', 'Hue shift', -180, 180, '{:+.0f}°', _rainbow(), 'Hue shift: turns the chosen colours around the colour wheel, e.g. greens towards yellow or cyan.', 1.0),
                 ('saturation', 'Saturation', -100, 100, '{:+.0f}', [(0.5, 0.5, 0.5), (0.8, 0.55, 0.35), (1, 0.3, 0.15)], 'Saturation: makes only the chosen colours stronger or weaker.', 1.0),
                 ('luminance', 'Brightness', -100, 100, '{:+.0f}', [(0.05, 0.05, 0.05), (0.6, 0.6, 0.6), (1, 1, 1)], 'Brightness (Luminance): makes only the chosen colours lighter or darker.', 1.0),
                 ('range', 'Spread', 2, 90, '{:.0f}°', [(0.25, 0.25, 0.25), (0.85, 0.85, 0.85)], 'Spread (Range): how wide a slice of the colour wheel is included, in degrees either side of the target. Small keeps to one exact colour, large takes in its neighbours too.', 1.0),
                 ('spread', 'Softness', 0, 100, '{:.0f}%', [(0.7, 0.7, 0.7), (0.3, 0.3, 0.3)], 'Softness (feather): how gently the change fades out at the edges.', 0.01))
        self._hue_sl = {}
        for key, label, lo, hi, fmt, stops, tip, scale in specs:
            dv = d[key] / scale if scale != 1.0 else d[key]
            s = GradSlider(label, lo, hi, dv, fmt, stops, tip)
            s.scale = scale
            s.connect('value-changed', self._on_hue_slider2, key)
            s.connect('released', self._flush_targeted_release)
            card.pack_start(s, False, False, 0)
            self._hue_sl[key] = s
        root.pack_start(card, False, False, 0)
        self._hue_card = card
        whole = _card()
        whole.pack_start(_sec('Whole picture'), False, False, 0)
        self._s_ghue = GradSlider('Hue shift', -180, 180, 0, '{:+.0f}°', _rainbow(),
                                  'Hue shift (Global hue rotation): turns every colour in the picture around the colour wheel at once. '
                                  'Works on its own, whatever target colours are chosen above.')
        self._s_ghue.connect('value-changed', self._on_global_hue)
        self._s_ghue.connect('released', self._flush_targeted_release)
        whole.pack_start(self._s_ghue, False, False, 0)
        root.pack_start(whole, False, False, 0)
        return root

    def _on_global_hue(self, _s, v):
        self._global_hue = float(v)
        self._schedule_targeted()

    def _sync_hue_ui(self):
        if not hasattr(self, '_hue_sl'): return
        self._s_ghue.set_value_quiet(float(self._global_hue))
        n = len(self._hue_adjust_state)
        if self._hue_sel >= n: self._hue_sel = n - 1
        i = self._hue_sel
        ok = 0 <= i < n
        self._hue_card.set_sensitive(ok)
        for k, s in self._hue_sl.items():
            v = float(self._hue_adjust_state[i].get(k, 0.0)) if ok else self._hue_defaults()[k]
            s.set_value_quiet(v / s.scale if s.scale != 1.0 else v)
        if ok:
            p = self._hue_adjust_state[i]
            self._hue_info.set_markup('<b>Target %d of %d</b> — %s' % (i + 1, n, _hue_name(float(p.get('source_hue', 0)))))
            self._hue_info.set_tooltip_text('Source hue %.0f°' % float(p.get('source_hue', 0)))
        else:
            self._hue_info.set_text('No target colour yet')
        self._hue_strip.sel = i
        self._hue_ring.sel = i
        self._hue_strip.queue_draw()
        self._hue_ring.queue_draw()

    def _add_hue_sample(self, h):
        s = {'source_hue': float(h)}
        s.update(self._hue_defaults())
        self._hue_adjust_state.append(s)
        self._hue_sel = len(self._hue_adjust_state) - 1
        self._sync_hue_ui()

    def _select_hue_sample(self, i):
        self._hue_sel = i
        self._sync_hue_ui()

    def _delete_hue_sample(self):
        i = self._hue_sel
        if 0 <= i < len(self._hue_adjust_state):
            self._hue_adjust_state.pop(i)
            self._hue_sel = min(i, len(self._hue_adjust_state) - 1)
            self._sync_hue_ui()
            self._schedule_targeted()
            self._flush_targeted_release()

    def _on_hue_slider2(self, s, v, key):
        i = self._hue_sel
        if 0 <= i < len(self._hue_adjust_state):
            self._hue_adjust_state[i][key] = float(v) * s.scale
            self._hue_strip.queue_draw()
            self._hue_ring.queue_draw()
            self._schedule_targeted()

    def _hue_move_source(self, i, h):
        if 0 <= i < len(self._hue_adjust_state):
            self._hue_adjust_state[i]['source_hue'] = float(h) % 360.0
            self._sync_hue_ui()
            self._schedule_targeted()

    def _hue_shift_dragged(self, i, v):
        if 0 <= i < len(self._hue_adjust_state):
            self._hue_adjust_state[i]['hue'] = max(-180.0, min(180.0, float(v)))
            self._sync_hue_ui()
            self._schedule_targeted()

    def _reset_hue_target(self, i):
        if 0 <= i < len(self._hue_adjust_state):
            self._hue_adjust_state[i].update(self._hue_defaults())
            self._sync_hue_ui()
            self._schedule_targeted()
            self._flush_targeted_release()

    def _picked_hue(self, _button, colour):
        lin = self._picked_linear(colour)
        if lin is None: return
        _L, C, h = linear_rgb_to_lch(*lin)
        if C < 4.0:
            self._hue_info.set_text('That pixel is grey, so it has no colour to pick. Pick a coloured pixel.')
            return
        self._add_hue_sample(h)
        self._schedule_targeted()

    def _build_colour(self):
        root = _vbox(8)
        root.pack_start(self._head('Colour', self._reset_colour), False, False, 0)
        root.pack_start(_hint('Re-mix the three colour channels. Drag the dot: around the circle changes the colour, '
                              'outwards makes it purer.'), False, False, 0)
        row = _hbox(6)
        row.set_homogeneous(True)
        self._primary_controls = {}
        for key, title, sub, base in (('red', 'Reds', 'Red primary', 0.0), ('green', 'Greens', 'Green primary', 120.0),
                                      ('blue', 'Blues', 'Blue primary', 240.0)):
            col = _vbox(3)
            nm = Gtk.Label(label=title)
            _mark_name(nm, 'plain')
            nm.set_tooltip_text('%s: changes how pure %s is built from the camera\'s %s channel.' % (sub, key, key))
            d = Disc(166, 'shift', base=base, tip='%s: angle shifts the colour, distance makes it purer.' % sub)
            d.set_active(False)
            d.connect('changed', self._on_primary_disc, key)
            d.connect('picked', lambda _w, k=key: self._set_primary_active(k))
            d.connect('released', self._flush_targeted_release)
            rd = Gtk.Label(label='Untouched')
            rd.get_style_context().add_class('cg-read')
            col.pack_start(nm, False, False, 0)
            col.pack_start(d, False, False, 0)
            col.pack_start(rd, False, False, 0)
            row.pack_start(col, True, True, 0)
            self._primary_controls[key] = {'disc': d, 'readout': rd, 'name': nm}
        root.pack_start(row, False, False, 0)
        card = _card()
        card.pack_start(_sec('Whole-picture colour'), False, False, 0)
        self._gc_sl = {}
        specs = (('temperature', 'Warmth', -100, 100, 0, [(0.25, 0.5, 0.88), (0.84, 0.84, 0.84), (0.94, 0.58, 0.17)],
                  'Warmth (Temperature): shifts the whole picture between cool and warm, like changing white balance.', ('Cooler', 'Warmer')),
                 ('tint', 'Green ↔ Magenta', -100, 100, 0, [(0.25, 0.71, 0.42), (0.84, 0.84, 0.84), (0.75, 0.25, 0.66)],
                  'Green ↔ Magenta (Tint): corrects fluorescent and mixed lighting.', ('Greener', 'More magenta')),
                 ('chroma', 'Vibrance', -100, 100, 0, [(0.55, 0.55, 0.55), (0.35, 0.6, 0.75), (0.9, 0.3, 0.4)],
                  'Vibrance (Chroma): boosts weak colours more than strong ones.', ('Subtler', 'Richer')))
        for key, label, lo, hi, d0, stops, tip, ends in specs:
            s = GradSlider(label, lo, hi, d0, '{:+.0f}', stops, tip, ends=ends)
            s.connect('value-changed', lambda _w, v, k=key: self._global_colour_changed(k, v))
            s.connect('released', self._flush_targeted_release)
            self._gc_sl[key] = s
            card.pack_start(s, False, False, 0)
            if key == 'temperature':
                chips = _hbox(5)
                for text, val, tip in (('Tungsten fix', -45, 'Cool down orange indoor light'), ('Neutral', 0, 'No warmth change'),
                                       ('Golden hour', 40, 'Warm, sunset feel'), ('Overcast fix', 22, 'Warm up flat grey daylight')):
                    chips.pack_start(_chip(text, tip, lambda _b, v=val: self._set_warmth(v)), False, False, 0)
                card.pack_start(chips, False, False, 0)
        self._s_sat2 = GradSlider('Saturation', 0, 200, 100, '{:.0f}%', [(0.5, 0.5, 0.5), (0.8, 0.55, 0.35), (1.0, 0.25, 0.1)],
                                  'Saturation: how strong every colour is. 100% is unchanged, 0% is black and white. Same control as on the Balance tab.',
                                  ends=('Black and white', 'Double'))
        self._s_sat2.connect('value-changed', self._on_sat_slider, 1)
        card.pack_start(self._s_sat2, False, False, 0)
        root.pack_start(card, False, False, 0)
        chk = _card()
        row2 = _hbox(8)
        txt = _vbox(2)
        t = Gtk.Label()
        t.set_markup('<b>Colour health check</b>')
        t.set_xalign(0)
        txt.pack_start(t, False, False, 0)
        txt.pack_start(_hint('Shows the picture\'s colour profile and bit depth, and warns if the grade is pushing too hard.'), False, False, 0)
        self._colour_diag_button = Gtk.Button(label='Open report…')
        self._colour_diag_button.set_valign(Gtk.Align.CENTER)
        self._colour_diag_button.connect('clicked', self._show_colour_diagnostics)
        row2.pack_start(txt, True, True, 0)
        row2.pack_end(self._colour_diag_button, False, False, 0)
        chk.pack_start(row2, False, False, 0)
        root.pack_start(chk, False, False, 0)
        return root

    def _set_warmth(self, v):
        self._gc_sl['temperature'].set_value_quiet(v)
        self._global_colour_changed('temperature', v)
        self._flush_targeted_release()

    def _set_primary_active(self, key):
        self._prim_active = key
        for k, c in self._primary_controls.items():
            _mark_name(c['name'], 'glow' if k == key else 'dim')
            c['disc'].set_active(k == key or self._primary_modified(k))

    def _primary_modified(self, key):
        p = self._primaries_state[key]
        return abs(p['hue']) > 0.5 or p['purity'] > 0.5

    def _on_primary_disc(self, _d, shift, r, key):
        if getattr(self, '_prim_active', None) != key: self._set_primary_active(key)
        self._primary_changed(key, 'hue', shift)
        self._primary_changed(key, 'purity', r * 100.0)
        self._sync_primary_readout(key)

    def _sync_primary_readout(self, key):
        p = self._primaries_state[key]
        c = self._primary_controls[key]
        c['disc'].set_value(p['hue'], p['purity'] / 100.0)
        on = self._primary_modified(key)
        c['disc'].set_active(on or key == getattr(self, '_prim_active', None))
        c['readout'].set_text('Shift %+.0f° · Purity %.0f' % (p['hue'], p['purity']) if on else 'Untouched')

    def _build_scopes(self):
        root = _vbox(8)
        clear = Gtk.Button(label='Clear')
        clear.set_tooltip_text('Clear the scope')
        clear.connect('clicked', self._scope_clear_cb)
        root.pack_start(self._head('Scopes', None, clear), False, False, 0)
        self._scope_display = ScopeDisplay()
        self._scope_display.set_size_request(-1, 300)
        row = _hbox(6)
        self._scope_seg = Segmented(['Brightness', 'Waveform', 'Parade', 'Colour'],
                                    ['Histogram: how many pixels at each brightness', 'RGB Waveform: brightness across the picture',
                                     'RGB Parade: red, green and blue side by side', 'Vectorscope: which colours and how strong'])
        self._scope_seg.connect('chosen', lambda _w, i: self._scope_display.set_mode(ScopeDisplay.MODES[i]))
        row.pack_start(self._scope_seg, False, False, 0)
        upd = Gtk.Button(label='Update')
        upd.set_tooltip_text('Read the picture again and redraw the scope')
        upd.connect('clicked', lambda *_: self._refresh_scope())
        row.pack_end(upd, False, False, 0)
        root.pack_start(row, False, False, 0)
        root.pack_start(self._scope_display, True, True, 0)
        card = _card()
        self._scope_quick = Gtk.Label(label='Open this tab or press Update to read the picture.')
        self._scope_quick.set_xalign(0)
        self._scope_quick.set_line_wrap(True)
        card.pack_start(self._scope_quick, False, False, 0)
        self._scope_status = _hint('Scopes are read when this tab opens, so nothing is measured while you edit. '
                                   'They use a reduced 8-bit copy of the preview.')
        card.pack_start(self._scope_status, False, False, 0)
        root.pack_start(card, False, False, 0)
        return root

    def _scope_clear_cb(self, *_):
        self._scope_display.data = None
        self._scope_display.queue_draw()

    def _on_notebook_switch_page(self, notebook, page, page_num):
        if page_num == 5:
            GLib.idle_add(self._initial_scope_refresh)

    def _initial_scope_refresh(self):
        self._refresh_scope()
        return False

    def _refresh_scope(self, *_):
        try:
            self._flush_managed_preview_now()
            Gimp.displays_flush()
            label = self._scope_display.refresh(self._image, getattr(self._preview, '_preview_layer', None))
            self._scopes_dirty = False
            h = self._scope_display._hist
            total = float(sum(h[0]) or 1)
            black = max(c[0] for c in h) / total * 100.0
            white = max(c[255] for c in h) / total * 100.0
            self._scope_quick.set_markup('Pure black: <b>%.1f%%</b>   ·   Pure white: <b>%.1f%%</b>   %s' % (
                black, white, '— shadows may be clipping' if black > 1.5 else ('— highlights may be clipping' if white > 1.5 else '— looking safe')))
            self._scope_status.set_text('Read from: %s (reduced 8-bit sample).' % label)
        except Exception as exc:
            msg = 'Scope error — ' + str(exc)
            self._scope_display.data = None
            self._scope_display.error = msg
            self._scope_display.queue_draw()
            self._scope_quick.set_text(msg)

    def _build_looks(self):
        root = _vbox(8)
        neutral = Gtk.Button(label='Neutral')
        neutral.set_tooltip_text('Clear the grade without loading another look')
        neutral.connect('clicked', lambda *_: self._apply_builtin_preset('neutral'))
        root.pack_start(self._head('Looks', None, neutral), False, False, 0)
        self._preset_status = _hint('Pick a look. The colour bar shows the tint each look adds from dark to light. Every control stays editable afterwards.')
        root.pack_start(self._preset_status, False, False, 0)
        self._look_search = Gtk.Entry()
        self._look_search.set_placeholder_text('Search looks…')
        self._look_search.connect('changed', lambda *_: self._look_grid.invalidate_filter())
        root.pack_start(self._look_search, False, False, 0)
        self._look_cat = 'All'
        chips = Gtk.FlowBox()
        chips.set_selection_mode(Gtk.SelectionMode.NONE)
        chips.set_max_children_per_line(9)
        chips.set_column_spacing(4)
        chips.set_row_spacing(4)
        self._cat_buttons = {}
        for cat in LOOK_CAT_ORDER:
            b = _chip(cat, 'Show %s looks' % cat.lower() if cat != 'All' else 'Show every look', lambda _b, c=cat: self._set_look_cat(c))
            self._cat_buttons[cat] = b
            chips.add(b)
        _set_on(self._cat_buttons['All'], True)
        root.pack_start(chips, False, False, 0)
        self._look_grid = Gtk.FlowBox()
        self._look_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self._look_grid.set_min_children_per_line(3)
        self._look_grid.set_max_children_per_line(3)
        self._look_grid.set_homogeneous(True)
        self._look_grid.set_row_spacing(6)
        self._look_grid.set_column_spacing(6)
        self._look_grid.set_filter_func(self._look_filter)
        self._look_buttons = {}
        for key, label, tip, recipe in PRESET_LOOKS:
            b = Gtk.Button()
            b.get_style_context().add_class('cg-card-btn')
            b.set_tooltip_text(tip)
            v = _vbox(3)
            v.pack_start(LookSwatch(self._look_state(recipe)), False, False, 0)
            nm = Gtk.Label(label=label)
            nm.set_ellipsize(Pango.EllipsizeMode.END)
            nm.set_max_width_chars(14)
            v.pack_start(nm, False, False, 0)
            b.add(v)
            b.connect('clicked', self._look_clicked, key)
            self._look_grid.add(b)
            b.get_parent().look_key = key
            self._look_buttons[key] = b
        root.pack_start(self._look_grid, False, False, 0)
        self._s_look = GradSlider('Look strength', 0, 100, 100, '{:.0f}%', [(0.3, 0.3, 0.3), (0.29, 0.64, 1.0)],
                                  'Look strength: blend between your original colours (left) and the full look (right).')
        self._s_look.connect('released', lambda *_: self._look_strength_released())
        root.pack_start(self._s_look, False, False, 0)

        root.pack_start(_sec('My looks'), False, False, 4)
        self._user_preset_box = Gtk.FlowBox()
        self._user_preset_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._user_preset_box.set_min_children_per_line(3)
        self._user_preset_box.set_max_children_per_line(3)
        self._user_preset_box.set_row_spacing(6)
        self._user_preset_box.set_column_spacing(6)
        self._user_preset_box.set_homogeneous(True)
        root.pack_start(self._user_preset_box, False, False, 0)
        r1 = _hbox(6)
        save = Gtk.Button(label='Save current look…')
        save.set_tooltip_text('Save the current controls as your own look')
        save.connect('clicked', self._save_user_preset)
        dele = Gtk.Button(label='Delete…')
        dele.set_tooltip_text('Choose saved looks to delete')
        dele.connect('clicked', self._delete_user_presets)
        ex = Gtk.Button(label='Export…')
        ex.set_tooltip_text('Save the current look to a file you can share')
        ex.connect('clicked', self._export_preset)
        im = Gtk.Button(label='Import…')
        im.set_tooltip_text('Load a look file')
        im.connect('clicked', self._import_preset)
        for b in (save, dele, ex, im): r1.pack_start(b, False, False, 0)
        root.pack_start(r1, False, False, 0)
        root.pack_start(_sec('LUT looks'), False, False, 4)
        root.pack_start(_hint('A LUT is a ready-made colour recipe file (.cube, or a HaldCLUT .png from RawTherapee or G\'MIC). '
                              'Choose the folder where you keep yours. The LUT is applied first, then your adjustments sit on top.'),
                        False, False, 0)
        fr = _hbox(6)
        self._lut_folder_label = Gtk.Label(label='No folder chosen')
        self._lut_folder_label.set_xalign(0)
        self._lut_folder_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._lut_folder_label.get_style_context().add_class('cg-read')
        fr.pack_start(self._lut_folder_label, True, True, 0)
        choose = Gtk.Button(label='Choose folder…')
        choose.set_tooltip_text('Pick the folder that holds your LUT files. Sub-folders are included.')
        choose.connect('clicked', self._choose_lut_folder)
        get = Gtk.Button(label='Get free LUTs…')
        get.set_tooltip_text('Links to free, open-source LUT collections')
        get.connect('clicked', self._show_lut_links)
        fr.pack_end(get, False, False, 0)
        fr.pack_end(choose, False, False, 0)
        root.pack_start(fr, False, False, 0)
        self._lut_combo = Gtk.ComboBoxText()
        self._lut_combo.append_text('No LUT')
        self._lut_combo.set_active(0)
        self._lut_combo.set_tooltip_text('Pick a LUT from your folder to preview it on the picture')
        self._lut_combo.connect('changed', self._lut_chosen)
        root.pack_start(self._lut_combo, False, False, 0)
        self._s_lutk = GradSlider('LUT strength', 0, 100, 100, '{:.0f}%', [(0.3, 0.3, 0.3), (0.29, 0.64, 1.0)],
                                  'LUT strength: blend between your original colours (left) and the full LUT (right).')
        self._s_lutk.connect('released', lambda *_: self._lut_strength_released())
        root.pack_start(self._s_lutk, False, False, 0)
        self._lut_status = _hint('No LUT loaded.')
        root.pack_start(self._lut_status, False, False, 0)
        self._lut_paths = []
        self._lut_current = None
        self._lut_busy = False

        self._reload_user_presets()
        self._lut_init_folder()
        return root

    def _set_look_cat(self, cat):
        self._look_cat = cat
        for c, b in self._cat_buttons.items(): _set_on(b, c == cat)
        self._look_grid.invalidate_filter()

    def _look_filter(self, child):
        key = getattr(child, 'look_key', None)
        if key is None: return True
        label, tip, _r = PRESET_INDEX[key]
        if self._look_cat != 'All' and LOOK_CATS.get(key) != self._look_cat: return False
        q = self._look_search.get_text().strip().lower()
        return not q or q in label.lower() or q in tip.lower() or q in LOOK_CATS.get(key, '').lower()

    def _mark_look(self, key):
        self._last_look = key
        for k, b in self._look_buttons.items(): _set_on(b, k == key)

    def _look_clicked(self, _b, key):
        self._apply_builtin_preset(key)

    def _look_strength_released(self):
        if self._last_look: self._apply_builtin_preset(self._last_look)

    def _blend_state(self, st, k):
        out = json.loads(json.dumps(st))
        def lerp(a, b=0.0): return b + (float(a) - b) * k
        out['bands'] = {b: [lerp(x) for x in st['bands'][b]] for b in st['bands']}
        out['master'] = [lerp(x) for x in st['master']]
        out['saturation'] = lerp(st['saturation'], 1.0)
        out['contrast'] = lerp(st['contrast'])
        for t in out['tone']: t['brightness'] = lerp(t['brightness'])
        for t in out['tonal_colour']:
            t['strength'] = lerp(t['strength']); t['luminance'] = lerp(t['luminance'])
        for h in out['hue_adjustments']:
            for f in ('hue', 'saturation', 'luminance'): h[f] = lerp(h[f])
        out['global_hue'] = lerp(st.get('global_hue', 0.0))
        for p in out['primaries'].values():
            p['hue'] = lerp(p['hue']); p['purity'] = lerp(p['purity'])
        gc = out['global_colour']
        for f in ('temperature', 'tint', 'chroma'): gc[f] = lerp(gc[f])
        gc['saturation'] = lerp(gc['saturation'], 100.0)
        return out

    def _apply_builtin_preset(self, key):
        label, _tip, recipe = PRESET_INDEX.get(key, ('Neutral', '', {}))
        if key == 'neutral': label = 'Neutral'
        st = self._look_state(recipe if key in PRESET_INDEX else {})
        st['bands'] = {b: list(st['bands'].get(b, (0, 0, 0))) for b in BANDS}
        k = max(0.0, min(1.0, self._s_look.value / 100.0)) if key in PRESET_INDEX else 1.0
        if k < 0.999: st = self._blend_state(st, k)
        self._mark_look(key if key in PRESET_INDEX else None)
        self._apply_grade_state(st, label)

    def _set_preset_status(self, text):
        lab = getattr(self, '_preset_status', None)
        if lab is not None:
            try: lab.set_text(text)
            except Exception: pass

    def _apply_user_preset(self, _button, path):
        try:
            with open(path, 'r', encoding='utf-8') as f: st = json.load(f)
            if not isinstance(st, dict) or st.get('format') not in PRESET_FORMATS: raise ValueError('Not a Colour Grading preset')
        except Exception as exc:
            self._set_preset_status('Could not read that look: ' + str(exc))
            return
        self._mark_look(None)
        self._apply_grade_state(st, str(st.get('name') or os.path.splitext(os.path.basename(path))[0]))

    def _grade_state(self):
        return {
            'format': 'colour-grading-preset', 'version': 2,
            'bands': {b: list(self._canonical_rgb.get(b, (0, 0, 0))) for b in BANDS},
            'master': list(self._master_canonical_rgb),
            'saturation': float(self._sat_value),
            'contrast': float(self._contrast), 'pivot': float(self._pivot),
            'ranges': {k: float(v) for k, v in self._ranges.items()},
            'tone': [dict(x) for x in self._tone_state],
            'tonal_colour': [dict(x) for x in self._tonal_colour_state],
            'hue_adjustments': [dict(x) for x in self._hue_adjust_state],
            'global_hue': float(self._global_hue),
            'primaries': {k: dict(v) for k, v in self._primaries_state.items()},
            'global_colour': dict(self._global_colour_state, saturation=100.0),
        }

    def _set_hs_from_rgb(self, band, rgb):
        h, s = rgb_to_wheel(*rgb)
        if s < 1e-6: h = self._hs[band][0]
        self._hs[band] = (h, s)
        self._discs[band].set_value(h, s)
        self._sync_readout(band)

    def _apply_grade_state_now(self, st):
        self._cancel_pending_preview_timers()
        errs = []
        pv = self._preview
        def sect(name, fn):
            try: fn()
            except Exception as exc:
                errs.append('%s: %s' % (name, exc)); _dbg('preset section %s failed: %r' % (name, exc))
        def f3(v, default=(0.0, 0.0, 0.0)):
            try:
                lst = list(v)[:3]
                return tuple(float(x) for x in lst) + (0.0,) * (3 - len(lst))
            except Exception: return default
        gcs = st.get('global_colour', {}) if isinstance(st.get('global_colour'), dict) else {}

        def bands():
            b = st.get('bands', {}) if isinstance(st.get('bands'), dict) else {}
            for band in BANDS:
                rgb = f3(b.get(band, [0, 0, 0]))
                self._canonical_rgb[band] = rgb
                self._wheel_values[band] = (0, 0, *rgb)
                self._set_hs_from_rgb(band, rgb)
            pv._band_wheel = dict(self._canonical_rgb)
            pv.update_wheels(self._wheel_values)

        def master():
            m = f3(st.get('master', [0, 0, 0]))
            self._master_canonical_rgb = m
            self._set_hs_from_rgb(MASTER, m)
            pv.update_master_rgb(*m)

        def satur():
            try: gk = float(gcs.get('saturation', 100.0)) / 100.0
            except Exception: gk = 1.0
            sat = max(0.0, min(2.0, float(st.get('saturation', 1)) * gk))
            self._sat_value = sat
            self._s_sat.set_value_quiet(sat * 100.0)
            self._s_sat2.set_value_quiet(sat * 100.0)
            pv.update_saturation(sat)

        def contrast():
            con = max(-1.0, min(1.0, float(st.get('contrast', 0))))
            piv = max(0.0, min(1.0, float(st.get('pivot', 0.5))))
            self._contrast, self._pivot = con, piv
            self._s_con.set_value_quiet(con * 100.0)
            self._s_piv.set_value_quiet(piv * 100.0)
            pv.set_contrast(con, piv)

        def ranges():
            r = st.get('ranges', {}) if isinstance(st.get('ranges'), dict) else {}
            merged = dict(_DEFAULT_RANGES)
            for k in merged:
                try: merged[k] = max(0.0, min(1.0, float(r.get(k, merged[k]))))
                except Exception: pass
            self._ranges = merged
            for band in BANDS: self._reach[band] = _ranges_to_reach(band, merged)
            pv.update_ranges(dict(merged))

        def targeted():
            self._load_targeted_state(st)
            pv._last_target_sig = None; pv._target_error_shown = False; pv._last_target_error = None
            pv.update_targeted(self._tone_state, self._tonal_colour_state, self._hue_adjust_state, global_hue=self._global_hue)

        def primaries():
            prim = st.get('primaries', {}) if isinstance(st.get('primaries'), dict) else {}
            def g(k, p):
                try: return float(prim.get(k, {}).get(p, 0))
                except Exception: return 0.0
            self._primaries_state = {k: {'hue': max(-180.0, min(180.0, g(k, 'hue'))),
                                         'purity': max(0.0, min(100.0, g(k, 'purity')))} for k in ('red', 'green', 'blue')}
            for k in self._primaries_state: self._sync_primary_readout(k)
            pv.update_primaries(self._primaries_state)

        def global_colour():
            def g(k, d):
                try: return float(gcs.get(k, d))
                except Exception: return d
            self._global_colour_state = {'temperature': max(-100.0, min(100.0, g('temperature', 0))),
                                         'tint': max(-100.0, min(100.0, g('tint', 0))),
                                         'chroma': max(-100.0, min(100.0, g('chroma', 0))), 'saturation': 100.0}
            for k, s in self._gc_sl.items(): s.set_value_quiet(self._global_colour_state[k])
            pv.update_global_colour(self._global_colour_state)

        pv.begin_batch()
        try:
            for name, fn in (('wheels', bands), ('master', master), ('saturation', satur), ('contrast', contrast),
                             ('ranges', ranges), ('tone / tonal colour / hue', targeted),
                             ('primaries', primaries), ('global colour', global_colour)):
                sect(name, fn)
        finally:
            try: pv.end_batch()
            except Exception as exc: errs.append('refresh: %s' % exc); self._preview._batch_depth = 0
        self._pending_targeted = False
        self._pending_primaries = False
        self._pending_global = False
        self._scopes_dirty = True
        self._sync_balance_panel()
        return errs

    def _sync_all_targeted_ui(self):
        self._sync_tone_ui()
        self._sync_tonal_ui()
        self._hue_sel = 0 if self._hue_adjust_state else -1
        self._sync_hue_ui()

    def _reset_section(self, keys, title):
        if not self._ask('Reset %s?' % title, 'Everything on this tab returns to neutral.', 'Reset'):
            return
        st = self._grade_state()
        neutral = self._look_state({})
        neutral['ranges'] = dict(_DEFAULT_RANGES)
        neutral['pivot'] = 0.5
        for k in keys: st[k] = neutral[k]
        self._mark_look(None)
        self._apply_grade_state(st, 'Reset')

    def _reset_balance(self, *_):
        self._reset_section(('bands', 'master', 'saturation', 'contrast', 'pivot', 'ranges'), 'Balance')

    def _reset_tone(self, *_):
        self._reset_section(('tone',), 'Tone')

    def _reset_tonal(self, *_):
        self._reset_section(('tonal_colour',), 'Tonal colour')

    def _reset_hue(self, *_):
        self._reset_section(('hue_adjustments', 'global_hue'), 'Hue adjustments')

    def _reset_colour(self, *_):
        self._reset_section(('primaries', 'global_colour', 'saturation'), 'Colour')

    def _confirm_global_reset(self, *_):
        if not self._ask('Reset the entire grade?', 'Every control on every tab returns to neutral.', 'Reset all'):
            return
        self._cancel_pending_preview_timers()
        self._apply_builtin_preset('neutral')
        self._scope_clear_cb()
        if self._lut_current is not None:
            self._lut_reset_combo()
            self._lut_apply(None)

    def _flush_pending_preview_state(self):
        self._cancel_pending_preview_timers()
        pv = self._preview
        pv.begin_batch()
        try:
            pv.update_wheels(self._wheel_values)
            pv.update_master_rgb(*self._master_canonical_rgb)
            pv.update_ranges(dict(self._ranges))
            pv.update_saturation(self._sat_value)
            pv.set_contrast(self._contrast, self._pivot)
            self._pending_targeted = True
            self._flush_managed_preview_now()
        finally:
            pv.end_batch()

    def _on_preview_toggled(self, button):
        self._preview.set_preview_enabled(button.get_active())

    def _on_cancel(self, _btn):
        self._cancel_pending_preview_timers()
        self._preview.discard()
        self.destroy()

    def _on_window_delete(self, widget, event):
        if self._closing_from_window: return True
        self._closing_from_window = True
        try:
            self._cancel_pending_preview_timers()
            self._preview.discard()
        finally:
            self.destroy()
        return True

    def _on_ok(self, _btn):
        self._flush_pending_preview_state()
        self._preview.set_preview_enabled(True)
        if hasattr(self._preview, 'has_active_grade') and not self._preview.has_active_grade():
            self._preview.discard(); self.destroy(); return
        if self._commit_current_fx() is None:
            return
        self._applied = True
        self.destroy()

    def _lut_config_path(self):
        return os.path.join(_config_dir(), 'lut-folder.txt')

    def _lut_init_folder(self):
        try:
            with open(self._lut_config_path(), 'r', encoding='utf-8') as f: folder = f.read().strip()
        except Exception:
            folder = ''
        if folder and os.path.isdir(folder): self._lut_set_folder(folder, save=False)

    def _choose_lut_folder(self, *_):
        d = Gtk.FileChooserDialog(title='Choose your LUT folder', parent=self, action=Gtk.FileChooserAction.SELECT_FOLDER)
        d.add_buttons('Cancel', Gtk.ResponseType.CANCEL, 'Choose', Gtk.ResponseType.OK)
        if d.run() == Gtk.ResponseType.OK:
            self._lut_set_folder(d.get_filename(), save=True)
        d.destroy()

    def _lut_set_folder(self, folder, save):
        self._lut_folder_label.set_text(folder)
        self._lut_folder_label.set_tooltip_text(folder)
        if save:
            try:
                with open(self._lut_config_path(), 'w', encoding='utf-8') as f: f.write(folder)
            except Exception: pass
        self._lut_combo.handler_block_by_func(self._lut_chosen)
        try:
            self._lut_combo.remove_all()
            self._lut_combo.append_text('No LUT')
            self._lut_paths = scan_luts(folder)
            for p in self._lut_paths:
                self._lut_combo.append_text(os.path.relpath(p, folder))
            self._lut_combo.set_active(0)
        finally:
            self._lut_combo.handler_unblock_by_func(self._lut_chosen)
        if self._lut_current is not None: self._lut_apply(None)
        n = len(self._lut_paths)
        self._lut_status.set_text('%d LUT file%s found.' % (n, '' if n == 1 else 's') if n else
                                  'No LUT files found here. Looking for .cube files and HaldCLUT .png files.')

    def _lut_progress(self, frac):
        try: Gimp.progress_update(float(frac))
        except Exception: pass
        while Gtk.events_pending(): Gtk.main_iteration()

    def _lut_chosen(self, combo):
        if self._lut_busy: return
        i = combo.get_active()
        if i <= 0:
            self._lut_apply(None)
            return
        path = self._lut_paths[i - 1]
        try:
            lut = load_lut(path)
        except Exception as exc:
            self._lut_status.set_text('Could not read that LUT: ' + str(exc))
            self._lut_reset_combo()
            return
        self._lut_apply(lut)

    def _lut_reset_combo(self):
        self._lut_busy = True
        try: self._lut_combo.set_active(0)
        finally: self._lut_busy = False

    def _lut_apply(self, lut):
        k = self._s_lutk.value / 100.0
        self.set_sensitive(False)
        try:
            if lut is not None:
                Gimp.progress_init('Applying LUT…')
                if _np is None:
                    self._lut_status.set_text('Applying the LUT. This can take a while on large pictures without numpy installed…')
            self._preview.set_lut(lut, k, self._lut_progress)
            self._lut_current = lut
            self._scopes_dirty = True
            if lut is None: self._lut_status.set_text('No LUT applied.')
            else: self._lut_status.set_text('Applied “%s” (%d-point LUT).' % (lut['name'], lut['size']))
        except Exception as exc:
            self._lut_current = None
            self._lut_status.set_text('Could not apply the LUT: ' + str(exc))
            self._lut_reset_combo()
        finally:
            try: Gimp.progress_end()
            except Exception: pass
            self.set_sensitive(True)

    def _lut_strength_released(self):
        if self._lut_current is None: return
        try:
            self._preview.set_lut(self._lut_current, self._s_lutk.value / 100.0)
            self._scopes_dirty = True
        except Exception as exc:
            self._lut_status.set_text('Could not change the LUT strength: ' + str(exc))

    def _show_lut_links(self, *_):
        d = Gtk.Dialog(title='Get free LUTs', transient_for=self, modal=True)
        d.add_button('Close', Gtk.ResponseType.CLOSE)
        area = d.get_content_area()
        area.set_spacing(8)
        area.set_border_width(14)
        intro = Gtk.Label(label='Download a collection, unzip it, then use “Choose folder…” to point this plugin at it. '
                                'Each collection has its own licence, so check it before sharing or selling work made with it. '
                                'For other LUTs, search the web for “free .cube LUT”.')
        intro.set_line_wrap(True)
        intro.set_width_chars(56)
        intro.set_max_width_chars(56)
        intro.set_xalign(0)
        area.pack_start(intro, False, False, 0)
        for text, url in (('RawTherapee film simulation collection (HaldCLUT .png)', 'https://rawpedia.rawtherapee.com/Film_Simulation'),
                          ('G\'MIC colour presets, over 1,100 CLUTs (.png and .cube)', 'https://gmic.eu/color_presets/'),
                          ('darktable “lut 3D” manual (how LUTs are used there)', 'https://docs.darktable.org/usermanual/5.6/en/module-reference/processing-modules/lut-3d/')):
            area.pack_start(Gtk.LinkButton.new_with_label(url, text), False, False, 0)
        d.set_resizable(False)
        d.show_all()
        d.run()
        d.destroy()

    def _commit_current_fx(self):
        preview=getattr(self._preview,'_preview_layer',None)
        if preview is None:
            return None
        state=self._grade_state()
        result=None
        try:
            preview.set_visible(True)
            for f in list(preview.get_filters() or []):
                try:
                    if not f.get_visible(): f.delete()
                except Exception: pass
            self._preview._display_flush()
            if not preview.merge_filters():
                raise RuntimeError('GIMP could not merge the grading filters into the layer.')
            if list(preview.get_filters() or []):
                raise RuntimeError('Filters are still attached after merging.')
            result=preview
            try: result.attach_parasite(Gimp.Parasite.new(RESULT_PARASITE,1,b'1'))
            except Exception: pass
        except Exception as exc:
            Gimp.message('Apply stopped: Colour Grading could not bake the result. Nothing was changed.\n'+str(exc))
            return None
        pm=self._preview
        pm._preview_layer=None
        pm._filters.clear()
        pm._sat_filter=None
        pm._contrast_filter=None
        pm._target_base_buffer=None; pm._draft_ctx=None
        for name in ('_primaries_filter','_temperature_filter','_tint_filter',
                     '_chroma_filter','_global_sat_filter'):
            if hasattr(pm,name):
                setattr(pm,name,None)
        pm._initialised=False
        pm._close_undo()
        pm._display_flush()
        _write_last_edit(self._source_drawable,state)
        _write_last_edit(result,state)
        try: self._image.set_selected_layers([result])
        except Exception: pass
        return result

    def _restore_last_edit(self,*_):
        data=_read_last_edit(self._source_drawable)
        if not data or not data.get('grade'):
            Gimp.message('No Colour Grading Last Edit is stored on this source layer.'); return
        if not self._ask('Restore Last Edit?','Replace the current Colour Grading controls with the last successfully applied grade stored on this source layer?','Restore'):
            return
        self._cancel_pending_preview_timers(); self._apply_grade_state(data['grade'])


    @staticmethod
    def _picked_linear(colour):
        try:
            v=colour.get_rgba()
            return max(0.0,min(1.0,float(v[0]))),max(0.0,min(1.0,float(v[1]))),max(0.0,min(1.0,float(v[2])))
        except Exception:
            return None

    @staticmethod
    def _neutral_tone_state():
        return [{'pos':.08,'name':'Blacks','brightness':0.0,'range':.12,'spread':.5},
                {'pos':.27,'name':'Shadows','brightness':0.0,'range':.16,'spread':.5},
                {'pos':.50,'name':'Midtones','brightness':0.0,'range':.18,'spread':.5},
                {'pos':.74,'name':'Highlights','brightness':0.0,'range':.16,'spread':.5},
                {'pos':.94,'name':'Whites','brightness':0.0,'range':.10,'spread':.5}]

    @staticmethod
    def _neutral_tonal_colour_state():
        return [{'name':'Shadows','pos':.22,'hue':220.,'strength':0.,'range':.22,'spread':.55,'luminance':0.},
                {'name':'Midtones','pos':.50,'hue':35.,'strength':0.,'range':.25,'spread':.55,'luminance':0.},
                {'name':'Highlights','pos':.80,'hue':48.,'strength':0.,'range':.20,'spread':.55,'luminance':0.}]

    @staticmethod
    def _hue_defaults():
        return {'hue':0.0,'saturation':0.0,'luminance':0.0,'range':20.0,'spread':.5}

    def _schedule_targeted(self):
        self._pending_targeted=True; self._scopes_dirty=True; self._schedule_managed_preview()

    def _flush_targeted_release(self,*_):
        sid=getattr(self,'_managed_preview_timer',None)
        if sid is not None:
            try: GLib.source_remove(sid)
            except Exception: pass
            self._managed_preview_timer=None
        sid=getattr(self,'_final_timer',None)
        if sid is not None:
            try: GLib.source_remove(sid)
            except Exception: pass
            self._final_timer=None
        self._pending_targeted=True; self._scopes_dirty=True
        self._flush_managed_preview_now(); return False

    def _make_pick_button(self,tip,callback,text):
        pick=GimpUi.PickButton.new(); pick.set_tooltip_text(tip); pick.connect('color-picked',callback)
        pick.set_valign(Gtk.Align.CENTER)
        try:
            old=pick.get_child(); img=None
            theme=Gtk.IconTheme.get_default()
            for name in ('gimp-tool-color-picker','color-select-symbolic','gtk-color-picker'):
                if theme is not None and theme.has_icon(name):
                    img=Gtk.Image.new_from_icon_name(name,Gtk.IconSize.BUTTON); img.set_pixel_size(20); break
            if img is None: img=old
            if old is not None: pick.remove(old)
            hb=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=6)
            if img is not None: hb.pack_start(img,False,False,0)
            hb.pack_start(Gtk.Label(label=text),False,False,0)
            pick.add(hb)
        except Exception as exc:
            _dbg('pick button restyle failed: %r'%(exc,))
        return pick

    def _load_targeted_state(self,st):
        def merged(defaults,incoming):
            out=[]
            for i,d in enumerate(defaults):
                n=dict(d)
                src=incoming[i] if isinstance(incoming,(list,tuple)) and i<len(incoming) and isinstance(incoming[i],dict) else {}
                for k,v in src.items():
                    if k=='name': continue
                    try: n[k]=float(v)
                    except Exception: pass
                out.append(n)
            return out
        self._tone_state[:]=merged(self._neutral_tone_state(),st.get('tone'))
        self._tonal_colour_state[:]=merged(self._neutral_tonal_colour_state(),st.get('tonal_colour'))
        hue=[]
        for s in (st.get('hue_adjustments') or []):
            if not isinstance(s,dict): continue
            n={'source_hue':0.0}; n.update(self._hue_defaults())
            for k in list(n):
                try: n[k]=float(s.get(k,n[k]))
                except Exception: pass
            hue.append(n)
        self._hue_adjust_state[:]=hue
        try: self._global_hue=max(-180.0,min(180.0,float(st.get('global_hue',0.0))))
        except Exception: self._global_hue=0.0
        self._clamp_targeted_state()
        self._sync_all_targeted_ui()

    @staticmethod
    def _precision_facts(precision):
        name=str(precision).upper()
        if 'U8_' in name: bits,kind=8,'integer'
        elif 'U16_' in name: bits,kind=16,'integer'
        elif 'U32_' in name: bits,kind=32,'integer'
        elif 'HALF_' in name: bits,kind=16,'floating point'
        elif 'DOUBLE_' in name: bits,kind=64,'floating point'
        elif 'FLOAT_' in name: bits,kind=32,'floating point'
        else: bits,kind=None,'unknown'
        if 'LINEAR' in name and 'NON_LINEAR' not in name: trc='Linear'
        elif 'PERCEPTUAL' in name: trc='Perceptual'
        elif 'NON_LINEAR' in name: trc='Non-linear'
        else: trc='Unknown'
        return bits,kind,trc

    def _colour_diagnostics(self):
        info=getattr(self._preview,'_colour_info',None) or self._preview.colour_preflight()
        warnings=[]; errors=[]
        try: precision=self._image.get_precision()
        except Exception: precision=None
        bits,kind,trc=self._precision_facts(precision)
        try: base=str(self._image.get_base_type()).replace('GIMP_IMAGE_BASE_TYPE_','').replace('GimpImageBaseType.','')
        except Exception: base='Unknown'
        profile=info.get('profile','unknown'); space=info.get('space','GEGL source space')
        if info.get('fallback'): errors.append(info['fallback'])
        if info.get('rgb') is False: errors.append('Effective profile is not RGB; Colour Grading RGB creative stages require RGB.')
        if bits == 8:
            warnings.append('8-bit source precision: strong or overlapping grades can reveal quantisation/banding. Convert to 16-bit or float before substantial grading. Increasing precision improves subsequent processing but cannot restore information already lost in the 8-bit source.')
        elif bits is None:
            warnings.append('Document precision could not be classified.')

        try:
            st=self._grade_state(); pressure=0
            pressure += sum(1 for b in st['bands'].values() if max(map(abs,b))>.12)
            pressure += 1 if max(map(abs,st['master']))>.12 else 0
            pressure += sum(1 for x in st['tone'] if abs(float(x.get('brightness',0)))>35)
            pressure += sum(1 for x in st['tonal_colour'] if float(x.get('strength',0))>55)
            pressure += sum(1 for x in st['hue_adjustments'] if max(abs(float(x.get('saturation',0))),abs(float(x.get('luminance',0))))>50)
            pressure += sum(1 for x in st['primaries'].values() if float(x.get('purity',0))>65)
            gc=st['global_colour']; pressure += 1 if abs(float(gc.get('chroma',0)))>55 or float(gc.get('saturation',100))>155 else 0
            if pressure>=4: warnings.append('Current grade has several strong overlapping colour/tonal adjustments. Watch scopes and destination gamut for clipping or compression.')
        except Exception: pass

        status='ERROR' if errors else ('WARN' if warnings else 'SOUND')
        rows=[
            ('Image model',base),('ICC / effective profile',profile),('Working space',space),
            ('Document precision',str(precision) if precision is not None else 'unknown'),
            ('Precision class',(str(bits)+'-bit '+kind) if bits else kind),('Document TRC / encoding',trc),
            ('Colour Grading internal processing','GEGL/babl floating processing; no intentional creative 8-bit raster path'),
            ('Tone targeting','CIE Lab L*'),('Tone adjustment','LCh Lightness'),
            ('Hue adjustments','Source-hue mask; Hue/Chroma/Lightness controls'),
            ('Contrast / Pivot','Perceptual (gamma-encoded) RGB with a movable pivot'),
            ('RGB wheels / primaries / tint','Source RGB space; no forced sRGB conversion'),
            ('Saturation','GEGL Native / source-space aware'),('Temperature','GEGL color-temperature'),
            ('Preview vs Apply',"Same temporary preview result is baked on Apply; targeted stages render through the preview layer's native-format GEGL buffer, safe stages remain native DrawableFilters"),
            ('Scopes','Monitoring only; reduced precision does not enter creative processing'),
        ]
        return status,rows,warnings,errors

    def _refresh_colour_diagnostics(self):
        try:
            status,rows,warnings,errors=self._colour_diagnostics()
            if hasattr(self,'_colour_diag_button'):
                b=self._colour_diag_button; ctx=b.get_style_context()
                for cls in ('cg-diag-ok','cg-diag-warn','cg-diag-error'): ctx.remove_class(cls)
                if status=='SOUND':
                    b.set_label('✓ Colour'); ctx.add_class('cg-diag-ok')
                    b.set_tooltip_text('Colour diagnostics: no current warnings. Click for the full colour and precision audit.')
                elif status=='WARN':
                    n=len(warnings); b.set_label('⚠ Colour'+((' · '+str(n)) if n else '')); ctx.add_class('cg-diag-warn')
                    b.set_tooltip_text('Colour diagnostics: '+str(n)+' warning'+('' if n==1 else 's')+'. Click for details and recommendations.')
                else:
                    n=len(errors); b.set_label('✕ Colour'); ctx.add_class('cg-diag-error')
                    b.set_tooltip_text('Colour diagnostics: '+str(n)+' error'+('' if n==1 else 's')+'. Click for details and recommendations.')
        except Exception as exc:
            if hasattr(self,'_colour_diag_button'):
                b=self._colour_diag_button; ctx=b.get_style_context()
                for cls in ('cg-diag-ok','cg-diag-warn','cg-diag-error'): ctx.remove_class(cls)
                b.set_label('✕ Colour'); ctx.add_class('cg-diag-error'); b.set_tooltip_text('Colour diagnostics failed: '+str(exc))
        return False

    def _show_colour_diagnostics(self,*_):
        status,rows,warnings,errors=self._colour_diagnostics()
        d=Gtk.Dialog(title='Colour Diagnostics — '+status,transient_for=self,modal=True)
        d.add_button('Close',Gtk.ResponseType.CLOSE); d.set_default_size(650,620)
        area=d.get_content_area(); area.set_spacing(8); area.set_border_width(12)
        title=Gtk.Label(); title.set_markup('<span size="large"><b>Colour Diagnostics — '+('✓ COLOUR' if status=='SOUND' else ('⚠ WARN' if status=='WARN' else '✕ ERROR'))+'</b></span>'); title.set_xalign(0); area.pack_start(title,False,False,0)
        sc=Gtk.ScrolledWindow(); sc.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC); area.pack_start(sc,True,True,0)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5); sc.add(box)
        for k,v in rows:
            row=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=12); a=Gtk.Label(label=k); a.set_xalign(0); a.set_size_request(190,-1); b=Gtk.Label(label=str(v)); b.set_xalign(0); b.set_line_wrap(True); b.set_selectable(True); row.pack_start(a,False,False,0); row.pack_start(b,True,True,0); box.pack_start(row,False,False,0)
        if errors:
            h=Gtk.Label(); h.set_markup('<b>Errors</b>'); h.set_xalign(0); box.pack_start(h,False,False,8)
            for x in errors:
                q=Gtk.Label(label='✕ '+x); q.set_xalign(0); q.set_line_wrap(True); box.pack_start(q,False,False,0)
        if warnings:
            h=Gtk.Label(); h.set_markup('<b>Warnings</b>'); h.set_xalign(0); box.pack_start(h,False,False,8)
            for x in warnings:
                q=Gtk.Label(label='⚠ '+x); q.set_xalign(0); q.set_line_wrap(True); box.pack_start(q,False,False,0)
        if not errors and not warnings:
            q=Gtk.Label(label='✓ No document or Colour Grading colour/precision warning detected.'); q.set_xalign(0); box.pack_start(q,False,False,8)

        facts=dict(rows)
        bits,_,_=self._precision_facts(facts.get('Document precision',''))
        profile=facts.get('ICC / effective profile','unknown')
        strong_grade=any(str(x).startswith('Current grade has several strong overlapping') for x in warnings)
        recs=[]
        if bits == 8:
            recs.append('For substantial grading, consider 16-bit integer or 32-bit floating-point precision before continuing. This protects subsequent calculations; it cannot recreate precision already lost in the source.')
        pl=str(profile).lower()
        if not errors and ('srgb' in pl or 's-rgb' in pl):
            try:
                if strong_grade: recs.append('This is a strong grade in an sRGB-class gamut. If the source and output workflow justify it, consider converting to a wider-gamut RGB working space before further extreme colour moves. Convert the profile; do not merely assign a different profile.')
            except Exception: pass
        if errors:
            recs.append('Resolve the profile/model error before relying on colour-critical grading. Diagnostics never changes the document automatically.')
        if not recs:
            recs.append('No colour-space or precision change is currently recommended. Keep the present setup unless the intended output workflow requires a different profile.')
        h=Gtk.Label(); h.set_markup('<b>Recommendations</b>'); h.set_xalign(0); box.pack_start(h,False,False,8)
        for x in recs:
            q=Gtk.Label(label='• '+x); q.set_xalign(0); q.set_line_wrap(True); box.pack_start(q,False,False,0)
        note=Gtk.Label(label='Diagnostics describe the current document and Colour Grading processing pipeline. Recommendations are advisory only: Colour Grading never changes profile, precision or encoding automatically. Gamut/clipping warnings are conservative pipeline-risk indicators, not a substitute for an output-profile soft proof.'); note.set_xalign(0); note.set_line_wrap(True); note.get_style_context().add_class('dim-label'); box.pack_start(note,False,False,10)
        d.show_all(); d.run(); d.destroy()

    def _user_preset_dir(self):
        path=os.path.join(_config_dir(),'presets')
        os.makedirs(path,exist_ok=True)
        return path

    def _reload_user_presets(self):
        box=getattr(self,'_user_preset_box',None)
        if box is None: return
        for child in list(box.get_children()): box.remove(child)
        try:
            files=sorted((f for f in os.listdir(self._user_preset_dir()) if f.lower().endswith('.json')),key=str.casefold)
        except Exception:
            files=[]
        if not files:
            empty=Gtk.Label(label='No saved looks yet.'); empty.set_xalign(0); empty.get_style_context().add_class('dim-label'); box.add(empty)
        else:
            for filename in files:
                path=os.path.join(self._user_preset_dir(),filename)
                try:
                    with open(path,'r',encoding='utf-8') as f: data=json.load(f)
                    label=str(data.get('name') or os.path.splitext(filename)[0])
                except Exception:
                    continue
                b=Gtk.Button(label=label); b.set_size_request(120,34); b.set_hexpand(True); b.get_style_context().add_class('cg-preset')
                b.set_tooltip_text('Apply saved user preset: '+label)
                b.connect('clicked',self._apply_user_preset,path)
                box.add(b)
        box.show_all()

    def _lut_save_note(self):
        lut=getattr(self,'_lut_current',None)
        if lut is None: return None
        n=Gtk.Label(label='Note: the LUT “%s” is not stored inside a saved look. Your adjustments are saved; choose the LUT again when you reuse this look.' % lut['name'])
        n.set_line_wrap(True); n.set_xalign(0); n.set_max_width_chars(48)
        n.get_style_context().add_class('cg-warn')
        return n

    def _save_user_preset(self,*_):
        d=Gtk.Dialog(title='Save User Preset',transient_for=self,modal=True)
        d.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Save Preset',Gtk.ResponseType.OK)
        area=d.get_content_area(); area.set_spacing(8); area.set_border_width(12)
        lab=Gtk.Label(label='Preset name'); lab.set_xalign(0); area.pack_start(lab,False,False,0)
        entry=Gtk.Entry(); entry.set_activates_default(True); entry.set_placeholder_text('My grade'); area.pack_start(entry,False,False,0)
        note=self._lut_save_note()
        if note is not None: area.pack_start(note,False,False,0)
        d.set_default_response(Gtk.ResponseType.OK); d.show_all()
        if d.run()==Gtk.ResponseType.OK:
            name=entry.get_text().strip()
            if name:
                safe=''.join(c if c.isalnum() or c in (' ','-','_') else '_' for c in name).strip().replace(' ','-') or 'preset'
                path=os.path.join(self._user_preset_dir(),safe+'.json')
                try:
                    st=self._grade_state(); st['name']=name
                    with open(path,'w',encoding='utf-8') as f: json.dump(st,f,indent=2)
                    self._reload_user_presets()
                except Exception as exc: Gimp.message('Could not save user preset: '+str(exc))
        d.destroy()

    def _delete_user_presets(self,*_):
        try:
            entries=[]
            for filename in sorted((f for f in os.listdir(self._user_preset_dir()) if f.lower().endswith('.json')), key=str.casefold):
                path=os.path.join(self._user_preset_dir(),filename)
                try:
                    with open(path,'r',encoding='utf-8') as f: data=json.load(f)
                    label=str(data.get('name') or os.path.splitext(filename)[0])
                    entries.append((label,path))
                except Exception:
                    continue
        except Exception:
            entries=[]
        if not entries:
            Gimp.message('There are no user presets to delete.')
            return

        d=Gtk.Dialog(title='Delete User Presets',transient_for=self,modal=True)
        d.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Continue…',Gtk.ResponseType.OK)
        d.set_default_size(460,420)
        area=d.get_content_area(); area.set_spacing(8); area.set_border_width(12)
        lab=Gtk.Label(label='Select the user presets to delete:'); lab.set_xalign(0); area.pack_start(lab,False,False,0)
        sw=Gtk.ScrolledWindow(); sw.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC); sw.set_min_content_height(260)
        choices=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4); sw.add(choices); area.pack_start(sw,True,True,0)
        checks=[]
        for label,path in entries:
            cb=Gtk.CheckButton(label=label); cb.set_tooltip_text(path); choices.pack_start(cb,False,False,0); checks.append((cb,label,path))
        d.show_all()
        response=d.run()
        selected=[(label,path) for cb,label,path in checks if cb.get_active()]
        d.destroy()
        if response!=Gtk.ResponseType.OK or not selected:
            return

        names=', '.join(label for label,_path in selected)
        confirm=Gtk.MessageDialog(transient_for=self,modal=True,message_type=Gtk.MessageType.WARNING,
                                  buttons=Gtk.ButtonsType.NONE,text='Delete selected user preset%s?' % ('' if len(selected)==1 else 's'))
        confirm.format_secondary_text(names+'\n\nThis cannot be undone.')
        confirm.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Delete',Gtk.ResponseType.OK)
        answer=confirm.run(); confirm.destroy()
        if answer!=Gtk.ResponseType.OK:
            return

        failed=[]
        for label,path in selected:
            try: os.remove(path)
            except Exception as exc: failed.append(label+': '+str(exc))
        self._reload_user_presets()
        if failed: Gimp.message('Could not delete some user presets:\n'+'\n'.join(failed))


    def _apply_grade_state(self,st,label=None):
        self._queued_state=st; self._queued_label=label or st.get('name') or 'preset'
        self._set_preset_status('Applying “%s”…'%self._queued_label)
        if getattr(self,'_state_idle',None) is None:
            self._state_idle=GLib.idle_add(self._run_queued_state)

    def _run_queued_state(self):
        self._state_idle=None
        st=getattr(self,'_queued_state',None); label=getattr(self,'_queued_label','preset'); self._queued_state=None
        if st is None: return False
        self._loading_state=True
        try:
            errs=self._apply_grade_state_now(st)
            bad=getattr(self._preview,'_last_target_error',None)
            if bad: errs=list(errs)+['preview: '+str(bad)]
            if errs:
                self._set_preset_status('Applied “%s” with problems — %s'%(label,'; '.join(errs)[:300]))
                _dbg('preset “%s” problems: %s'%(label,errs))
            else:
                self._set_preset_status('Applied “%s”.'%label)
        except Exception as exc:
            _dbg('preset apply failed: %r'%(exc,))
            self._set_preset_status('Could not apply “%s”: %s'%(label,exc))
        finally:
            self._loading_state=False
        return False

    def _look_state(self,r):
        zones=(('Blacks',.08,.12),('Shadows',.27,.16),('Midtones',.50,.18),('Highlights',.74,.16),('Whites',.94,.10))
        tb=list(r.get('tone',(0,0,0,0,0)))+[0]*5
        tone=[{'pos':p,'name':n,'brightness':float(tb[i]),'range':rg,'spread':.5} for i,(n,p,rg) in enumerate(zones)]
        tc=self._neutral_tonal_colour_state()
        for i,t in enumerate(r.get('tc',[])[:3]):
            tc[i]['hue']=float(t[0]); tc[i]['strength']=float(t[1]); tc[i]['luminance']=float(t[2]) if len(t)>2 else 0.0
        hue=[]
        for h,dh,sa,lu,rg in r.get('hue',[]):
            e={'source_hue':float(h)}; e.update(self._hue_defaults()); e.update({'hue':float(dh),'saturation':float(sa),'luminance':float(lu),'range':float(rg)}); hue.append(e)
        prim={k:{'hue':0.0,'purity':0.0} for k in ('red','green','blue')}
        for k,(h,p) in r.get('prim',{}).items(): prim[k]={'hue':float(h),'purity':float(p)}
        gc=r.get('gc',(0,0,0,100))
        return {'format':'colour-grading-preset','version':2,
                'bands':{'shadows':list(r.get('sh',(0,0,0))),'midtones':list(r.get('mid',(0,0,0))),'highlights':list(r.get('hi',(0,0,0)))},
                'master':list(r.get('master',(0,0,0))),'saturation':float(r.get('sat',1.0)),'contrast':float(r.get('con',0.0)),'pivot':0.5,
                'tone':tone,'tonal_colour':tc,'hue_adjustments':hue,'global_hue':0.0,'primaries':prim,
                'global_colour':{'temperature':float(gc[0]),'tint':float(gc[1]),'chroma':float(gc[2]),'saturation':float(gc[3])}}

    def _export_preset(self,*_):
        lut=getattr(self,'_lut_current',None)
        if lut is not None:
            m=Gtk.MessageDialog(transient_for=self,modal=True,message_type=Gtk.MessageType.INFO,buttons=Gtk.ButtonsType.OK,text='The LUT is not included')
            m.format_secondary_text('The LUT “%s” is not stored inside an exported look. Your adjustments are exported; choose the LUT again when you reuse it.' % lut['name'])
            m.run(); m.destroy()
        d=Gtk.FileChooserDialog(title='Export Colour Grading preset',parent=self,action=Gtk.FileChooserAction.SAVE); d.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Export',Gtk.ResponseType.OK); d.set_current_name('grading-preset.json'); d.set_do_overwrite_confirmation(True)
        if d.run()==Gtk.ResponseType.OK:
            try:
                with open(d.get_filename(),'w',encoding='utf-8') as f: json.dump(self._grade_state(),f,indent=2)
            except Exception as exc: Gimp.message('Preset export failed: '+str(exc))
        d.destroy()

    def _import_preset(self,*_):
        d=Gtk.FileChooserDialog(title='Import Colour Grading preset',parent=self,action=Gtk.FileChooserAction.OPEN); d.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Import',Gtk.ResponseType.OK); flt=Gtk.FileFilter(); flt.set_name('Colour Grading presets (*.json)'); flt.add_pattern('*.json'); d.add_filter(flt)
        if d.run()==Gtk.ResponseType.OK:
            try:
                with open(d.get_filename(),'r',encoding='utf-8') as f: st=json.load(f)
                if st.get('format') not in PRESET_FORMATS: raise ValueError('Not a Colour Grading preset')
                self._apply_grade_state(st,str(st.get('name') or os.path.basename(d.get_filename())))
            except Exception as exc: Gimp.message('Preset import failed: '+str(exc))
        d.destroy()

    def _schedule_managed_preview(self):
        if self._managed_preview_timer is not None: return
        last=float(getattr(self,'_last_render_ms',0.0))
        delay=int(max(40.0,min(300.0,last*0.8+20.0)))
        self._managed_preview_timer=GLib.timeout_add(delay,self._flush_managed_preview)

    def _final_full_render(self):
        self._final_timer=None
        self._pending_targeted=True
        self._flush_managed_preview_now()
        return False

    def _clamp_targeted_state(self):
        def cl(d,k,lo,hi,default):
            try: v=float(d.get(k,default))
            except Exception: v=default
            if v!=v: v=default
            d[k]=max(lo,min(hi,v))
        for p in self._tone_state:
            cl(p,'pos',.02,.98,.5); cl(p,'brightness',-100,100,0); cl(p,'range',.02,.5,.18); cl(p,'spread',0,1,.5)
        for p in self._tonal_colour_state:
            p['hue']=float(p.get('hue',0) or 0)%360.0
            cl(p,'strength',0,100,0); cl(p,'luminance',-100,100,0); cl(p,'range',.02,.5,.2); cl(p,'spread',0,1,.55)
        self._global_hue=max(-180.0,min(180.0,float(self._global_hue)))
        for p in self._hue_adjust_state:
            p['source_hue']=float(p.get('source_hue',0) or 0)%360.0; cl(p,'hue',-180,180,0); cl(p,'saturation',-100,100,0)
            cl(p,'luminance',-100,100,0); cl(p,'range',2,90,20); cl(p,'spread',0,1,.5)

    def _flush_managed_preview(self):
        self._managed_preview_timer=None
        self._flush_managed_preview_now(True)
        if getattr(self,'_draft_used',False):
            self._draft_used=False
            sid=getattr(self,'_final_timer',None)
            if sid is not None:
                try: GLib.source_remove(sid)
                except Exception: pass
            self._final_timer=GLib.timeout_add(450,self._final_full_render)
        return False

    def _flush_managed_preview_now(self,draft=False):
        if not any((self._pending_targeted,self._pending_primaries,self._pending_global)):
            return
        self._last_flush_t=time.monotonic()
        try: self._clamp_targeted_state()
        except Exception as exc: _dbg('clamp failed: %r'%(exc,))
        self._preview.begin_batch()
        try:
            targ=self._pending_targeted; self._pending_targeted=False
            prim=self._pending_primaries; self._pending_primaries=False
            glob=self._pending_global; self._pending_global=False
            if targ:
                t0=time.monotonic()
                self._preview.update_targeted(self._tone_state,self._tonal_colour_state,self._hue_adjust_state,draft,self._global_hue)
                self._last_render_ms=(time.monotonic()-t0)*1000.0
                if draft and getattr(self._preview,'_draft_ctx',None) is not None: self._draft_used=True
            if prim: self._preview.update_primaries(self._primaries_state)
            if glob: self._preview.update_global_colour(self._global_colour_state)
        finally:
            self._preview.end_batch()
        if hasattr(self,'_colour_diag_button'): self._refresh_colour_diagnostics()

    def _primary_changed(self,key,prop,value):
        if not hasattr(self,'_primaries_state'):
            self._primaries_state={k:{'hue':0.0,'purity':0.0} for k in ('red','green','blue')}
        self._primaries_state[key][prop]=float(value)
        self._scopes_dirty=True; self._pending_primaries=True; self._schedule_managed_preview()
        if hasattr(self,'_colour_diag_button'): self._refresh_colour_diagnostics()

    def _global_colour_changed(self,key,value):
        if not hasattr(self,'_global_colour_state'):
            self._global_colour_state={'temperature':0.0,'tint':0.0,'chroma':0.0,'saturation':100.0}
        self._global_colour_state[key]=float(value)
        self._scopes_dirty=True; self._pending_global=True; self._schedule_managed_preview()
        if hasattr(self,'_colour_diag_button'): self._refresh_colour_diagnostics()

    def _ask(self,title,text,ok):
        d=Gtk.MessageDialog(transient_for=self,modal=True,message_type=Gtk.MessageType.WARNING,buttons=Gtk.ButtonsType.NONE,text=title); d.format_secondary_text(text); d.add_button('Cancel',Gtk.ResponseType.CANCEL); d.add_button(ok,Gtk.ResponseType.OK); r=d.run(); d.destroy(); return r==Gtk.ResponseType.OK

    def _push_band_colour(self, band, r, g, b):
        self._canonical_rgb[band] = (float(r), float(g), float(b))
        self._preview._band_wheel[band] = self._canonical_rgb[band]
        self._wheel_values[band] = (0.0, 0.0, r, g, b)

        if self._wheel_timer[band] is None:
            self._wheel_timer[band] = GLib.timeout_add(PREVIEW_FAST_MS, self._flush_colour, band)

    def _flush_colour(self, band):
        self._wheel_timer[band] = None
        if not self._preview._initialised:
            return False

        try:
            _, _, r, g, b = self._wheel_values[band]
            self._preview._band_wheel[band] = (r, g, b)
            self._preview._apply_band(band)
            self._preview._display_flush()
        except Exception as exc:
            self._preview._error_dialog(exc, 'Failed to update colour control')
        return False

    def _flush_master_rgb(self):
        self._master_timer = None
        r, g, b = self._master_canonical_rgb
        self._preview.update_master_rgb(r, g, b)
        return False

    def _cancel_pending_preview_timers(self):
        for band in BANDS:
            sid = self._wheel_timer.get(band)
            if sid is not None:
                try: GLib.source_remove(sid)
                except Exception: pass
                self._wheel_timer[band] = None
            sid = self._range_timers.get(band)
            if sid is not None:
                try: GLib.source_remove(sid)
                except Exception: pass
                self._range_timers[band] = None
        for attr in ('_master_timer', '_sat_timer', '_contrast_timer'):
            sid = getattr(self, attr, None)
            if sid is not None:
                try: GLib.source_remove(sid)
                except Exception: pass
                setattr(self, attr, None)
        sid=getattr(self,'_managed_preview_timer',None)
        if sid is not None:
            try: GLib.source_remove(sid)
            except Exception: pass
            self._managed_preview_timer=None
        sid=getattr(self,'_hue_preview_timer',None)
        if sid is not None:
            try: GLib.source_remove(sid)
            except Exception: pass
            self._hue_preview_timer=None
        sid=getattr(self,'_final_timer',None)
        if sid is not None:
            try: GLib.source_remove(sid)
            except Exception: pass
            self._final_timer=None


def _run(procedure, run_mode, image, drawables, config, data):
    if run_mode != Gimp.RunMode.INTERACTIVE:
        return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR, GLib.Error('Colour Grading needs to be run interactively.'))
    GimpUi.init(PLUGIN_NAME)
    if not drawables:
        return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR, GLib.Error('No drawable selected'))
    layer = drawables[0]
    try:
        sel = image.get_selected_layers()
        if sel: layer = sel[0]
    except Exception: pass
    dialog = ColourGradingDialog(image, layer)
    if dialog._ready:
        dialog.connect('destroy', lambda *_: Gtk.main_quit())
        dialog.show_all()
        Gtk.main()
    status = Gimp.PDBStatusType.SUCCESS if dialog._applied else Gimp.PDBStatusType.CANCEL
    return procedure.new_return_values(status, GLib.Error())

class ColourGradingPlugin(Gimp.PlugIn):

    def do_set_i18n(self, procedure_name):
        return False

    def do_query_procedures(self):
        return [PLUGIN_NAME]

    def do_create_procedure(self, name):
        proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, _run, None)
        proc.set_image_types('RGB*')
        proc.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)
        proc.set_menu_label(MENU_LABEL)
        proc.add_menu_path('<Image>/Colors')
        proc.set_documentation('Colour grading: balance, tone, tonal colour, hue, colour, scopes and looks',
                               'Non-destructive colour grading built on GIMP native drawable filters', name)
        proc.set_attribution('Zarir Madon', 'Zarir Madon', '2026')
        return proc

Gimp.main(ColourGradingPlugin.__gtype__, sys.argv)
