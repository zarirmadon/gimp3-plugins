# Remove Background for GIMP 3.2

**One less background to worry about.**

Ever wanted to lift a person, pet, product or illustrated character out of a picture without spending your afternoon painting around every stray hair? **Remove Background** brings [rembg](https://github.com/danielgatis/rembg) into GIMP with a proper preview, a choice of segmentation models, and an editable result.

It won't make a bad photograph good, but it can at least persuade the background to leave quietly.

## What it does

- **Model choice:** Pick from supported rembg models, including U²-Net, IS-Net and BiRefNet variants. There are lighter models for speed and heavier ones for fiddly edges.
- **Before/after preview:** Zoom in, fit to window and drag the comparison divider before committing anything.
- **Edge controls:** Choose **Fine** (the default), Smooth, Crisp or Feathered. Edge refinement also offers a basic mode, colour-fringe decontamination and an optional ViTMatte path.
- **Shadow preservation:** Off, Subtle, Natural or Strong. On suitable light, fairly uniform backgrounds, the plugin attempts to recover genuine cast shadows from the source image. Detected shadows go onto their **own editable layer**. No shadow detected? No fake one is invented.
- **Non-destructive output:** Place the result in a **new layer with an editable mask**, or create a new GIMP image. Your original layer is left intact.
- **Advanced options:** Post-process the mask or work with the mask alone, plus installation and update diagnostics.
- **Remembered settings:** Your previous choices return when you reopen the plugin. Because nobody enjoys setting the same five switches for the 73rd time.

The interface follows GIMP's GTK theme, with a blue highlight so you can actually see which choice is selected.

## Install

This is a **GIMP 3.2 Python plugin**, not a GIMP 2.10 plugin. It was developed around a native Linux GIMP 3.2 environment; other installations may need adjustments to their Python dependencies.

1. Make sure GIMP 3.2 can access Python packages including **rembg**, **Pillow** and **ONNX Runtime**, plus the dependencies required by your installed rembg version. Check the plugin's **Installation & Diagnostics** section if something is missing.
2. Create the plugin directory and put the complete `rembg-gimp.py` file inside it:

   ```text
   ~/.config/GIMP/3.2/plug-ins/rembg-gimp/rembg-gimp.py
   ```

3. Make the script executable:

   ```bash
   chmod +x ~/.config/GIMP/3.2/plug-ins/rembg-gimp/rembg-gimp.py
   ```

4. Restart GIMP. Find it at **Filters → Distorts → Remove Background…**

**A note about dependencies:** The Python interpreter used by GIMP must be able to import the required packages. On Arch/CachyOS, prefer compatible distribution packages where available and inspect package ownership before using `pip` or changing ONNX Runtime/CUDA components. The plugin does not install system packages for you.

## How to use it

1. Open an image and select **one raster layer**.
2. Launch **Remove Background** from the Filters menu.
3. Choose a model. If its checkpoint isn't cached yet, the plugin offers to open its model installer; downloads are **manual**, not automatic on startup.
4. Set edge refinement, quality, edge style and shadow preservation. **Fine** is the default for both quality and edge style.
5. Press **Preview**, inspect the before/after divider and adjust as needed. Preview works on reduced-size source data; final Apply uses the full-resolution layer.
6. Press **Apply**. The finished cutout is added to GIMP, and the plugin closes **after successful completion**. If something fails, the dialog stays open with an error.

**Tip:** Need to rescue a couple of hairs or a stubborn ear? Paint black or white on the new layer mask in GIMP. It's often quicker than convincing another neural network to reconsider its life choices.

## A few useful realities

**Is it fast?** That depends on the model, image size and CPU. A lightweight model can be relatively quick; a large BiRefNet model can take considerably longer to load and run. The plugin uses an isolated inference process to help keep GIMP responsive, but large image preparation still has costs.

**Does it use the GPU?** **Not in this release.** Inference currently selects ONNX Runtime's `CPUExecutionProvider`. Installing CUDA alone won't make this build switch to GPU processing.

**Will it preserve every shadow?** No. The shadow-recovery method works best with neutral/light, reasonably uniform backgrounds. Complex backgrounds, faint shadows and ambiguous dark shapes may not produce a recoverable shadow. The plugin does not manufacture shadows to fill the gap.

**Are models included?** No. Checkpoint files are downloaded separately when you explicitly request them. Some are quite large.

**Can I use the model output commercially?** Check the **licence of the model weights you choose**. The rembg library's MIT licence does *not* automatically cover every third-party checkpoint or dataset.

## Troubleshooting

- **Plugin doesn't appear:** Check that the folder and file are both named `rembg-gimp`, the `.py` file is executable, and GIMP has been restarted.
- **Missing rembg or another Python package:** Open **Advanced → Installation & Diagnostics** and inspect the Python interpreter and reported packages.
- **Model not installed:** Use the prompted model installer. Model downloads require an internet connection.
- **Slow preview or Apply:** Try a lighter model for comparison. The quality setting controls preview resolution; large models still require CPU time.
- **The cutout isn't quite right:** Try a different model or edge style, then touch up the editable layer mask. White backgrounds and fine hair are not always the best of friends.

Settings are stored at `~/.config/GIMP/3.2/rembg-gimp-settings.json`.

## Credits and licensing

**Designed and created by Zarir Madon**  
Website: [zarirmadon.com](https://www.zarirmadon.com)  
AI-assisted coding: **OpenAI GPT-6 (ChatGPT)**  
Earlier coding assistance: **Anthropic Claude Sonnet 5**  
Copyright © 2026 Zarir Madon

**Plugin source:** GNU General Public License **v3.0 only** (`GPL-3.0-only`). See the source header and the GPL-3.0 licence text. The plugin is provided without warranty.

Built using the GIMP 3 API and the independent projects [rembg](https://github.com/danielgatis/rembg) (MIT), [ONNX Runtime](https://github.com/microsoft/onnxruntime) (MIT), [Pillow](https://github.com/python-pillow/Pillow) (MIT-CMU), [NumPy](https://github.com/numpy/numpy) (BSD-3-Clause), [SciPy](https://github.com/scipy/scipy) (BSD-3-Clause), [scikit-image](https://github.com/scikit-image/scikit-image) (BSD-style, with additional notices), and [PyMatting](https://github.com/pymatting/pymatting) (MIT; dependency in some installations). GIMP itself is GPL-3.0-or-later. **Model checkpoints may carry different terms.** Third-party libraries are not bundled in this Python file.

If something goes wrong, a clear bug report and the relevant GIMP terminal output are more useful than an interpretive dance—although either is appreciated.
