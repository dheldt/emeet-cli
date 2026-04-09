"""
High-level eMeet Pixy camera interface.

Wraps UVC control requests and frame capture behind a clean API.
All pan/tilt/zoom values exposed to the user are normalized 0–100,
mapped to the device's actual reported min/max range.
"""

import contextlib
import platform
from pathlib import Path

_PLATFORM = platform.system()

if _PLATFORM == "Darwin":
    import AVFoundation as av
else:
    av = None

if _PLATFORM == "Linux":
    from . import v4l2


def _require_uvc():
    try:
        from . import uvc
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "USB control support requires the 'pyusb' dependency in your environment."
        ) from exc
    return uvc

def _scale(value: float, src_min, src_max, dst_min, dst_max) -> int:
    """Linearly map value from [src_min, src_max] → [dst_min, dst_max]."""
    if src_max == src_min:
        return int(dst_min)
    ratio = (value - src_min) / (src_max - src_min)
    return int(dst_min + ratio * (dst_max - dst_min))


def _require_cv2():
    try:
        import cv2
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "OpenCV is required for camera capture and device listing. Install the "
            "'opencv-python' dependency in your environment."
        ) from exc
    return cv2


def _capture_backend(cv2):
    if _PLATFORM == "Darwin":
        return cv2.CAP_AVFOUNDATION
    if _PLATFORM == "Linux" and hasattr(cv2, "CAP_V4L2"):
        return cv2.CAP_V4L2
    return cv2.CAP_ANY


def _iter_linux_video_devices():
    sys_class = Path("/sys/class/video4linux")
    if not sys_class.exists():
        return

    for entry in sorted(sys_class.glob("video*")):
        try:
            node_index = int(entry.name.replace("video", "", 1))
        except ValueError:
            continue

        name = (entry / "name").read_text(encoding="utf-8").strip() if (entry / "name").exists() else entry.name
        modalias = (entry / "device" / "modalias").read_text(encoding="utf-8").strip() if (entry / "device" / "modalias").exists() else ""
        sysfs_index = int((entry / "index").read_text(encoding="utf-8").strip()) if (entry / "index").exists() else 0
        yield {
            "index": node_index,
            "sysfs_index": sysfs_index,
            "name": name,
            "path": f"/dev/{entry.name}",
            "modalias": modalias,
        }


def _probe_camera(index: int, name: str):
    cv2 = _require_cv2()
    backend = _capture_backend(cv2)
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return None

    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return {"index": index, "name": name, "width": width, "height": height}
    finally:
        cap.release()


def _linux_pixy_device():
    for device in _iter_linux_video_devices() or ():
        device_name = device["name"].lower()
        modalias = device["modalias"].lower()
        if "emeet" not in device_name and "v328f" not in modalias and "p00c0" not in modalias:
            continue
        if device["sysfs_index"] == 0:
            return device
    for device in _iter_linux_video_devices() or ():
        device_name = device["name"].lower()
        modalias = device["modalias"].lower()
        if "emeet" in device_name or "v328f" in modalias or "p00c0" in modalias:
            return device
    raise RuntimeError("No eMeet camera found. Is it connected? Run 'emeet devices' to list cameras.")


def _linux_query(control_id: int) -> dict:
    return v4l2.query_control(_linux_pixy_device()["path"], control_id)


def _linux_get(control_id: int) -> int:
    return v4l2.get_control(_linux_pixy_device()["path"], control_id)


def _linux_set(control_id: int, value: int):
    v4l2.set_control(_linux_pixy_device()["path"], control_id, value)


@contextlib.contextmanager
def open_device():
    """Context manager that yields (dev, terminal_id, vc_intf_num)."""
    uvc = _require_uvc()
    dev = uvc.find_device()
    terminal_id, vc_intf_num = uvc.find_camera_terminal(dev)
    state = uvc.prepare_device(dev, vc_intf_num)
    try:
        yield dev, terminal_id, vc_intf_num
    finally:
        uvc.release_device(dev, state)
        uvc.usb.util.dispose_resources(dev)


# ---------------------------------------------------------------------------
# Zoom
# ---------------------------------------------------------------------------

def zoom_set(level: int):
    """
    Set zoom to a normalized level 0–100.
    0 = widest angle, 100 = maximum zoom.
    """
    if _PLATFORM == "Linux":
        ctrl = _linux_query(v4l2.V4L2_CID_ZOOM_ABSOLUTE)
        target = _scale(level, 0, 100, ctrl["min"], ctrl["max"])
        _linux_set(v4l2.V4L2_CID_ZOOM_ABSOLUTE, target)
        return

    with open_device() as (dev, tid, intf):
        z_min, z_max = uvc.get_zoom_range(dev, tid, intf)
        target = _scale(level, 0, 100, z_min, z_max)
        uvc.set_zoom(dev, tid, intf, target)


