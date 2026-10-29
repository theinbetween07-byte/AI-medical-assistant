# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：产出单文件 exe（双击即用，也可被宿主程序以子进程方式拉起）。

构建（在 report-engine 目录下）：
    .venv\\Scripts\\pyinstaller.exe --clean --noconfirm build\\report_engine.spec

产物：dist\\medscribe-report-engine.exe
运行：dist\\medscribe-report-engine.exe --open
"""

import os

from PyInstaller.utils.hooks import collect_submodules

# spec 中的相对路径以 build/ 为基准，这里统一解析回工程根目录
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

# 术语库/抽取规则在仓库 data/terms；兼容旧布局 report-engine/terms。
# 注意：必须判断「目录非空」，空目录残留会让打包静默漏掉术语库（health 里 terminology_entries=0）。
def _nonempty_dir(path):
    try:
        return os.path.isdir(path) and any(os.scandir(path))
    except OSError:
        return False


TERMS_SRC = os.path.join(ROOT, "terms")
if not _nonempty_dir(TERMS_SRC):
    TERMS_SRC = os.path.abspath(os.path.join(ROOT, os.pardir, "data", "terms"))
if not _nonempty_dir(TERMS_SRC):
    raise SystemExit(f"找不到术语库目录（需要 extraction_rules.json / seed_terms.json）：{TERMS_SRC}")

hiddenimports = (
    collect_submodules("uvicorn")
    + ["app.main", "app.service", "app.pipeline", "app.exporter", "reportlab.pdfbase._cidfontdata"]
)

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=[],
    # 内置资源随 exe 打包；exe 同目录同名目录存在时优先使用外部版本（免重打包换模板）
    datas=[
        (os.path.join(ROOT, "templates"), "templates"),
        (TERMS_SRC, "data/terms"),
        (os.path.join(ROOT, "app", "static"), "app/static"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PyQt5", "PySide6"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="medscribe-report-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,          # 保留控制台：便于打印监听端口，宿主程序也可读取 stdout
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
