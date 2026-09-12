"""Windows audio render-device discovery and per-process audio helpers.

scrcpy uses SDL for computer-side audio playback and does not expose a
command-line option for selecting an output endpoint.  Windows keeps a
per-application endpoint preference, so this module applies the selected
endpoint to the scrcpy process without changing the global default device.

The policy API used for per-application routing is an undocumented Windows
COM interface.  All calls are guarded and fail softly so the launcher still
works normally on systems where the interface is unavailable.

The phone-only mode uses scrcpy's audio duplication (which keeps playback on
the device) and mutes only the scrcpy audio session on Windows.  This is
necessary because scrcpy rejects --audio-dup together with
--no-audio-playback.
"""

from __future__ import annotations

import ctypes
import uuid
from ctypes import wintypes
from dataclasses import dataclass


_S_OK = 0
_S_FALSE = 1
_RPC_E_CHANGED_MODE = -2147417850
_CLSCTX_INPROC_SERVER = 1
_CLSCTX_ALL = 23
_STGM_READ = 0
_DEVICE_STATE_ACTIVE = 1
_E_RENDER = 0
_E_CONSOLE = 0
_E_MULTIMEDIA = 1
_E_COMMUNICATIONS = 2
_VT_LPWSTR = 31
_VT_BSTR = 8

_CLSID_MM_DEVICE_ENUMERATOR = "BCDE0395-E52F-467C-8E3D-C4579291692E"
_IID_MM_DEVICE_ENUMERATOR = "A95664D2-9614-4F35-A746-DE8DB63617E6"
_IID_AUDIO_POLICY_CONFIG_21H2 = "AB3D4648-E242-459F-B02F-541C70306324"
_IID_AUDIO_POLICY_CONFIG_DOWNLEVEL = "2A59116D-6C4F-45E0-A74F-707E3FEF9258"
_IID_AUDIO_SESSION_MANAGER2 = "77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"
_IID_AUDIO_SESSION_CONTROL2 = "BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D"
_IID_SIMPLE_AUDIO_VOLUME = "87CE5498-68D6-44E5-9215-6DA47EF883D8"
_AUDIO_POLICY_RUNTIME_CLASS = "Windows.Media.Internal.AudioPolicyConfig"
_PKEY_DEVICE_FRIENDLY_NAME = "A45C254E-DF1C-4EFD-8020-67D146A850E0"
_AUDIO_RENDER_INTERFACE_SUFFIX = "#{E6327CAD-DCEC-4949-AE8A-991E976A79D2}"
_MMDEVAPI_PREFIX = r"\\?\SWD#MMDEVAPI#"


class _GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    )


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = (("fmtid", _GUID), ("pid", wintypes.DWORD))


class _PROPVARIANT_DATA(ctypes.Union):
    _fields_ = (
        ("ptr", ctypes.c_void_p),
        ("pwszVal", ctypes.c_wchar_p),
        ("bstrVal", ctypes.c_void_p),
    )


class _PROPVARIANT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = (
        ("vt", wintypes.USHORT),
        ("wReserved1", wintypes.USHORT),
        ("wReserved2", wintypes.USHORT),
        ("wReserved3", wintypes.USHORT),
        ("data", _PROPVARIANT_DATA),
    )


@dataclass(frozen=True)
class AudioOutputDevice:
    """A Windows playback endpoint shown in the launcher."""

    name: str
    endpoint_id: str
    is_default: bool = False


def _guid(value: str) -> _GUID:
    return _GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


def _failed(hr: int) -> bool:
    return ctypes.c_long(hr).value < 0


def _com_method(interface: ctypes.c_void_p, index: int, restype, *argtypes):
    vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    function = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])
    return lambda *args: function(interface, *args)


def _release(interface: ctypes.c_void_p | None) -> None:
    if interface:
        _com_method(interface, 2, ctypes.c_long)()


def _co_initialize() -> bool:
    if not hasattr(ctypes, "windll"):
        return False
    hr = ctypes.windll.ole32.CoInitializeEx(None, 2)
    return hr in (_S_OK, _S_FALSE)


def _co_uninitialize(initialized: bool) -> None:
    if initialized:
        ctypes.windll.ole32.CoUninitialize()


