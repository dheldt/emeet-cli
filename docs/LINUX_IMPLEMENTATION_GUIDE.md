# Linux Implementation Guide and Mode-Switch Exploration

## Purpose

This document captures two things:

1. How Linux support was implemented for `emeet-cli`.
2. What was explored while trying to add CLI support for switching the eMeet Pixy between `tracking` and `standard` modes.

The Linux support work in this file reflects the current codebase.
The mode-switch work is exploratory only and was not shipped.

## Linux Implementation Guide

### Summary

The original project was macOS-oriented:

- it depended on `pyobjc-framework-AVFoundation`
- it imported `AVFoundation` at module import time
- it used the OpenCV AVFoundation backend for capture
- it used a USB/UVC control path that was reasonable on macOS but disruptive on Linux

Linux support was added by separating platform-specific behavior and moving Linux PTZ control onto V4L2 instead of raw USB control transfers.

### Packaging

Linux installation was failing because `pyobjc-framework-AVFoundation` was listed as a normal dependency.
That dependency was made macOS-only in `pyproject.toml`.

Current packaging behavior:

- `click`, `pyusb`, and `opencv-python` remain normal dependencies
- `pyobjc-framework-AVFoundation` is only installed on Darwin

This change prevents Linux environments from trying to install macOS-only bindings.

### Runtime import strategy

Two import-time issues had to be removed:

1. `AVFoundation` was imported unconditionally.
2. `cv2` was imported globally even for PTZ-only commands.

Current behavior in `emeet_cli/camera.py`:

- `AVFoundation` is only imported on macOS
- OpenCV is loaded lazily through `_require_cv2()`
- USB/UVC support is loaded lazily through `_require_uvc()`

That means:

- `emeet pan`, `emeet tilt`, `emeet zoom`, `emeet reset`, and `emeet info` do not fail just because OpenCV is missing
- Linux can import the package without `AVFoundation`
- error messages are more specific when optional runtime pieces are missing

### Linux camera discovery

Linux device discovery is implemented in `emeet_cli/camera.py` using `/sys/class/video4linux`.

Key pieces:

- `_iter_linux_video_devices()` walks `video*` entries under `/sys/class/video4linux`
- each device record includes:
  - OpenCV/V4L2 index
  - human-readable name
  - `/dev/video*` path
  - USB modalias
- `_linux_pixy_device()` identifies the Pixy by either:
  - `EMEET` in the device name
  - vendor/product markers in the modalias (`v328f`, `p00c0`)

This is more reliable than assuming a fixed camera index.

### Linux capture backend

Image capture now uses platform-specific OpenCV backends:

- macOS: `CAP_AVFOUNDATION`
- Linux: `CAP_V4L2` when available
- fallback: `CAP_ANY`

This logic is handled by `_capture_backend()` in `emeet_cli/camera.py`.

### Device listing cleanup

The Linux system exposed multiple `video*` nodes for the same hardware, including secondary or metadata-related nodes that were not appropriate for direct probing.

To reduce noise:

- Linux camera listing only probes nodes where the sysfs `index` is `0`
- this avoids duplicate or non-primary nodes
- it removes the earlier warning spam from OpenCV/V4L2 when probing non-capture siblings

### Linux PTZ control design

The important Linux design decision was to avoid using the original raw USB UVC control path for PTZ operations.

Why:

- on Linux, using the USB/libusb PTZ path could move the camera
- but it could also disturb the kernel camera stack enough to make an active Google Meet session lose the device temporarily
- the camera would often reappear only after another query or after the stack settled again

The fix was to use V4L2 controls for Linux PTZ instead.

Current Linux PTZ behavior:

- `pan`, `tilt`, `zoom`, and `reset` are implemented via V4L2 controls
- this logic lives in:
  - `emeet_cli/camera.py`
  - `emeet_cli/v4l2.py`

The V4L2 control IDs in use are:

- `V4L2_CID_PAN_ABSOLUTE`
- `V4L2_CID_TILT_ABSOLUTE`
- `V4L2_CID_ZOOM_ABSOLUTE`

The V4L2 helper layer provides:

- `query_control()`
- `get_control()`
- `set_control()`

These are thin wrappers around `VIDIOC_QUERYCTRL`, `VIDIOC_G_CTRL`, and `VIDIOC_S_CTRL`.

### Linux PTZ value mapping

The CLI continues to expose normalized values in the range `0..100`.

On Linux:

- the actual device min/max are queried from V4L2
- normalized CLI values are mapped through `_scale()`
- raw device values are returned in `info`-style calls where appropriate

This preserves the user-facing interface while adapting to the real device ranges reported by the driver.

