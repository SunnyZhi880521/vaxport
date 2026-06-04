# PyInstaller spec for vaxport API server (Tauri sidecar)
# 用法: pyinstaller vaxport.spec

# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

block_cipher = None

# 项目根路径
BASE = Path(SPECPATH)

a = Analysis(
    [str(BASE / 'src' / 'vaxport' / 'api_server.py')],
    pathex=[str(BASE / 'src')],
    binaries=[],
    datas=[],
    hiddenimports=[
        # psycopg2
        'psycopg2',
        # matplotlib
        'matplotlib',
        'matplotlib.backends.backend_agg',
        # textual
        'textual',
        'textual.widgets',
        'textual.app',
        # rich
        'rich',
        'rich.markdown',
        'rich.syntax',
        'rich.table',
        # jinja2
        'jinja2',
        'jinja2.ext',
        # openai / dashscope
        'openai',
        'dashscope',
        # tiktoken
        'tiktoken',
        'tiktoken_ext',
        'tiktoken_ext.openai_public',
        # sqlparse
        'sqlparse',
        # yaml
        'yaml',
        # 其他
        'pydantic',
        'pydantic_core',
        'httpx',
        'certifi',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'wx',
        'IPython',
        'jupyter',
        'notebook',
        'pytest',
        'setuptools',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='vaxport-api',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(BASE / 'src' / 'vaxport' / 'icon.png') if (BASE / 'src' / 'vaxport' / 'icon.png').exists() else None,
)