def _create_mm_device_enumerator() -> ctypes.c_void_p:
    ole32 = ctypes.windll.ole32
    ole32.CoCreateInstance.argtypes = (
        ctypes.POINTER(_GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    )
    ole32.CoCreateInstance.restype = ctypes.c_long
    result = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        ctypes.byref(_guid(_CLSID_MM_DEVICE_ENUMERATOR)),
        None,
        _CLSCTX_INPROC_SERVER,
        ctypes.byref(_guid(_IID_MM_DEVICE_ENUMERATOR)),
        ctypes.byref(result),
    )
    if _failed(hr):
        raise OSError(f"CoCreateInstance failed: 0x{ctypes.c_ulong(hr).value:08X}")
    return result


def _get_endpoint_id(device: ctypes.c_void_p) -> str:
    endpoint_id = ctypes.c_void_p()
    hr = _com_method(
        device,
        5,
        ctypes.c_long,
        ctypes.POINTER(ctypes.c_void_p),
    )(ctypes.byref(endpoint_id))
    if _failed(hr) or not endpoint_id.value:
        raise OSError("IMMDevice.GetId failed")
    value = ctypes.wstring_at(endpoint_id.value)
    ctypes.windll.ole32.CoTaskMemFree(endpoint_id)
    return value


def _get_friendly_name(device: ctypes.c_void_p) -> str:
    store = ctypes.c_void_p()
    hr = _com_method(
        device,
        4,
        ctypes.c_long,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    )(_STGM_READ, ctypes.byref(store))
    if _failed(hr):
        return "未命名音频设备"

    propvariant = _PROPVARIANT()
    property_key = _PROPERTYKEY(_guid(_PKEY_DEVICE_FRIENDLY_NAME), 14)
    try:
        hr = _com_method(
            store,
            5,
            ctypes.c_long,
            ctypes.POINTER(_PROPERTYKEY),
            ctypes.POINTER(_PROPVARIANT),
        )(ctypes.byref(property_key), ctypes.byref(propvariant))
        if _failed(hr):
            return "未命名音频设备"
        if propvariant.vt == _VT_LPWSTR and propvariant.pwszVal:
            return propvariant.pwszVal
        if propvariant.vt == _VT_BSTR and propvariant.bstrVal:
            return ctypes.wstring_at(propvariant.bstrVal)
        return "未命名音频设备"
    finally:
        try:
            ctypes.windll.oleaut32.PropVariantClear(ctypes.byref(propvariant))
        except (AttributeError, OSError):
            pass
        _release(store)


def list_audio_outputs() -> list[AudioOutputDevice]:
    """Return active Windows playback devices, default device first."""

    if not hasattr(ctypes, "windll"):
        return []

    initialized = _co_initialize()
    if not initialized:
        return []

    enumerator = None
    collection = None
    default_device = None
    try:
        enumerator = _create_mm_device_enumerator()
        hr = _com_method(
            enumerator,
            4,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        )(_E_RENDER, _E_MULTIMEDIA, ctypes.byref(default_device := ctypes.c_void_p()))
        default_id = _get_endpoint_id(default_device) if not _failed(hr) else ""

        hr = _com_method(
            enumerator,
            3,
            ctypes.c_long,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        )(_E_RENDER, _DEVICE_STATE_ACTIVE, ctypes.byref(collection := ctypes.c_void_p()))
        if _failed(hr):
            return []

        count = wintypes.UINT()
        hr = _com_method(collection, 3, ctypes.c_long, ctypes.POINTER(wintypes.UINT))(
            ctypes.byref(count)
        )
        if _failed(hr):
            return []

        devices: list[AudioOutputDevice] = []
        for index in range(count.value):
            device = ctypes.c_void_p()
            hr = _com_method(
                collection,
                4,
                ctypes.c_long,
                wintypes.UINT,
                ctypes.POINTER(ctypes.c_void_p),
            )(index, ctypes.byref(device))
            if _failed(hr) or not device:
                continue
            try:
                endpoint_id = _get_endpoint_id(device)
                devices.append(
                    AudioOutputDevice(
                        name=_get_friendly_name(device),
                        endpoint_id=endpoint_id,
                        is_default=endpoint_id == default_id,
                    )
                )
            finally:
                _release(device)

        devices.sort(key=lambda item: (not item.is_default, item.name.casefold()))
        return devices
    except (OSError, AttributeError, ctypes.ArgumentError, ValueError):
        return []
    finally:
        _release(collection)
        _release(default_device)
        _release(enumerator)
        _co_uninitialize(initialized)


