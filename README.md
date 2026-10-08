## Recommended Setup

The recommended dependency manager for this project is `vcpkg`.

1. Install `vcpkg`.
2. Use `vcpkg` to install the required libraries.
3. Update `VCPKG_ROOT` in [CMakePresets.json](/f:/dev2/cvf2/CMakePresets.json) to match your local `vcpkg` path.
4. Configure and build the project with CMake presets.

The current preset file contains:

```json
"environment": {
  "VCPKG_ROOT": "F:/vcpkg/vcpkg"
}
```

If your local `vcpkg` is installed in a different directory, change that path before configuring the project.

## Dependencies

This project currently depends on the following libraries:

- `opencv`
- `assimp`
- `freeglut`
- `glm`
- `nlohmann-json`


## Install Dependencies With vcpkg

For the current Windows x64 preset, install the dependencies with:

```powershell
vcpkg install assimp:x64-windows freeglut:x64-windows glm:x64-windows nlohmann-json:x64-windows opencv:x64-windows
```

## Configure And Build

After `vcpkg` is ready and `VCPKG_ROOT` has been updated:

```powershell
cmake --preset msvc-x64
cmake --build --preset release
```

## Remote imshow (display images on a desktop from another device)

`Python/runImshowServer.py` displays the images that a program pushes over the network, which is
handy when the program runs where a local window is inconvenient to watch (e.g. an Android device).

Start the server on the machine that should show the images:

```powershell
python Python/runImshowServer.py --port 8100
```

Then, on the client side, configure the connection and push frames (`CVX/imshow_remote.h`, in the
`cv` namespace):

```cpp
#include "CVX/imshow_remote.h"

cv::imshow_remote_init("192.168.1.10", 8100); // server ip and port, once
cv::imshow_remote("left", leftImg);           // any thread, returns immediately
cv::imshow_remote("depth", depthImg16U);      // 16-bit/float Mats are sent lossless
cv::imshow_remote("left", img, "frame 12");   // optional 3rd argument: info carried with the frame
cv::imshow_remote_close("left");              // optional, closes the sub-window
```

The optional third argument of `imshow_remote()` (default `""`, utf-8) attaches text to a frame: a
status string, a detector result, a key of the sample being captured, ... It is what the server
writes into the `msg` field of the recording sidecar, which is how captured data stays linked to
the code that produced it.

Every client process gets one top-level window on the server, and every distinct name passed to
`imshow_remote()` is a floating sub-window inside it: a sub-window is as large as the image, the
image cannot be scrolled or panned, but the sub-window can be dragged with the mouse (title bar or
image) and may overlap the other sub-windows. The mouse wheel zooms a sub-window, its `✕` button
hides it until `u` is pressed or the client reconnects. Sending is asynchronous: the newest frame
of each sub-window wins, and frames are dropped silently while the server is unreachable, so image
output never blocks the caller. `CVX/imshow_remote.h` documents the environment variables that
override the target and the encoding.

The client is identified by `<hostname>:<program name>` (`test1.exe` sends `mypc:test1`), so
starting the program again reuses the window of the previous run and simply updates its images:
the window, the sub-windows and their positions all survive. Set `IMSHOW_REMOTE_CLIENT` to a
different value to give an instance a window of its own, e.g. when two instances of the same
program are watched at the same time. Closing a window with its `✕` button stops it from being
recreated until the client reconnects (its next `hello`/run brings it back).

Useful server options: `--selftest` (built-in sender, no client needed), `--colormap turbo` and
`--range LO HI` for depth maps, `--win-size WxH`, `--cascade N`, `--fit-max N` (scale down images
larger than N pixels), `--save-dir` (the `s` key saves the current frame of every sub-window),
`s`/`c`/`u`/`q` keys (save / clear / unhide / quit).

### Recording a sub-window on the server side

A sub-window can be stored as a video or as an image sequence, either from the command line or
while the program runs:

```powershell
# record every "depth" sub-window of every client as lossless 16 bit png frames
python Python/runImshowServer.py --record depth --record-format raw --record-dir data/depth

# record "color" into a 30 fps mp4
python Python/runImshowServer.py --record color --record-format mp4 --record-fps 30
```

Click a sub-window and press `r` to start/stop recording it (it then shows a `REC` marker, `R`
stops every recording). Recording happens in the netcall thread *before* the display coalescing, so
all the frames the client sends are captured — not only the ones that are displayed. The frames are
written by a dedicated thread, so a slow disk never blocks the connection; the frames that do not
fit `--record-queue` (default 64) are counted and reported when the recording stops.

| `--record-format` | stored data |
|---|---|
| `mp4` | the displayed image (so `--colormap` applies), fps from `--record-fps` or measured on the incoming frames |
| `seq` | the displayed image, one `.png` per frame |
| `raw` | the image as received, lossless: `.png` for `uint8` and single channel `uint16` (e.g. depth), `.npy` for anything else (e.g. `float32`) |

Output layout: `<record-dir>/<client>/<name>_<date>_<time>` (`.mp4` for a video, a folder for
`seq`/`raw`), next to it a `frames.jsonl` sidecar with one line per frame

```json
{"i": 12, "file": "000012.png", "recv": 1759912345.678901, "shape": [240, 320, 3], "dtype": "uint8", "msg": "frame 12"}
```

(`i`: frame index in the recording, `file`: the file holding that frame — the png/npy of the frame
for `seq`/`raw`, the video itself for `mp4` —, `recv`: receive time, `msg`: the third argument of
`imshow_remote()`) and a `meta.json` summary with the frame count, the measured fps and the number
of dropped frames. The video fps is only an approximation, `frames.jsonl` keeps the real timing of
every frame.

Quit the server with `q`/`Esc`/Ctrl+C so that a running recording is closed properly: a video that
is not closed ends up without its index and cannot be played. `--record-format seq`/`raw` only lose
the not-yet-written frames if the process is killed.

`ff::exec("tests.imshow_remote")` in `test1` pushes a set of synthetic images through the whole
path and is the quickest way to check the setup.
