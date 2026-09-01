"""Fail-closed test-process guard for Production mutable project data."""

from __future__ import annotations

import atexit
import builtins
import io
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PROTECTED_DIRS = tuple(
    (_PROJECT_ROOT / name).resolve()
    for name in (
        "runtime",
        "stocks",
        "routines",
        "routine_instances",
        "groups",
        "archived_stocks",
        "logs",
        "reports",
    )
)
_PROTECTED_FILES = {(_PROJECT_ROOT / "PROJECT_CHANGELOG.txt").resolve()}
_TEMP_ROOT = TemporaryDirectory(prefix="kiwoom_auto_tests_")
atexit.register(_TEMP_ROOT.cleanup)
_INSTALLED = False


def _resolved_path(value) -> Path | None:
    if isinstance(value, int):
        return None
    try:
        return Path(value).resolve(strict=False)
    except (OSError, TypeError, ValueError):
        return None


def _is_production_mutable_path(value) -> bool:
    path = _resolved_path(value)
    if path is None:
        return False
    if path in _PROTECTED_FILES:
        return True
    for directory in _PROTECTED_DIRS:
        try:
            path.relative_to(directory)
        except ValueError:
            continue
        return True
    return False


def _deny_production_write(value, operation: str) -> None:
    if _is_production_mutable_path(value):
        raise AssertionError(
            f"test attempted Production mutable {operation}: "
            f"{_resolved_path(value)}"
        )


_ORIGINAL_BUILTIN_OPEN = builtins.open
_ORIGINAL_IO_OPEN = io.open
_ORIGINAL_PATH_OPEN = Path.open
_ORIGINAL_PATH_WRITE_TEXT = Path.write_text
_ORIGINAL_PATH_WRITE_BYTES = Path.write_bytes
_ORIGINAL_PATH_MKDIR = Path.mkdir
_ORIGINAL_PATH_TOUCH = Path.touch
_ORIGINAL_PATH_UNLINK = Path.unlink
_ORIGINAL_PATH_RMDIR = Path.rmdir
_ORIGINAL_PATH_RENAME = Path.rename
_ORIGINAL_PATH_REPLACE = Path.replace
_ORIGINAL_OS_OPEN = os.open
_ORIGINAL_OS_MKDIR = os.mkdir
_ORIGINAL_OS_MAKEDIRS = os.makedirs
_ORIGINAL_OS_RMDIR = os.rmdir
_ORIGINAL_OS_REPLACE = os.replace
_ORIGINAL_OS_RENAME = os.rename
_ORIGINAL_OS_REMOVE = os.remove
_ORIGINAL_OS_UNLINK = os.unlink
_ORIGINAL_SHUTIL_RMTREE = shutil.rmtree


def _writing_mode(mode: object) -> bool:
    text = str(mode or "r")
    return any(marker in text for marker in ("w", "a", "x", "+"))


def _guarded_builtin_open(file, mode="r", *args, **kwargs):
    if _writing_mode(mode):
        _deny_production_write(file, "open")
    return _ORIGINAL_BUILTIN_OPEN(file, mode, *args, **kwargs)


def _guarded_io_open(file, mode="r", *args, **kwargs):
    if _writing_mode(mode):
        _deny_production_write(file, "open")
    return _ORIGINAL_IO_OPEN(file, mode, *args, **kwargs)


def _guarded_path_open(self, mode="r", *args, **kwargs):
    if _writing_mode(mode):
        _deny_production_write(self, "open")
    return _ORIGINAL_PATH_OPEN(self, mode, *args, **kwargs)


def _guarded_write_text(self, *args, **kwargs):
    _deny_production_write(self, "write_text")
    return _ORIGINAL_PATH_WRITE_TEXT(self, *args, **kwargs)


def _guarded_write_bytes(self, *args, **kwargs):
    _deny_production_write(self, "write_bytes")
    return _ORIGINAL_PATH_WRITE_BYTES(self, *args, **kwargs)


def _guarded_mkdir(self, *args, **kwargs):
    _deny_production_write(self, "mkdir")
    return _ORIGINAL_PATH_MKDIR(self, *args, **kwargs)