def _unpack_endpoint_id(endpoint_id: str) -> str:
    """Convert an MMDevice endpoint id to the id expected by the policy API."""

    value = endpoint_id
    if value.startswith(_MMDEVAPI_PREFIX):
        value = value[len(_MMDEVAPI_PREFIX) :]
    if value.casefold().endswith(_AUDIO_RENDER_INTERFACE_SUFFIX.casefold()):
        value = value[: -len(_AUDIO_RENDER_INTERFACE_SUFFIX)]
    return value


def _create_policy_factory() -> ctypes.c_void_p:
    combase = ctypes.windll.combase
    combase.WindowsCreateString.argtypes = (
        ctypes.c_wchar_p,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
    )
    combase.WindowsCreateString.restype = ctypes.c_long
    combase.WindowsDeleteString.argtypes = (ctypes.c_void_p,)
    combase.WindowsDeleteString.restype = ctypes.c_long
    combase.RoGetActivationFactory.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    )
    combase.RoGetActivationFactory.restype = ctypes.c_long

    class_id = ctypes.c_void_p()
    hr = combase.WindowsCreateString(
        _AUDIO_POLICY_RUNTIME_CLASS,
        len(_AUDIO_POLICY_RUNTIME_CLASS),
        ctypes.byref(class_id),
    )
    if _failed(hr):
        raise OSError("WindowsCreateString failed")
    try:
        for interface_id in (_IID_AUDIO_POLICY_CONFIG_21H2, _IID_AUDIO_POLICY_CONFIG_DOWNLEVEL):
            factory = ctypes.c_void_p()
            hr = combase.RoGetActivationFactory(
                class_id,
                ctypes.byref(_guid(interface_id)),
                ctypes.byref(factory),
            )
            if not _failed(hr) and factory:
                return factory
        raise OSError("RoGetActivationFactory failed")
    finally:
        combase.WindowsDeleteString(class_id)


def _query_interface(interface: ctypes.c_void_p, interface_id: str) -> ctypes.c_void_p | None:
    """Query a COM interface by IID and return a retained pointer."""

    result = ctypes.c_void_p()
    hr = _com_method(
        interface,
        0,
        ctypes.c_long,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    )(ctypes.byref(_guid(interface_id)), ctypes.byref(result))
    if _failed(hr) or not result.value:
        return None
    return result


def _create_audio_session_manager() -> ctypes.c_void_p:
    """Activate the audio session manager for the default render device."""

    enumerator = _create_mm_device_enumerator()
    device = ctypes.c_void_p()
    try:
        hr = _com_method(
            enumerator,
            4,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        )(_E_RENDER, _E_MULTIMEDIA, ctypes.byref(device))
        if _failed(hr) or not device.value:
            raise OSError("IMMDeviceEnumerator.GetDefaultAudioEndpoint failed")

        manager = ctypes.c_void_p()
        hr = _com_method(
            device,
            3,
            ctypes.c_long,
            ctypes.POINTER(_GUID),
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )(
            ctypes.byref(_guid(_IID_AUDIO_SESSION_MANAGER2)),
            _CLSCTX_ALL,
            None,
            ctypes.byref(manager),
        )
        if _failed(hr) or not manager.value:
            raise OSError("IMMDevice.Activate(IAudioSessionManager2) failed")
        return manager
    finally:
        _release(device)
        _release(enumerator)