def zoom_get() -> dict:
    """Return current zoom and device range."""
    if _PLATFORM == "Linux":
        ctrl = _linux_query(v4l2.V4L2_CID_ZOOM_ABSOLUTE)
        current = _linux_get(v4l2.V4L2_CID_ZOOM_ABSOLUTE)
        return {
            "current": _scale(current, ctrl["min"], ctrl["max"], 0, 100),
            "raw": current,
            "raw_min": ctrl["min"],
            "raw_max": ctrl["max"],
        }

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
    if _PLATFORM == "Linux":
        pan_ctrl = _linux_query(v4l2.V4L2_CID_PAN_ABSOLUTE)
        tilt_ctrl = _linux_query(v4l2.V4L2_CID_TILT_ABSOLUTE)
        current_pan = _linux_get(v4l2.V4L2_CID_PAN_ABSOLUTE)
        current_tilt = _linux_get(v4l2.V4L2_CID_TILT_ABSOLUTE)

        new_pan = _scale(pan_level, 0, 100, pan_ctrl["min"], pan_ctrl["max"]) if pan_level is not None else current_pan
        new_tilt = _scale(tilt_level, 0, 100, tilt_ctrl["min"], tilt_ctrl["max"]) if tilt_level is not None else current_tilt

        if pan_level is not None:
            _linux_set(v4l2.V4L2_CID_PAN_ABSOLUTE, new_pan)
        if tilt_level is not None:
            _linux_set(v4l2.V4L2_CID_TILT_ABSOLUTE, new_tilt)
        return

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
    if _PLATFORM == "Linux":
        pan_ctrl = _linux_query(v4l2.V4L2_CID_PAN_ABSOLUTE)
        tilt_ctrl = _linux_query(v4l2.V4L2_CID_TILT_ABSOLUTE)
        pan = _linux_get(v4l2.V4L2_CID_PAN_ABSOLUTE)
        tilt = _linux_get(v4l2.V4L2_CID_TILT_ABSOLUTE)
        return {
            "pan": {"current": _scale(pan, pan_ctrl["min"], pan_ctrl["max"], 0, 100), "raw": pan},
            "tilt": {"current": _scale(tilt, tilt_ctrl["min"], tilt_ctrl["max"], 0, 100), "raw": tilt},
        }

    with open_device() as (dev, tid, intf):
        (p_min, p_max), (t_min, t_max) = uvc.get_pan_tilt_range(dev, tid, intf)
        pan, tilt = uvc.get_pan_tilt(dev, tid, intf)
        return {
            "pan":  {"current": _scale(pan,  p_min, p_max, 0, 100), "raw": pan},
            "tilt": {"current": _scale(tilt, t_min, t_max, 0, 100), "raw": tilt},
        }


def reset():
    """Return camera to center position and minimum zoom."""
    if _PLATFORM == "Linux":
        zoom_ctrl = _linux_query(v4l2.V4L2_CID_ZOOM_ABSOLUTE)
        pan_ctrl = _linux_query(v4l2.V4L2_CID_PAN_ABSOLUTE)
        tilt_ctrl = _linux_query(v4l2.V4L2_CID_TILT_ABSOLUTE)
        _linux_set(v4l2.V4L2_CID_ZOOM_ABSOLUTE, zoom_ctrl["min"])
        _linux_set(v4l2.V4L2_CID_PAN_ABSOLUTE, (pan_ctrl["min"] + pan_ctrl["max"]) // 2)
        _linux_set(v4l2.V4L2_CID_TILT_ABSOLUTE, (tilt_ctrl["min"] + tilt_ctrl["max"]) // 2)
        return

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

    On Linux, this is resolved from /sys/class/video4linux using the device
    name or USB modalias. On macOS, device ordering can differ between OpenCV
    and AVFoundation, so we probe each candidate and choose the one with real
    image content.
    """

    cv2 = _require_cv2()
    backend = _capture_backend(cv2)

    if _PLATFORM == "Linux":
        return _linux_pixy_device()["index"]

    if _PLATFORM == "Darwin":
        av_devices = av.AVCaptureDevice.devicesWithMediaType_(av.AVMediaTypeVideo)
        candidates = []
        for idx in range(len(av_devices)):
            cap = cv2.VideoCapture(idx, backend)
            if not cap.isOpened():
                cap.release()
                continue
            ret, frame = cap.read()
            cap.release()
            if ret and frame is not None:
                candidates.append((idx, float(frame.mean())))

        if candidates:
            candidates.sort(key=lambda item: item[1], reverse=True)
            return candidates[0][0]

    for idx in range(10):
        cap = cv2.VideoCapture(idx, backend)
        if cap.isOpened():
            cap.release()
            return idx
        cap.release()

    raise RuntimeError("No eMeet camera found. Is it connected? Run 'emeet devices' to list cameras.")


def capture(output_path: str, device_index: int = None):
    """
    Capture a single frame from the camera and save it to output_path.
    Supports JPEG and PNG based on the file extension.
    If device_index is not given, the EMEET PIXY is located automatically.
    """
    cv2 = _require_cv2()
    backend = _capture_backend(cv2)

    if device_index is None:
        device_index = _find_pixy_index()

    cap = cv2.VideoCapture(device_index, backend)
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
    """
    if _PLATFORM == "Darwin":
        names = [d.localizedName() for d in av.AVCaptureDevice.devicesWithMediaType_(av.AVMediaTypeVideo)]
        found = []
        for idx, name in enumerate(names):
            info = _probe_camera(idx, name)
            if info is not None:
                found.append(info)
        return found

    if _PLATFORM == "Linux":
        found = []
        for device in _iter_linux_video_devices() or ():
            if device["sysfs_index"] != 0:
                continue
            info = _probe_camera(device["index"], device["name"])
            if info is not None:
                found.append(info)
        return found

    found = []
    for idx in range(10):
        info = _probe_camera(idx, f"Camera {idx}")
        if info is not None:
            found.append(info)
    return found
