"""
Low-level UVC protocol helpers.

Reference: USB Video Class spec 1.5
  - bmRequestType for SET: 0x21 (host→device, class, interface)
  - bmRequestType for GET: 0xA1 (device→host, class, interface)
  - bRequest SET_CUR: 0x01
  - bRequest GET_CUR: 0x81 / GET_MIN: 0x82 / GET_MAX: 0x83
  - wValue: (control_selector << 8)
  - wIndex: (unit_id << 8) | interface_number

macOS note
----------
pyusb's ctrl_transfer() calls claim_interface() before sending, which fails
on macOS because the UVC kernel driver holds the VideoControl interface
exclusively. UVC GET/SET requests go to endpoint 0 (the default control pipe)
and do not require the interface to be claimed. We bypass pyusb and call
libusb_control_transfer() directly via ctypes to avoid the spurious claim.
"""

import ctypes
import ctypes.util
import platform
import struct
import usb.core
import usb.util

_PLATFORM = platform.system()

# UVC device identifiers
EMEET_VENDOR_ID       = 0x328F
EMEET_PIXY_PRODUCT_ID = 0x00C0

# UVC request codes
SET_CUR = 0x01
GET_CUR = 0x81
GET_MIN = 0x82
GET_MAX = 0x83
GET_RES = 0x84

# bmRequestType
SET_REQUEST_TYPE = 0x21  # host-to-device, class, interface
GET_REQUEST_TYPE = 0xA1  # device-to-host, class, interface

# UVC Camera Terminal control selectors
CT_ZOOM_ABSOLUTE_CONTROL     = 0x0B  # 2 bytes, uint16
CT_PAN_TILT_ABSOLUTE_CONTROL = 0x0D  # 8 bytes, int32 pan + int32 tilt

# UVC descriptor constants
CS_INTERFACE    = 0x24
VC_HEADER       = 0x01
VC_INPUT_TERMINAL = 0x02
ITT_CAMERA      = 0x0201

# UVC interface class/subclass
CC_VIDEO        = 0x0E
SC_VIDEOCONTROL = 0x01

# ---------------------------------------------------------------------------
# libusb direct access (bypasses pyusb's claim_interface)
# ---------------------------------------------------------------------------

def _load_libusb():
    """Load libusb-1.0 shared library on macOS/Linux."""
    for path in (
        "/opt/homebrew/lib/libusb-1.0.dylib",
        "/usr/local/lib/libusb-1.0.dylib",
        "/usr/lib/x86_64-linux-gnu/libusb-1.0.so",
        "/usr/lib/aarch64-linux-gnu/libusb-1.0.so",
        "/usr/lib64/libusb-1.0.so",
        "/usr/lib/libusb-1.0.so",
        ctypes.util.find_library("usb-1.0"),
    ):
        if path:
            try:
                return ctypes.CDLL(path)
            except OSError:
                continue
    raise RuntimeError(
        "libusb-1.0 not found. Install it with your system package manager "
        "(for example: 'brew install libusb' or 'sudo apt install libusb-1.0-0')."
    )


_lib = _load_libusb()
_lib.libusb_control_transfer.restype  = ctypes.c_int
_lib.libusb_control_transfer.argtypes = [
    ctypes.c_void_p,   # dev_handle
    ctypes.c_uint8,    # bmRequestType
    ctypes.c_uint8,    # bRequest
    ctypes.c_uint16,   # wValue
    ctypes.c_uint16,   # wIndex
    ctypes.c_void_p,   # data
    ctypes.c_uint16,   # wLength
    ctypes.c_uint,     # timeout (ms)
]


def _get_libusb_handle(dev):
    """Extract the raw libusb_device_handle* from a pyusb Device."""
    # pyusb opens the device lazily; touching ._ctx.handle forces it open.
    dev._ctx.managed_open()
    return dev._ctx.handle.handle


def _ctrl_transfer_raw(dev, bm_request_type, b_request, w_value, w_index,
                        data_or_length, timeout=5000):
    """
    Send a USB control transfer directly via libusb, without claiming
    any interface. Returns bytes on GET, None on SET.
    """
    handle = _get_libusb_handle(dev)
    is_read = bool(bm_request_type & 0x80)

    if is_read:
        buf = (ctypes.c_uint8 * data_or_length)()
        ret = _lib.libusb_control_transfer(
            handle, bm_request_type, b_request,
            w_value, w_index, buf, data_or_length, timeout,
        )
        if ret < 0:
            raise IOError(f"libusb_control_transfer failed: {ret}")
        return bytes(buf[:ret])
    else:
        data = bytes(data_or_length)
        buf  = (ctypes.c_uint8 * len(data))(*data)
        ret  = _lib.libusb_control_transfer(
            handle, bm_request_type, b_request,
            w_value, w_index, buf, len(data), timeout,
        )
        if ret < 0:
            raise IOError(f"libusb_control_transfer failed: {ret}")
        return None


def _ctrl_transfer_pyusb(dev, bm_request_type, b_request, w_value, w_index,
                         data_or_length, timeout=5000):
    """Send a USB control transfer through pyusb."""
    result = dev.ctrl_transfer(
        bm_request_type,
        b_request,
        w_value,
        w_index,
        data_or_length,
        timeout=timeout,
    )
    if bm_request_type & 0x80:
        return bytes(result)
    return None


# ---------------------------------------------------------------------------
# Device / descriptor helpers
# ---------------------------------------------------------------------------

def find_device():
    """Find the eMeet Pixy USB device. Raises RuntimeError if not found."""
    dev = usb.core.find(idVendor=EMEET_VENDOR_ID, idProduct=EMEET_PIXY_PRODUCT_ID)
    if dev is None:
        raise RuntimeError("eMeet Pixy not found. Is it plugged in?")
    return dev


