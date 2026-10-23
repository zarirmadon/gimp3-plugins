# Colour Grading for GIMP 3.2

### Give your photographs a mood. Give your colours a story.

**Colour Grading** brings a complete, hands-on grading workspace into GIMP: expressive colour wheels, precise tonal and hue targeting, channel controls, creative looks, LUTs and professional-style scopes — all in one beautifully focused dialog.

Whether you're warming the last light of a landscape, shaping a cinematic portrait, rescuing a colour cast or inventing a look entirely your own, the aim is simple: **less dialog-hopping, more creating.**

<p align="center">
  <img width="900" alt="image" src="https://github.com/user-attachments/assets/0053481f-6c2f-4fb6-8a20-ad869e5069c3" />
</p>


> **Made for photographers, artists and anyone who likes to push colour around until an image feels right.** The controls use approachable names; hover over them for the technical explanations.

## What makes it special?

- **Grade on the GIMP canvas.** See your adjustments in context, with a Preview toggle for before/after comparisons.
- **Keep your original.** The plugin grades a copy of the selected layer. Press **OK** to bake the result into a new numbered layer; **Cancel** discards the preview.
- **Go from broad to precise.** Start with overall balance, then target a brightness zone or a particular family of hues.
- **Find a look, then make it yours.** Start from **18 editable built-in looks**, create and share your own, or bring in external LUTs.
- **See what your colours are doing.** Histogram, RGB waveform, parade and vectorscope help you judge the result.
- **Pick up where you left off.** **Restore last edit** recalls settings saved with the graded layers.

## Seven tabs, one creative flow

| Tab | What you can do |
| --- | --- |
| **Balance** | Shape shadows, midtones and highlights with three-way colour wheels; add an overall cast, saturation and **Punch** (contrast). |
| **Tone** | Brighten or darken five adjustable zones — Blacks, Shadows, Midtones, Highlights and Whites — with **Width** and **Softness** controls. |
| **Tonal** | Split-tone shadows, midtones and highlights with independent colour, strength, lightness and feathering. |
| **Hue** | Select colour families on a hue strip or wheel, then adjust their hue, saturation and brightness without globally shifting everything else. |
| **Colour** | Refine red, green and blue primaries; adjust **Warmth**, green–magenta tint, **Vibrance** and saturation. Includes a colour health report. |
| **Scopes** | Inspect brightness histogram, RGB waveform, RGB parade and vectorscope; refresh when you need them. |
| **Looks** | Explore 18 built-in looks, save your own, and apply `.cube` or HaldCLUT `.png` LUTs with adjustable strength. |

### A few favourite creative moves

**Golden-hour glow:** add warmth, give highlights a subtle amber tint, and soften the contrast.

**Cinematic atmosphere:** cool the shadows, warm the highlights, then refine the hue of foliage or sky without dragging the entire palette along.

**Portrait polish:** use gentle midtone colour, a little vibrance and targeted tonal changes rather than indiscriminate saturation.

**Film-inspired finish:** start with a look such as *Bleach Bypass*, *Vivid Slide*, *Instant Print* or *Silver Noir*, then change any of its controls to suit your photograph.

## Install

**Requirements:** GIMP **3.2 or newer** with its Python 3 plug-in support. Designed for Linux, Windows and macOS. **NumPy is optional**: it accelerates LUT processing when available; without it, LUT processing uses a slower Python path.