### macOS path preservation

Linux support did not replace the macOS implementation.

Current split:

- Linux PTZ path: V4L2 controls
- macOS PTZ path: UVC/libusb path from `emeet_cli/uvc.py`
- Linux capture path: OpenCV + V4L2 backend
- macOS capture path: OpenCV + AVFoundation backend

### libusb loading

`emeet_cli/uvc.py` was updated so `libusb` lookup covers Linux library paths as well as the earlier Homebrew/macOS paths.

This mainly affects:

- Linux environments that need the macOS-style UVC code path in the future
- clearer `libusb` installation errors

### Current Linux result

At the end of this work, the Linux-relevant behavior is:

- installation works
- import-time macOS failures are removed
- `emeet devices` works
- `emeet capture` works through V4L2/OpenCV
- `emeet pan`, `emeet tilt`, `emeet zoom`, `emeet reset`, and `emeet info` work
- PTZ changes no longer cause the camera to disappear from Google Meet

## Exploration: Tracking/Standard Mode Switch

### Goal

The goal was to support a CLI command like:

```bash
emeet mode tracking
emeet mode standard
```

This was investigated, but not implemented in the final code.

### Exploration path

The following paths were investigated in order:

1. standard Linux V4L2 controls
2. UVC camera terminal controls
3. UVC vendor extension-unit controls
4. Linux UVC ROI / detect-and-track definitions
5. HID traffic monitoring
6. raw USB bus capture with `usbmon`

### Standard V4L2 controls

The first assumption was that tracking mode might be exposed as a normal Linux V4L2 control.

Observed controls included:

- `pan_absolute`
- `tilt_absolute`
- `zoom_absolute`
- `focus_absolute`
- `focus_automatic_continuous`
- exposure-related controls

No obvious `tracking`, `auto-framing`, or `mode` control was exposed as a standard V4L2 control.

Conclusion:

- standard V4L2 control enumeration was not enough

### UVC descriptors and extension unit

The Pixy USB descriptors showed:

- normal UVC camera controls
- a vendor extension unit with GUID `{46394292-0cd0-4ae3-8783-3133f9eaaa3b}`
- a separate HID interface

This suggested two realistic places for the mode switch:

- vendor UVC extension unit
- HID interface

### UVC extension-unit probing

The vendor extension unit was enumerated and several selectors could be read.

What was learned:

- some selectors clearly changed with PTZ state
- manual switching between `tracking` and `standard` in the vendor app did not cause any visible change in the watched extension-unit selectors

Conclusion:

- the mode state did not appear to be expressed in the observed UVC extension-unit values

### ROI / detect-and-track hypothesis

Linux headers exposed UVC Region of Interest definitions, including a documented detect-and-track bit.
That made ROI look like a plausible Linux-facing mode representation.

This path was explored and briefly used as the basis for a temporary `mode` implementation, but it turned out to be wrong.

Why it was rejected:

- the inferred state did not match the actual camera mode
- the payload stayed constant even when the mode changed in the vendor application

Conclusion:

- the ROI-based interpretation was a false lead

### HID monitoring

The first real mode-related signal appeared on the Pixy's HID interface.

Using `usbhid-dump`, the camera emitted report `0x09`, and byte `8` changed when the mode was toggled manually:

- one state produced byte `8 == 0`
- the other produced byte `8 == 1`

This was the strongest result of the exploration.

Conclusion:

- the actual mode signal is on the HID side
- the earlier UVC-based guesses were not the right control path

### USB bus capture

`usbmon` and `tcpdump` were then used to capture the Pixy's traffic on its USB bus.

This showed that:

- bus capture is possible on the system
- the capture includes the relevant device traffic
- the raw text-mode output is very noisy because video/audio streaming traffic dominates the dump

The session did not successfully isolate the exact outbound host-to-device HID command responsible for toggling the mode.

### Why the feature was dropped

The mode-switch feature was intentionally removed because it could not be implemented reliably from the available evidence.

Specifically:

- the first implementation attempt was based on a wrong UVC-side assumption
- the real observable state change was later found on the HID interface
- the actual outbound write command was not isolated with enough confidence

Leaving a guessed `mode` command in the CLI would have made the tool misleading and unreliable.

### Most promising future direction

If this feature is revisited later, the best path is:

1. capture the exact outbound HID command from the vendor app
2. identify the report format and write path
3. implement switching through the HID interface, not through guessed UVC/V4L2 state

## Files Relevant to This Work

- `pyproject.toml`
- `Readme.md`
- `emeet_cli/camera.py`
- `emeet_cli/uvc.py`
- `emeet_cli/v4l2.py`
- `emeet_cli/cli.py`