def _guarded_touch(self, *args, **kwargs):
    _deny_production_write(self, "touch")
    return _ORIGINAL_PATH_TOUCH(self, *args, **kwargs)


def _guarded_unlink(self, *args, **kwargs):
    _deny_production_write(self, "unlink")
    return _ORIGINAL_PATH_UNLINK(self, *args, **kwargs)


def _guarded_rmdir(self, *args, **kwargs):
    _deny_production_write(self, "rmdir")
    return _ORIGINAL_PATH_RMDIR(self, *args, **kwargs)


def _guarded_rename(self, target, *args, **kwargs):
    _deny_production_write(self, "rename source")
    _deny_production_write(target, "rename target")
    return _ORIGINAL_PATH_RENAME(self, target, *args, **kwargs)


def _guarded_replace(self, target, *args, **kwargs):
    _deny_production_write(self, "replace source")
    _deny_production_write(target, "replace target")
    return _ORIGINAL_PATH_REPLACE(self, target, *args, **kwargs)


def _guarded_os_replace(source, target, *args, **kwargs):
    _deny_production_write(source, "replace source")
    _deny_production_write(target, "replace target")
    return _ORIGINAL_OS_REPLACE(source, target, *args, **kwargs)


def _guarded_os_open(path, flags, *args, **kwargs):
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
    if int(flags) & write_flags:
        _deny_production_write(path, "os.open")
    return _ORIGINAL_OS_OPEN(path, flags, *args, **kwargs)


def _guarded_os_mkdir(path, *args, **kwargs):
    _deny_production_write(path, "mkdir")
    return _ORIGINAL_OS_MKDIR(path, *args, **kwargs)


def _guarded_os_makedirs(name, *args, **kwargs):
    _deny_production_write(name, "makedirs")
    return _ORIGINAL_OS_MAKEDIRS(name, *args, **kwargs)


def _guarded_os_rmdir(path, *args, **kwargs):
    _deny_production_write(path, "rmdir")
    return _ORIGINAL_OS_RMDIR(path, *args, **kwargs)


def _guarded_os_rename(source, target, *args, **kwargs):
    _deny_production_write(source, "rename source")
    _deny_production_write(target, "rename target")
    return _ORIGINAL_OS_RENAME(source, target, *args, **kwargs)


def _guarded_os_remove(path, *args, **kwargs):
    _deny_production_write(path, "remove")
    return _ORIGINAL_OS_REMOVE(path, *args, **kwargs)


def _guarded_os_unlink(path, *args, **kwargs):
    _deny_production_write(path, "unlink")
    return _ORIGINAL_OS_UNLINK(path, *args, **kwargs)


def _guarded_shutil_rmtree(path, *args, **kwargs):
    _deny_production_write(path, "rmtree")
    return _ORIGINAL_SHUTIL_RMTREE(path, *args, **kwargs)


def installed() -> bool:
    return _INSTALLED


def install() -> None:
    """Install the guard once, regardless of unittest import style."""

    global _INSTALLED
    if _INSTALLED:
        return
    builtins.open = _guarded_builtin_open
    io.open = _guarded_io_open
    Path.open = _guarded_path_open
    Path.write_text = _guarded_write_text
    Path.write_bytes = _guarded_write_bytes
    Path.mkdir = _guarded_mkdir
    Path.touch = _guarded_touch
    Path.unlink = _guarded_unlink
    Path.rmdir = _guarded_rmdir
    Path.rename = _guarded_rename
    Path.replace = _guarded_replace
    os.open = _guarded_os_open
    os.mkdir = _guarded_os_mkdir
    os.makedirs = _guarded_os_makedirs
    os.rmdir = _guarded_os_rmdir
    os.replace = _guarded_os_replace
    os.rename = _guarded_os_rename
    os.remove = _guarded_os_remove
    os.unlink = _guarded_os_unlink
    shutil.rmtree = _guarded_shutil_rmtree

    from event_journal_writer import EventJournalWriter
    import event_journal_production

    event_journal_production._WRITER = EventJournalWriter(
        Path(_TEMP_ROOT.name) / "runtime" / "event_journal"
    )
    _INSTALLED = True
