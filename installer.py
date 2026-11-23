#!/usr/bin/env python3
"""Cross-platform per-user installer/uninstaller for UHV Sentinel."""
from __future__ import annotations
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

from app_paths import APP_SLUG, PRODUCT_NAME, data_dir, install_dir, runtime_python
from version import __version__

APP_FILES = (
    'uhv_sentinel.py', 'vacuum_extra_devices.py', 'epimax_network.py', 'impac_pyrometer.py', 'tdk_lambda.py', 'tdk_logging.py', 'tdk_panel.py', 'profiles.py',
    'updater.py', 'app_paths.py', 'version.py', 'release_config.json',
    'requirements.txt', 'README.md', 'Unibasel_theme.json',
    'example_theme.json', 'LICENSE', 'CHANGELOG.md', 'installer.py',
    'INSTALL_WINDOWS.bat', 'UNINSTALL_WINDOWS.bat',
    'INSTALL_LINUX_MAC.sh', 'UNINSTALL_LINUX_MAC.sh',
)
APP_DIRS = ('docs', 'scripts')


def source_dir() -> Path:
    return Path(__file__).resolve().parent


def desktop_dir() -> Path:
    """Respect redirected Windows Desktop folders (including OneDrive)."""
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                value, _ = winreg.QueryValueEx(key, 'Desktop')
            return Path(os.path.expandvars(value))
        except Exception:
            pass
    return Path.home() / 'Desktop'


def create_shortcuts(root: Path) -> None:
    python_exe = runtime_python(root, gui=(sys.platform == 'win32'))
    script = root / 'app' / 'uhv_sentinel.py'
    if sys.platform == 'win32':
        # PowerShell COM is a built-in Windows API; no third-party shortcut package.
        desktop = desktop_dir()
        desktop.mkdir(parents=True, exist_ok=True)
        shortcut = desktop / f'{PRODUCT_NAME}.lnk'
        def quote(s: str) -> str:
            return "'" + s.replace("'", "''") + "'"
        shortcut_arguments = quote('"' + str(script) + '"')
        ps = ("$W = New-Object -ComObject WScript.Shell; "
              f"$S = $W.CreateShortcut({quote(str(shortcut))}); "
              f"$S.TargetPath = {quote(str(python_exe))}; "
              f"$S.Arguments = {shortcut_arguments}; "
              f"$S.WorkingDirectory = {quote(str(script.parent))}; "
              f"$S.Description = 'UHV Sentinel vacuum pressure monitor'; $S.Save()")
        subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', ps], check=True)
    elif sys.platform == 'darwin':
        desktop = desktop_dir()
        desktop.mkdir(exist_ok=True)
        launcher = desktop / f'{PRODUCT_NAME}.command'
        launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(str(python_exe)) + ' ' +
                            shlex.quote(str(script)) + '\n', encoding='utf-8')
        launcher.chmod(0o755)
    else:
        application_dir = Path.home() / '.local' / 'share' / 'applications'
        application_dir.mkdir(parents=True, exist_ok=True)
        launch = root / 'launch.sh'
        launch.write_text('#!/bin/sh\nexec ' + shlex.quote(str(python_exe)) + ' ' +
                          shlex.quote(str(script)) + '\n', encoding='utf-8')
        launch.chmod(0o755)
        desktop_file = application_dir / f'{APP_SLUG}.desktop'
        desktop_file.write_text('[Desktop Entry]\nType=Application\n'
                                f'Name={PRODUCT_NAME}\n'
                                'Comment=Laboratory vacuum device monitor\n'
                                f'Exec="{launch}"\nTerminal=false\n'
                                'Icon=utilities-system-monitor\nCategories=Science;Utility;\n', encoding='utf-8')
        desktop_file.chmod(0o755)
        desktop = desktop_dir()
        if desktop.is_dir():
            shortcut = desktop / f'{PRODUCT_NAME}.desktop'
            shutil.copy2(desktop_file, shortcut)
            shortcut.chmod(0o755)


def remove_shortcuts(root: Path) -> None:
    desktop = desktop_dir()
    candidates = [
        desktop / f'{PRODUCT_NAME}.lnk',
        desktop / f'{PRODUCT_NAME}.command',
        desktop / f'{PRODUCT_NAME}.desktop',
        Path.home() / '.local' / 'share' / 'applications' / f'{APP_SLUG}.desktop',
    ]
    for path in candidates:
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            print(f'Could not remove launcher: {path}')


def install(*, launch: bool = True) -> None:
    src = source_dir()
    root = install_dir()
    current = root / 'app'
    stage = root / 'staging_install'
    backup = root / 'previous_app'
    missing = [item for item in APP_FILES if not (src / item).is_file()]
    if missing:
        raise FileNotFoundError(f'Missing source files: {missing}')
    root.mkdir(parents=True, exist_ok=True)
    target_py = runtime_python(root)
    if not target_py.is_file():
        print(f'Creating a private Python environment at {target_py.parent.parent}')
        venv.EnvBuilder(with_pip=True).create(root / 'runtime')
    subprocess.run([str(target_py), '-m', 'pip', 'install', '-r', str(src / 'requirements.txt')], check=True)
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    for item in APP_FILES:
        shutil.copy2(src / item, stage / item)
    for item in APP_DIRS:
        if (src / item).exists():
            shutil.copytree(src / item, stage / item)
    if backup.exists():
        shutil.rmtree(backup)
    if current.exists():
        current.rename(backup)
    try:
        stage.rename(current)
    except Exception:
        if backup.exists():
            backup.rename(current)
        raise
    (root / 'install.json').write_text(json.dumps({"product": PRODUCT_NAME, "version_at_install": __version__}, indent=2), encoding='utf-8')
    create_shortcuts(root)
    print(f'Installed {PRODUCT_NAME} v{__version__} in {root}')
    print(f'Configuration and recorded data remain in {data_dir()}')
    if launch:
        executable = runtime_python(root, gui=(sys.platform == 'win32'))
        subprocess.Popen([str(executable), str(current / 'uhv_sentinel.py')], cwd=str(current),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def uninstall(*, purge_data: bool = False) -> None:
    root = install_dir()
    marker = root / 'install.json'
    if root.exists() and not marker.is_file():
        raise RuntimeError(f'{root} does not look like a UHV Sentinel installation; refusing to remove it')
    remove_shortcuts(root)
    if root.exists():
        shutil.rmtree(root)
    print(f'Removed {PRODUCT_NAME} application and its runtime.')
    if purge_data:
        folder = data_dir()
        if folder.is_dir():
            shutil.rmtree(folder)
        print('Deleted ALL application recordings, settings, and logs (--purge-data).')
    else:
        print(f'Preserved settings and recorded pressure data at: {data_dir()}')
        print('To delete them too, re-run: python installer.py uninstall --purge-data')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='UHV Sentinel installer and uninstaller')
    parser.add_argument('action', choices=('install', 'uninstall'))
    parser.add_argument('--no-launch', action='store_true', help='Do not start the GUI after installing')
    parser.add_argument('--purge-data', action='store_true', help='Uninstall: permanently delete all user recordings/settings')
    args = parser.parse_args(argv)
    try:
        if args.action == 'install':
            if args.purge_data:
                parser.error('--purge-data may be used only for uninstall')
            install(launch=not args.no_launch)
        else:
            uninstall(purge_data=args.purge_data)
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
