"""
High-level eMeet Pixy camera interface.

Wraps UVC control requests and OpenCV capture behind a clean API.
All pan/tilt/zoom values exposed to the user are normalized 0–100,
mapped to the device's actual reported min/max range.
"""

import cv2
import contextlib
from . import uvc
import AVFoundation as av

def _scale(value: float, src_min, src_max, dst_min, dst_max) -> int:
    """Linearly map value from [src_min, src_max] → [dst_min, dst_max]."""
    ratio = (value - src_min) / (src_max - src_min)
    return int(dst_min + ratio * (dst_max - dst_min))


@contextlib.contextmanager
def open_device():
    """Context manager that yields (dev, terminal_id, vc_intf_num)."""
    dev = uvc.find_device()
    terminal_id, vc_intf_num = uvc.find_camera_terminal(dev)
    try:
        yield dev, terminal_id, vc_intf_num
    finally:
        uvc.usb.util.dispose_resources(dev)


# ---------------------------------------------------------------------------
# Zoom
# ---------------------------------------------------------------------------

def zoom_set(level: int):
    """
    Set zoom to a normalized level 0–100.
    0 = widest angle, 100 = maximum zoom.
    """
    with open_device() as (dev, tid, intf):
        z_min, z_max = uvc.get_zoom_range(dev, tid, intf)
        target = _scale(level, 0, 100, z_min, z_max)
        uvc.set_zoom(dev, tid, intf, target)


def zoom_get() -> dict:
    """Return current zoom and device range."""
    with open_device() as (dev, tid, intf):
        z_min, z_max = uvc.get_zoom_range(dev, tid, intf)
        current = uvc.get_zoom(dev, tid, intf)
        normalized = _scale(current, z_min, z_max, 0, 100)
        return {"current": normalized, "raw": current, "raw_min": z_min, "raw_max": z_max}


# ---------------------------------------------------------------------------
# Pan / Tilt
# ---------------------------------------------------------------------------

def _pan_tilt_set(pan_level=None, tilt_level=None):
    """
    Set pan and/or tilt. Levels are normalized 0–100.
    50 = center, 0 = full left/down, 100 = full right/up.
    Pass None to leave an axis unchanged.
    """
    with open_device() as (dev, tid, intf):
        (p_min, p_max), (t_min, t_max) = uvc.get_pan_tilt_range(dev, tid, intf)
        current_pan, current_tilt = uvc.get_pan_tilt(dev, tid, intf)

        new_pan  = _scale(pan_level,  0, 100, p_min, p_max) if pan_level  is not None else current_pan
        new_tilt = _scale(tilt_level, 0, 100, t_min, t_max) if tilt_level is not None else current_tilt

        uvc.set_pan_tilt(dev, tid, intf, new_pan, new_tilt)


def pan_set(level: int):
    """Set pan to normalized level 0–100 (50 = center)."""
    _pan_tilt_set(pan_level=level)


def tilt_set(level: int):
    """Set tilt to normalized level 0–100 (50 = center)."""
    _pan_tilt_set(tilt_level=level)


def pan_tilt_get() -> dict:
    """Return current pan/tilt positions and device ranges."""
    with open_device() as (dev, tid, intf):
        (p_min, p_max), (t_min, t_max) = uvc.get_pan_tilt_range(dev, tid, intf)
        pan, tilt = uvc.get_pan_tilt(dev, tid, intf)
        return {
            "pan":  {"current": _scale(pan,  p_min, p_max, 0, 100), "raw": pan},
            "tilt": {"current": _scale(tilt, t_min, t_max, 0, 100), "raw": tilt},
        }


def reset():
    """Return camera to center position and minimum zoom."""
    with open_device() as (dev, tid, intf):
        z_min, _ = uvc.get_zoom_range(dev, tid, intf)
        uvc.set_zoom(dev, tid, intf, z_min)

        (p_min, p_max), (t_min, t_max) = uvc.get_pan_tilt_range(dev, tid, intf)
        center_pan  = (p_min + p_max) // 2
        center_tilt = (t_min + t_max) // 2
        uvc.set_pan_tilt(dev, tid, intf, center_pan, center_tilt)


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def _find_pixy_index() -> int:
    """
    Return the OpenCV device index that delivers the live eMeet Pixy feed.

    eMeet installs a system extension (com.emeet.studio.mac-camera-extension)
    that runs permanently and exposes the physical camera feed through a virtual
    device. Because cv2 and AVFoundation can disagree on device ordering,
    we probe each candidate device and pick the one with real image content
    (highest mean pixel value, indicating a live scene rather than a logo).
    """

    av_devices = av.AVCaptureDevice.devicesWithMediaType_(av.AVMediaTypeVideo)

    # Collect indices for any eMeet-related device name
    candidates = []
    for idx in range(len(av_devices)):
        cap = cv2.VideoCapture(idx, cv2.CAP_AVFOUNDATION)
        if not cap.isOpened():
            cap.release()
            continue
        ret, frame = cap.read()
        cap.release()
        if ret and frame is not None:
            candidates.append((idx, float(frame.mean())))

    if not candidates:
        raise RuntimeError("No eMeet camera found. Is it connected? Run 'emeet devices' to list cameras.")

    # Pick the device with the brightest/most-content frame (live scene > logo)
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def capture(output_path: str, device_index: int = None):
    """
    Capture a single frame from the camera and save it to output_path.
    Supports JPEG and PNG based on the file extension.
    If device_index is not given, the EMEET PIXY is located automatically.
    """
    if device_index is None:
        device_index = _find_pixy_index()

    cap = cv2.VideoCapture(device_index, cv2.CAP_AVFOUNDATION)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera at index {device_index}.")

    try:
        # Discard a few frames so the camera auto-exposure settles
        for _ in range(5):
            cap.read()

        ret, frame = cap.read()
        if not ret or frame is None:
            raise RuntimeError("Failed to read a frame from the camera.")

        ok = cv2.imwrite(output_path, frame)
        if not ok:
            raise RuntimeError(f"Failed to write image to {output_path!r}.")
    finally:
        cap.release()


def list_cameras() -> list[dict]:
    """
    Return available cameras with index, name, and resolution.
    Uses AVFoundation for device names (matches OpenCV's index order).
    """
    
    av_devices = av.AVCaptureDevice.devicesWithMediaType_(av.AVMediaTypeVideo)
    names = [d.localizedName() for d in av_devices]

    found = []
    for idx, name in enumerate(names):
        cap = cv2.VideoCapture(idx, cv2.CAP_AVFOUNDATION)
        if cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            found.append({"index": idx, "name": name, "width": w, "height": h})
            cap.release()
    return found
