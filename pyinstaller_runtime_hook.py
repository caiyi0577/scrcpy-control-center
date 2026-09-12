import os
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    internal_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    _dll_directory_handles = []
    dll_dirs = [
        internal_dir,
        internal_dir / "PySide6",
        internal_dir / "shiboken6",
        internal_dir / "pywin32_system32",
    ]
    for dll_dir in dll_dirs:
        if dll_dir.is_dir():
            try:
                _dll_directory_handles.append(os.add_dll_directory(str(dll_dir)))
            except (AttributeError, OSError):
                pass
    os.environ["PATH"] = os.pathsep.join(
        [str(path) for path in dll_dirs if path.is_dir()] + [os.environ.get("PATH", "")]
    )