def set_process_audio_mute(process_id: int, muted: bool) -> tuple[bool, str]:
    """Mute or unmute all Windows audio sessions owned by a process."""

    if not process_id or not hasattr(ctypes, "windll"):
        return False, "缺少有效的进程信息"

    initialized = _co_initialize()
    if not initialized:
        return False, "Windows 音频 COM 初始化失败"

    manager = None
    session_enumerator = None
    try:
        manager = _create_audio_session_manager()
        session_enumerator = ctypes.c_void_p()
        hr = _com_method(
            manager,
            5,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_void_p),
        )(ctypes.byref(session_enumerator))
        if _failed(hr) or not session_enumerator.value:
            return False, "无法枚举 Windows 音频会话"

        count = wintypes.INT()
        hr = _com_method(
            session_enumerator,
            3,
            ctypes.c_long,
            ctypes.POINTER(wintypes.INT),
        )(ctypes.byref(count))
        if _failed(hr):
            return False, "无法读取 Windows 音频会话"

        matched = 0
        first_error = ""
        for index in range(max(0, count.value)):
            session = ctypes.c_void_p()
            hr = _com_method(
                session_enumerator,
                4,
                ctypes.c_long,
                wintypes.INT,
                ctypes.POINTER(ctypes.c_void_p),
            )(index, ctypes.byref(session))
            if _failed(hr) or not session.value:
                continue

            control2 = None
            volume = None
            try:
                control2 = _query_interface(session, _IID_AUDIO_SESSION_CONTROL2)
                if not control2:
                    continue
                session_pid = wintypes.DWORD()
                hr = _com_method(
                    control2,
                    14,
                    ctypes.c_long,
                    ctypes.POINTER(wintypes.DWORD),
                )(ctypes.byref(session_pid))
                if _failed(hr) or session_pid.value != int(process_id):
                    continue

                matched += 1
                volume = _query_interface(session, _IID_SIMPLE_AUDIO_VOLUME)
                if not volume:
                    first_error = first_error or "找不到该进程的音量会话"
                    continue
                hr = _com_method(
                    volume,
                    5,
                    ctypes.c_long,
                    wintypes.BOOL,
                    ctypes.c_void_p,
                )(wintypes.BOOL(bool(muted)), None)
                if _failed(hr):
                    first_error = first_error or (
                        f"设置进程音量失败（0x{ctypes.c_ulong(hr).value:08X}）"
                    )
            finally:
                _release(volume)
                _release(control2)
                _release(session)

        if matched == 0:
            return False, "暂未找到该进程的 Windows 音频会话"
        if first_error:
            return False, first_error
        return True, ""
    except (OSError, AttributeError, ctypes.ArgumentError, ValueError) as exc:
        return False, str(exc)
    finally:
        _release(session_enumerator)
        _release(manager)
        _co_uninitialize(initialized)


def set_process_audio_output(process_id: int, endpoint_id: str) -> tuple[bool, str]:
    """Route a process to an output endpoint using Windows app preferences."""

    if not process_id or not endpoint_id or not hasattr(ctypes, "windll"):
        return False, "缺少有效的进程或音频设备信息"

    initialized = _co_initialize()
    if not initialized:
        return False, "Windows 音频 COM 初始化失败"

    factory = None
    device_hstring = ctypes.c_void_p()
    try:
        factory = _create_policy_factory()
        combase = ctypes.windll.combase
        combase.WindowsCreateString(
            _unpack_endpoint_id(endpoint_id),
            len(_unpack_endpoint_id(endpoint_id)),
            ctypes.byref(device_hstring),
        )
        if not device_hstring:
            return False, "无法创建 Windows 音频设备标识"

        # SDL normally opens the multimedia role. Setting all render roles
        # makes the preference consistent with Windows' app-volume settings.
        for role in (_E_CONSOLE, _E_MULTIMEDIA, _E_COMMUNICATIONS):
            hr = _com_method(
                factory,
                24,
                ctypes.c_long,
                wintypes.DWORD,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_void_p,
            )(process_id, _E_RENDER, role, device_hstring)
            if _failed(hr):
                return False, f"设置 Windows 音频出口失败（0x{ctypes.c_ulong(hr).value:08X}）"
        return True, ""
    except (OSError, AttributeError, ctypes.ArgumentError, ValueError) as exc:
        return False, str(exc)
    finally:
        if device_hstring:
            ctypes.windll.combase.WindowsDeleteString(device_hstring)
        _release(factory)
        _co_uninitialize(initialized)
