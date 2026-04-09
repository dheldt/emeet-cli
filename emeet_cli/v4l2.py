"""
Linux V4L2 control helpers for pan/tilt/zoom.
"""

import ctypes
import fcntl
import os

# ioctl encoding from asm-generic/ioctl.h
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS = 2

_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS

_IOC_NONE = 0
_IOC_WRITE = 1
_IOC_READ = 2


def _IOC(direction, ioctl_type, nr, size):
    return (
        (direction << _IOC_DIRSHIFT)
        | (ioctl_type << _IOC_TYPESHIFT)
        | (nr << _IOC_NRSHIFT)
        | (size << _IOC_SIZESHIFT)
    )


def _IOWR(ioctl_type, nr, struct_type):
    return _IOC(_IOC_READ | _IOC_WRITE, ioctl_type, nr, ctypes.sizeof(struct_type))


class _V4L2QueryCtrl(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("name", ctypes.c_uint8 * 32),
        ("minimum", ctypes.c_int32),
        ("maximum", ctypes.c_int32),
        ("step", ctypes.c_int32),
        ("default_value", ctypes.c_int32),
        ("flags", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 2),
    ]


class _V4L2Control(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("value", ctypes.c_int32),
    ]


VIDIOC_QUERYCTRL = _IOWR(ord("V"), 36, _V4L2QueryCtrl)
VIDIOC_G_CTRL = _IOWR(ord("V"), 27, _V4L2Control)
VIDIOC_S_CTRL = _IOWR(ord("V"), 28, _V4L2Control)

V4L2_CTRL_CLASS_CAMERA = 0x009A0000
V4L2_CID_CAMERA_CLASS_BASE = V4L2_CTRL_CLASS_CAMERA | 0x900

V4L2_CID_PAN_ABSOLUTE = V4L2_CID_CAMERA_CLASS_BASE + 8
V4L2_CID_TILT_ABSOLUTE = V4L2_CID_CAMERA_CLASS_BASE + 9
V4L2_CID_ZOOM_ABSOLUTE = V4L2_CID_CAMERA_CLASS_BASE + 13


def _ioctl(fd, request, struct_obj):
    try:
        fcntl.ioctl(fd, request, struct_obj)
    except OSError as exc:
        raise RuntimeError(exc.strerror or str(exc)) from exc
    return struct_obj


def _open_device(device_path: str):
    try:
        return os.open(device_path, os.O_RDWR | os.O_NONBLOCK)
    except OSError as exc:
        raise RuntimeError(f"Could not open {device_path}: {exc.strerror or exc}") from exc


def query_control(device_path: str, control_id: int) -> dict:
    fd = _open_device(device_path)
    try:
        ctrl = _V4L2QueryCtrl(id=control_id)
        _ioctl(fd, VIDIOC_QUERYCTRL, ctrl)
        return {
            "min": ctrl.minimum,
            "max": ctrl.maximum,
            "step": ctrl.step,
            "default": ctrl.default_value,
            "flags": ctrl.flags,
        }
    finally:
        os.close(fd)


def get_control(device_path: str, control_id: int) -> int:
    fd = _open_device(device_path)
    try:
        ctrl = _V4L2Control(id=control_id)
        _ioctl(fd, VIDIOC_G_CTRL, ctrl)
        return ctrl.value
    finally:
        os.close(fd)


def set_control(device_path: str, control_id: int, value: int):
    fd = _open_device(device_path)
    try:
        ctrl = _V4L2Control(id=control_id, value=value)
        _ioctl(fd, VIDIOC_S_CTRL, ctrl)
    finally:
        os.close(fd)
