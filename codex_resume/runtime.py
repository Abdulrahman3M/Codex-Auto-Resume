"""Operating-system paths and process helpers for the Codex desktop app."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


WINDOWS_PIPE = r'\\.\pipe\codex-ipc'
CODEX_EXE = 'codex.exe'
WINDOWS_ENGINE_DIR = Path('OpenAI', 'Codex', 'bin')
CODEX_PACKAGE_FAMILY = 'openai.codex_2p2nqsd0c76g0'
PYTHON_IMAGES = frozenset({'python.exe', 'pythonw.exe', 'py.exe', 'pyw.exe'})


def platform_name():
    return 'windows' if sys.platform == 'win32' else 'macos' if sys.platform == 'darwin' else sys.platform


def _windows_local_app_data():
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))


def default_app_path(system=None):
    system = system or platform_name()
    if system == 'windows':
        # The Store/MSIX app relocates its bundled CLI here so it remains
        # executable outside the protected WindowsApps directory.
        return _windows_local_app_data() / WINDOWS_ENGINE_DIR / CODEX_EXE
    return Path('/Applications/ChatGPT.app')


def codex_binary(app_path, system=None):
    """Resolve the App-bundled CLI without searching arbitrary PATH entries."""
    system = system or platform_name()
    path = Path(app_path)
    if system == 'windows':
        candidates = []
        if path.name.lower() == 'codex.exe':
            candidates.append(path)
        elif path.suffix.lower() == '.exe':
            candidates.extend((path.parent / 'resources' / 'codex.exe', path.parent / 'codex.exe'))
        else:
            candidates.extend((path / 'codex.exe', path / 'resources' / 'codex.exe',
                               path / 'OpenAI' / 'Codex' / 'bin' / 'codex.exe'))
        return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    resources = path / 'Contents' / 'Resources'
    # Newer macOS builds package the CLI as a versioned app resource bundle.
    candidates = (resources / 'codex-cli' / 'bin' / 'codex', resources / 'codex')
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


def discover_app_path(system=None):
    """Resolve the Windows engine path, preferring the copy the App is running.

    An explicitly supplied --app path never reaches this function. A running
    engine only counts once its process tree can be attributed to the desktop
    App, so the watcher's own ``app-server`` children and stale query processes
    are never mistaken for the current engine. Without that evidence only the
    documented install directory is searched: the legacy ``bin\\codex.exe``
    file and one level of hash subdirectories, both after links and junctions
    are resolved.
    """
    system = system or platform_name()
    if system != 'windows':
        return default_app_path(system)
    root = _resolved(_windows_local_app_data()) / WINDOWS_ENGINE_DIR
    default = root / CODEX_EXE
    if _resolved(root) != root:
        raise RuntimeError(f'The engine installation directory is redirected by a link: {root}\n'
                           'Remove the link or junction, or use --app to specify the codex.exe path explicitly')
    attributed, unattributed = _app_engine_paths()
    running = _trusted_binaries(attributed, root)
    if len(running) > 1:
        raise RuntimeError(_ambiguous_message(
            'Multiple running Codex engines were detected', running, 'Close the extra Codex App instances and try again'))
    if running:
        return running[0]
    installed = _trusted_binaries(_install_candidates(root), root)
    if len(installed) > 1:
        raise RuntimeError(_ambiguous_message(
            'Multiple codex.exe files were found in the installation directory', installed, _install_hint(unattributed, root)))
    if installed:
        return installed[0]
    # Nothing usable inside the install root. Keep the documented default so a
    # missing App still reports a real location, even if nothing is installed.
    if not _resolved(default).is_relative_to(root):
        raise RuntimeError(f'The default engine path points outside the installation directory: {default}\n'
                           'Remove the link or junction, or use --app to specify the codex.exe path explicitly')
    if default.is_symlink():
        raise RuntimeError(f'The default engine link does not point to a usable codex.exe: {default}')
    return default


def _resolved(path):
    """Absolute path with every link, junction and drive substitution resolved."""
    return Path(os.path.realpath(os.fspath(path)))


def _trusted_binaries(paths, root):
    """Return the checked targets themselves, without following aliases again."""
    candidates, seen = [], set()
    for value in paths:
        path = Path(value)
        if path.name.lower() != CODEX_EXE:
            continue
        target = _resolved(path)
        if (target in seen or not target.is_relative_to(root)
                or target.name.lower() != CODEX_EXE or not target.is_file()):
            continue
        seen.add(target)
        candidates.append(target)
    return candidates


def _install_candidates(root):
    """The bounded candidate list: the legacy file plus one directory level."""
    legacy = root / CODEX_EXE
    try:
        children = sorted(root.iterdir(), key=lambda item: os.path.normcase(item.name))
    except OSError:
        return [legacy]
    return [legacy, *[child / CODEX_EXE for child in children]]


def _ambiguous_message(summary, candidates, hint):
    listing = '\n'.join(f'  {candidate}' for candidate in candidates)
    return f'{summary}; unable to choose reliably:\n{listing}\n{hint}; or use --app to specify the codex.exe path explicitly'


def _install_hint(unattributed, root):
    """Explain how to disambiguate, naming unattributed engines when present."""
    if _trusted_binaries(unattributed, root):
        return 'A running codex.exe was found, but its App ownership could not be verified. Open the target task from the App and try again'
    return 'Start the Codex App so its running engine can be identified'


def _app_engine_paths():
    """Running codex.exe images split by whether the App owns them.

    Look for the App's Windows package identity in the parent chain. Keep
    watcher children and processes without a verifiable chain separate so they
    can be reported when discovery is ambiguous.
    """
    table = _windows_process_table()
    if not table:
        return [], []
    parents = {pid: (parent, name) for pid, parent, name in table}
    attributed, unattributed = [], []
    for pid, _parent, name in table:
        if name.lower() != CODEX_EXE:
            continue
        path = _windows_image_path(pid)
        if path:
            (attributed if _app_hosted(pid, parents) else unattributed).append(path)
    return attributed, unattributed


def _app_hosted(pid, parents):
    """True when the parent chain of pid reaches an App package process."""
    seen = set()
    parent = parents.get(pid, (None, ''))[0]
    while parent and parent not in seen:
        seen.add(parent)
        entry = parents.get(parent)
        if entry is None:
            return False        # ancestor already gone: ownership is unknown
        if (entry[1] or '').lower() in PYTHON_IMAGES:
            return False        # our own watcher or CLI started it
        family = _windows_package_family(parent)
        if family and family.lower() == CODEX_PACKAGE_FAMILY:
            return True
        parent = entry[0]
    return False


def _windows_process_table():
    """(pid, parent pid, image name) for every process; empty when unreadable.

    Best-effort: an unreadable process list yields no entries, which lets the
    caller fall back to searching the install directory.
    """
    import ctypes
    kernel32, entry_type = _process_api()
    if kernel32 is None:
        return []
    TH32CS_SNAPPROCESS = 0x00000002
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, ctypes.c_void_p(-1).value):
        return []
    entries = []
    try:
        entry = entry_type()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            entries.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return entries


def _windows_image_path(pid):
    """Full executable path of one process, or None when it cannot be read."""
    import ctypes
    from ctypes import wintypes
    kernel32, _entry_type = _process_api()
    if kernel32 is None:
        return None
    process_query_limited_information = 0x1000
    handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def _windows_package_family(pid):
    """Read the MSIX package identity assigned by Windows to this process."""
    import ctypes
    from ctypes import wintypes
    kernel32, _entry_type = _process_api()
    if kernel32 is None:
        return None
    process_query_limited_information = 0x1000
    handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
    if not handle:
        return None
    try:
        # PACKAGE_FAMILY_NAME_MAX_LENGTH is 64, plus the terminating NUL.
        buffer = ctypes.create_unicode_buffer(65)
        size = wintypes.UINT(len(buffer))
        if kernel32.GetPackageFamilyName(handle, ctypes.byref(size), buffer) == 0:
            return buffer.value
        return None
    finally:
        kernel32.CloseHandle(handle)


def _process_api():
    """Configured kernel32 and PROCESSENTRY32W, or (None, None) off Windows."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ('dwSize', wintypes.DWORD),
            ('cntUsage', wintypes.DWORD),
            ('th32ProcessID', wintypes.DWORD),
            ('th32DefaultHeapID', ctypes.c_size_t),
            ('th32ModuleID', wintypes.DWORD),
            ('cntThreads', wintypes.DWORD),
            ('th32ParentProcessID', wintypes.DWORD),
            ('pcPriClassBase', wintypes.LONG),
            ('dwFlags', wintypes.DWORD),
            ('szExeFile', wintypes.WCHAR * 260),
        ]

    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = kernel32.Process32FirstW.argtypes
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.GetPackageFamilyName.argtypes = (
            wintypes.HANDLE, ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR)
        kernel32.GetPackageFamilyName.restype = wintypes.LONG
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
    except (AttributeError, OSError):
        return None, None
    return kernel32, PROCESSENTRY32W


def windows_package_version():
    """Read the current user's Store/MSIX package version without changing it."""
    shell = shutil.which('powershell.exe') or shutil.which('pwsh.exe')
    if not shell:
        raise RuntimeError('PowerShell was not found; the Windows Codex App version cannot be verified')
    script = ("$p=Get-AppxPackage -Name OpenAI.Codex | Sort-Object Version -Descending | "
              "Select-Object -First 1; if ($null -eq $p) { exit 3 }; $p.Version.ToString()")
    result = subprocess.run([shell, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, timeout=10)
    value = result.stdout.strip()
    if result.returncode or not value:
        raise RuntimeError('Could not find the Windows Codex App installed for the current user')
    return value


def process_exists(pid):
    if platform_name() != 'windows':
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
    import ctypes
    query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(query_limited_information, False, int(pid))
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True
