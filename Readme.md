# emeet-cli

A command-line tool to control the **eMeet Pixy** webcam — take photos, adjust zoom, and tilt the camera without opening eMeet Studio.

## Overview

The eMeet Pixy is a USB UVC-class camera with PTZ (pan/tilt/zoom) capabilities. This CLI communicates directly with the camera over USB using UVC extension unit controls, bypassing the eMeet Studio GUI entirely.

**Camera identifiers:**
- USB Vendor ID: `0x328F` (EMEET)
- USB Product ID: `0x00C0` (EMEET PIXY)

## Features

- **Capture** — take a still photo and save it to a file
- **Zoom** — set the optical/digital zoom level
- **Tilt** — tilt the camera up or down
- **Pan** — pan the camera left or right
- **Reset** — return the camera to its default position and zoom

## Requirements

- macOS 12+, Python 3.10+
- eMeet Pixy connected via USB
- [libusb](https://libusb.info/) for UVC control: `brew install libusb`

## Installation

```bash
git clone https://github.com/yourname/emeet-cli
cd emeet-cli
pip install -e .
```

The `emeet` command is installed to `~/Library/Python/3.x/bin/`. Add it to your PATH if needed:

```bash
export PATH="$HOME/Library/Python/3.13/bin:$PATH"
```

Or invoke directly without PATH changes:

```bash
python3 -m emeet_cli.cli --help
```

## Usage

All levels are normalized **0–100**. For pan and tilt, 50 is center.

### Take a photo

```bash
emeet capture                          # saves photo.jpg
emeet capture -o snapshot.png          # PNG output
emeet capture -o photo.jpg -d 1        # use camera at device index 1
```

### Zoom

```bash
emeet zoom          # show current zoom
emeet zoom 50       # set to 50% (mid zoom)
emeet zoom 0        # widest angle
emeet zoom 100      # maximum zoom
```

### Tilt

```bash
emeet tilt          # show current tilt
emeet tilt 50       # center
emeet tilt 80       # tilt up
emeet tilt 20       # tilt down
```

### Pan

```bash
emeet pan           # show current pan
emeet pan 50        # center
emeet pan 100       # full right
emeet pan 0         # full left
```

### Reset

```bash
emeet reset         # center pan/tilt and set minimum zoom
```

### Show all current values

```bash
emeet info
```

### List camera devices

```bash
emeet devices
```

### Chaining commands

```bash
emeet tilt 70 && emeet zoom 60 && emeet capture -o shot.jpg
```

## How It Works

The eMeet Pixy is a standard UVC-class USB device. This tool:

- **PTZ control** — sends UVC `CT_ZOOM_ABSOLUTE_CONTROL` (selector `0x0B`) and `CT_PAN_TILT_ABSOLUTE_CONTROL` (selector `0x0D`) SET_CUR/GET_CUR requests via `pyusb` + `libusb`. The Camera Terminal unit ID and VideoControl interface number are parsed from the live USB descriptors.
- **Image capture** — uses OpenCV with the `CAP_AVFOUNDATION` backend to grab a frame and write it to disk.

User-facing levels (0–100) are mapped to the device's own min/max range, which is queried at runtime via GET_MIN/GET_MAX.

## Project layout

```
emeet_cli/
  uvc.py      UVC protocol constants, descriptor parsing, raw control transfers
  camera.py   High-level API (zoom_set, tilt_set, pan_set, capture, …)
  cli.py      Click-based CLI entry point
pyproject.toml
```

## Development

```bash
pip install -e .
python3 -m emeet_cli.cli --help
```

## Limitations

- Pan/tilt range depends on what the eMeet Pixy hardware physically supports
- Close eMeet Studio (and Zoom/Teams) before running — UVC control transfers may be blocked while another app holds the camera
- Tested on macOS only; Linux support via V4L2 is possible but not implemented

## License

MIT