def find_camera_terminal(dev):
    """
    Parse UVC descriptors to find the Camera Terminal unit ID and
    VideoControl interface number.

    Returns (terminal_id, vc_interface_number) or raises RuntimeError.
    """
    cfg = dev.get_active_configuration()

    for intf in cfg:
        if intf.bInterfaceClass != CC_VIDEO or intf.bInterfaceSubClass != SC_VIDEOCONTROL:
            continue

        vc_intf_num = intf.bInterfaceNumber
        extra = bytes(intf.extra_descriptors)

        i = 0
        while i + 2 < len(extra):
            b_len  = extra[i]
            b_type = extra[i + 1]
            b_sub  = extra[i + 2]

            if b_len == 0:
                break

            if b_type == CS_INTERFACE and b_sub == VC_INPUT_TERMINAL and i + 5 < len(extra):
                terminal_id   = extra[i + 3]
                terminal_type = extra[i + 4] | (extra[i + 5] << 8)
                if terminal_type == ITT_CAMERA:
                    return terminal_id, vc_intf_num

            i += b_len

    raise RuntimeError("Camera Terminal not found in UVC descriptors.")


def prepare_device(dev, intf_num):
    """
    Prepare the device for UVC control transfers.

    On Linux, detach the kernel driver from the VideoControl interface so
    pyusb can claim it for class-specific control requests.
    """
    detached = False

    if _PLATFORM == "Linux":
        try:
            if dev.is_kernel_driver_active(intf_num):
                dev.detach_kernel_driver(intf_num)
                detached = True
        except (NotImplementedError, usb.core.USBError):
            detached = False

    return {"intf_num": intf_num, "detached": detached}


def release_device(dev, state):
    """Undo any Linux-specific preparation done in prepare_device()."""
    intf_num = state["intf_num"]

    try:
        usb.util.release_interface(dev, intf_num)
    except usb.core.USBError:
        pass

    if _PLATFORM == "Linux" and state["detached"]:
        try:
            dev.attach_kernel_driver(intf_num)
        except usb.core.USBError:
            pass


def _windex(unit_id, intf_num):
    return (unit_id << 8) | intf_num


# ---------------------------------------------------------------------------
# UVC GET / SET helpers
# ---------------------------------------------------------------------------

def ctrl_set(dev, unit_id, intf_num, selector, data: bytes):
    """Send a UVC SET_CUR control request."""
    transfer = _ctrl_transfer_raw if _PLATFORM == "Darwin" else _ctrl_transfer_pyusb
    transfer(dev, SET_REQUEST_TYPE, SET_CUR, (selector << 8), _windex(unit_id, intf_num), data)


def ctrl_get(dev, unit_id, intf_num, selector, length, request=GET_CUR):
    """Send a UVC GET_CUR/MIN/MAX control request, return bytes."""
    transfer = _ctrl_transfer_raw if _PLATFORM == "Darwin" else _ctrl_transfer_pyusb
    return transfer(dev, GET_REQUEST_TYPE, request, (selector << 8), _windex(unit_id, intf_num), length)


# ---------------------------------------------------------------------------
# Zoom
# ---------------------------------------------------------------------------

def get_zoom_range(dev, unit_id, intf_num):
    """Return (min_zoom, max_zoom) as integers."""
    lo = struct.unpack_from("<H", ctrl_get(dev, unit_id, intf_num, CT_ZOOM_ABSOLUTE_CONTROL, 2, GET_MIN))[0]
    hi = struct.unpack_from("<H", ctrl_get(dev, unit_id, intf_num, CT_ZOOM_ABSOLUTE_CONTROL, 2, GET_MAX))[0]
    return lo, hi


def get_zoom(dev, unit_id, intf_num):
    """Return current zoom as an integer."""
    raw = ctrl_get(dev, unit_id, intf_num, CT_ZOOM_ABSOLUTE_CONTROL, 2)
    return struct.unpack_from("<H", raw)[0]


def set_zoom(dev, unit_id, intf_num, value: int):
    """Set absolute zoom. value must be within the device's reported range."""
    ctrl_set(dev, unit_id, intf_num, CT_ZOOM_ABSOLUTE_CONTROL, struct.pack("<H", value))


# ---------------------------------------------------------------------------
# Pan / Tilt
# ---------------------------------------------------------------------------

def get_pan_tilt_range(dev, unit_id, intf_num):
    """Return ((pan_min, pan_max), (tilt_min, tilt_max))."""
    lo = ctrl_get(dev, unit_id, intf_num, CT_PAN_TILT_ABSOLUTE_CONTROL, 8, GET_MIN)
    hi = ctrl_get(dev, unit_id, intf_num, CT_PAN_TILT_ABSOLUTE_CONTROL, 8, GET_MAX)
    pan_min, tilt_min = struct.unpack_from("<ii", lo)
    pan_max, tilt_max = struct.unpack_from("<ii", hi)
    return (pan_min, pan_max), (tilt_min, tilt_max)


def get_pan_tilt(dev, unit_id, intf_num):
    """Return (pan, tilt) as integers in device units."""
    raw = ctrl_get(dev, unit_id, intf_num, CT_PAN_TILT_ABSOLUTE_CONTROL, 8)
    pan, tilt = struct.unpack_from("<ii", raw)
    return pan, tilt


def set_pan_tilt(dev, unit_id, intf_num, pan: int, tilt: int):
    """Set absolute pan and tilt in device units."""
    ctrl_set(dev, unit_id, intf_num, CT_PAN_TILT_ABSOLUTE_CONTROL, struct.pack("<ii", pan, tilt))
