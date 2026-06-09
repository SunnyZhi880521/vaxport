# PyInstaller spec for vaxport API server (Tauri sidecar)
# 用法: pyinstaller vaxport.spec

# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path
from datetime import datetime

block_cipher = None

# 项目根路径
BASE = Path(SPECPATH)

# 注入构建时间戳到 server.py
SERVER_PY = BASE / 'src' / 'vaxport' / 'api' / 'server.py'
BUILD_TS = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
server_content = SERVER_PY.read_text(encoding='utf-8')
server_content = server_content.replace('BUILD_VERSION = "dev"', f'BUILD_VERSION = "{BUILD_TS}"')
SERVER_PY.write_text(server_content, encoding='utf-8')
print(f"[BUILD] Injected version: {BUILD_TS}")

a = Analysis(
    [str(BASE / 'src' / 'vaxport' / 'api_server.py')],
    pathex=[str(BASE / 'src')],
    binaries=[],
    datas=[
        ('src/vaxport/tui/style.tcss', 'vaxport/tui/'),
        ('src/vaxport/skills', 'vaxport/skills'),
    ],
    hiddenimports=[
        # psycopg2
        'psycopg2', 'psycopg2.extras', 'psycopg2.pool', 'psycopg2.sql',
        # pgvector (vector extension support)
        'pgvector',
        # matplotlib
        'matplotlib', 'matplotlib.backends.backend_agg',
        'matplotlib.font_manager', 'matplotlib.pyplot', 'matplotlib.ticker',
        # textual
        'textual', 'textual.widgets', 'textual.app',
        'textual._xterm_parser', 'textual.binding', 'textual.command',
        'textual.containers', 'textual.drivers.linux_driver',
        'textual.events', 'textual.keys', 'textual.message',
        'textual.screen', 'textual.widgets._option_list',
        # rich（重点！unicode_data 整包 + 所有版本文件）
        'rich', 'rich.box', 'rich.console', 'rich.markdown',
        'rich.panel', 'rich.rule', 'rich.syntax', 'rich.table', 'rich.text',
        'rich._unicode_data',
        # rich 动态加载的 unicode 版本数据文件
        'rich._unicode_data._versions',
        'rich._unicode_data.unicode17-0-0',  # Python 3.13
        'rich._unicode_data.unicode16-0-0',  # Python 3.12
        'rich._unicode_data.unicode15-1-0',  # Python 3.11
        'rich._unicode_data.unicode15-0-0',
        'rich._unicode_data.unicode14-0-0',
        'rich._unicode_data.unicode13-0-0',
        # jinja2
        'jinja2', 'jinja2.ext',
        # openai / dashscope
        'openai', 'dashscope',
        # tiktoken
        'tiktoken', 'tiktoken_ext', 'tiktoken_ext.openai_public',
        # sqlparse（子模块显式声明）
        'sqlparse', 'sqlparse.sql', 'sqlparse.tokens', 'sqlparse.keywords',
        # yaml
        'yaml',
        # vaxport new modules (v1.4.0)
        'vaxport.deep_research', 'vaxport.semantic_memory',
        'vaxport.skill_engine', 'vaxport.skill_validator',
        'vaxport.ear.skill_monitor',
        # web framework
        'uvicorn', 'fastapi', 'starlette',
        'fastapi.middleware.cors', 'fastapi.responses',
        'sse_starlette', 'sse_starlette.sse',
        # pydantic
        'pydantic', 'pydantic_core',
        # http
        'httpx', 'certifi',
        # setuptools/pkg_resources 依赖（PyInstaller 6.x 兼容）
        'jaraco.text',
        'jaraco.context',
        'jaraco.functools',
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