#!/usr/bin/env python3
"""运行环境准备：让 WeasyPrint 能用，并把 fontconfig 缓存隔离到临时目录。

要点：
1. macOS + homebrew 的 pango/gobject 不在默认搜索路径，需要 DYLD_FALLBACK_LIBRARY_PATH。
2. fontconfig 默认会把缓存写进 /opt/homebrew/var/cache/fontconfig，并在重建时清理
   自己的 TMP/LCK 文件——在受管控的环境里会被当成"删除文件"拦截。
   这里写一个只改 cachedir 的包装配置，把缓存全部落到临时目录。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_HOMEBREW_CONFS = [
    "/opt/homebrew/etc/fonts/fonts.conf",
    "/usr/local/etc/fonts/fonts.conf",
    "/etc/fonts/fonts.conf",
]


def prepare() -> dict[str, str]:
    """返回应注入的环境变量；同时顺手写好临时 fontconfig 配置。"""
    env: dict[str, str] = {}

    if sys.platform == "darwin":
        for lib in ("/opt/homebrew/lib", "/usr/local/lib"):
            if Path(lib).is_dir():
                old = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
                env["DYLD_FALLBACK_LIBRARY_PATH"] = f"{lib}:{old}" if old else lib
                break

    base = Path(tempfile.gettempdir()) / "a4skill"
    cache = base / "fc-cache"
    cache.mkdir(parents=True, exist_ok=True)
    env["XDG_CACHE_HOME"] = str(base / "xdg")
    (base / "xdg").mkdir(parents=True, exist_ok=True)

    conf = next((c for c in _HOMEBREW_CONFS if Path(c).exists()), None)
    if conf:
        wrapper = base / "fonts.conf"
        # cachedir 必须写在 include 之前：fontconfig 的 cachedir 是「追加」语义，
        # 写在 include 之后时系统默认的 /opt/homebrew/var/cache/fontconfig 仍然生效，
        # 重建缓存时照样会去动那个目录（受管控环境下会被拦成删除用户文件）。
        wrapper.write_text(
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
            "<fontconfig>\n"
            f"  <cachedir>{cache}</cachedir>\n"
            f'  <include ignore_missing="yes">{conf}</include>\n'
            "</fontconfig>\n",
            encoding="utf-8",
        )
        env["FONTCONFIG_FILE"] = str(wrapper)

    os.environ.update(env)
    return env


def missing_cjk_font() -> bool:
    """系统里有没有能显示中文的字体。

    缺字体时 weasyprint 不会报错，中文会静默渲染成豆腐块（空心方框），
    而 verify 也查不出来（转曲后只剩路径，看不出字形对不对）。
    所以渲染前主动查一次 fontconfig：`fc-list :lang=zh` 为空即缺失。

    fc-list 不可用时返回 False（不做判断），避免在极简环境里误报。
    """
    import subprocess

    try:
        r = subprocess.run(
            ["fc-list", ":lang=zh"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0:
        return False
    return not r.stdout.strip()