1. Download **`colour-grading.py`**.
2. Create a folder named **`colour-grading`** in your GIMP plug-ins directory and put the Python file inside it:

   | System | Plug-in directory |
   | --- | --- |
   | Linux | `~/.config/GIMP/3.2/plug-ins/colour-grading/` |
   | Windows | `%APPDATA%\GIMP\3.2\plug-ins\colour-grading\` |
   | macOS | `~/Library/Application Support/GIMP/3.2/plug-ins/colour-grading/` |

3. On **Linux/macOS**, make it executable (adjust the path if necessary):

   ```bash
   chmod +x ~/.config/GIMP/3.2/plug-ins/colour-grading/colour-grading.py
   ```

4. Restart GIMP. Open an RGB image, select a normal image layer and choose **Colors → Colour Grading**.

You can configure additional plug-in directories through **Edit → Preferences → Folders → Plug-ins**.

## Your first grade in 60 seconds

1. Open a photograph and select the layer you want to grade.
2. Choose **Colors → Colour Grading**.
3. In **Balance**, gently move a Shadows or Highlights wheel to set the mood.
4. In **Colour**, adjust **Warmth** or **Vibrance**.
5. Visit **Looks** if you'd like a ready-made starting point — each built-in look is editable.
6. Toggle **Preview** to compare the grade with your original.
7. Press **OK** to create your finished **Colour Grading #1** layer, or **Cancel** to discard the preview.

**Useful shortcuts:** double-click a control to return it to default; **Reset tab** clears one tab; **Reset all** clears the whole grade. Hover for explanations. Use **Restore last edit** to reload settings from a previously graded source or result layer.

## Looks, presets and LUTs

The **18 built-in looks** cover cinematic, film, portrait, landscape, warm, cool, monochrome and creative styles:

| Family | Looks |
| --- | --- |
| Cinematic | Teal & Orange · Bleach Bypass · Night Neon |
| Film | Vivid Slide · Cross Process · Instant Print |
| Portrait | Portrait Film |
| Landscape | Faded Forest · Lush Landscape |
| Warm | Golden Hour Glow · Autumn Gold · Summer Pop |
| Cool | Arctic Steel · Day for Night |
| Mono | Warm Sepia · Silver Noir |
| Creative | Pastel Dream · Cyberpunk |

Save your own grades as **My looks** and import/export them as shareable `.json` files.

For external colour recipes, choose a LUT folder (including subfolders) and browse supported **3D `.cube`** and **HaldCLUT `.png`** files. Set **LUT strength** to taste. The LUT is applied before the other grading adjustments, so you can refine the result using the rest of the plugin.

**Important:** LUT data is **not embedded** in saved/exported looks. When reusing a look that depends on a LUT, select that LUT again. LUTs are applied to display-encoded RGB values; choose a LUT intended for your source material. External LUT packs have their own licences.

## How it handles your image

Colour Grading combines GIMP's native colour-balance and GEGL effects with targeted processing. The tabs use different colour representations for different jobs:

- **Balance:** GIMP Color Balance and GEGL saturation; Punch uses a perceptual gamma-style contrast treatment.
- **Tone:** CIE Lab lightness to identify zones; CIE LCh for lightness changes.
- **Tonal:** Lab lightness for targeting, linear-light tinting and LCh lightness adjustment.
- **Hue:** CIE LCh hue/chroma for targeting, with Lab/LCh adjustments.
- **Colour:** linear-light channel mixing for primaries and tint, GEGL temperature, and LCh vibrance.
- **Scopes:** a reduced 8-bit, sRGB-style preview sample for monitoring, not a full-precision measurement.
- **LUTs:** display-encoded RGB, as expected by many photographic LUTs.

The original layer's colour profile is not deliberately replaced. Use **Colour → Open report…** to inspect profile/precision information and grading warnings.

## A few things to know

- Select a **normal RGB layer**, not a layer group. You can select a normal layer *inside* a group.
- The preview is built on a **copy of the selected layer**, not a flattened composite of every visible layer.
- Tone, Tonal and Hue targeting can take longer to preview on large images; the full-quality result follows after interaction.
- Scopes use a reduced 8-bit sample and refresh on demand; they are guidance, not reference-grade measurement instruments.
- A LUT designed for log footage, for example, may produce unexpected colours on a regular photograph.
- The plugin is a GIMP 3.2 Python plug-in; compatibility with individual GIMP distributions and operating systems should be reported with the exact GIMP version and any console error.

## Feedback, bugs and contributions

Ideas, screenshots, bug reports and contributions are welcome. When reporting a problem, please include your **operating system, GIMP version, image mode/precision, steps to reproduce and any error message**. Before/after examples are particularly helpful for colour issues.

## Licence

**GPL-3.0-only** — GNU General Public License, version 3 only. See the licence notice in [`colour-grading.py`](colour-grading.py) and the [GPL v3 text](https://www.gnu.org/licenses/gpl-3.0.html).

External LUTs and other third-party assets retain their own licences.

## Credits

**Concept and design:** Zarir Madon — [zarirmadon.com](https://www.zarirmadon.com) · [zmcreative.art](https://www.zmcreative.art)

**Code:** Claude Sonnet 5.5 and OpenAI GPT 5.6, as credited in the plugin source.

---

*Make it warmer. Make it moodier. Make it yours.*
