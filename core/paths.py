"""
路径工具：统一管理程序目录、资源目录与数据目录。

兼容两种运行方式：
1. 开发环境：``python main.py``，程序目录 = 项目根目录（core/ 的上一级）。
2. PyInstaller 打包后的 ``.exe``：程序目录 = ``.exe`` 所在目录
   （使用 ``sys.executable`` 定位，绝不依赖 ``os.getcwd()``，
   保证开机自启 / 从任意位置启动时工作目录变化也能正确读写数据）。

数据目录选择策略：
- 优先使用「程序同级 data/ 目录」（便携模式，数据跟着 exe 走）；
- 若该目录不可写（例如 exe 被放在 C:\\Program Files 下），
  自动回退到用户目录 ``%LOCALAPPDATA%\\FloatingClipboard\\data``；
- 可通过环境变量 ``FLOATING_CLIPBOARD_DATA_DIR`` 强制指定（便于测试/调试）。

目录结构::

    data/
    ├── clipboard.db     SQLite 数据库
    ├── config.json      配置文件（第五步使用）
    └── images/          剪贴板图片缓存（PNG）
"""

from __future__ import annotations

import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

APP_NAME = "FloatingClipboard"
DATA_DIR_ENV = "FLOATING_CLIPBOARD_DATA_DIR"

DB_FILENAME = "clipboard.db"
CONFIG_FILENAME = "config.json"
IMAGES_DIRNAME = "images"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包后的环境中。"""
    return bool(getattr(sys, "frozen", False))


def get_app_dir() -> Path:
    """程序所在目录：打包后为 exe 所在目录，开发环境为项目根目录。"""
    if is_frozen():
        return Path(os.path.dirname(os.path.abspath(sys.executable)))
    return Path(__file__).resolve().parent.parent


def get_resource_dir() -> Path:
    """只读内置资源目录（图标等）：打包后为 sys._MEIPASS 临时解压目录。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", get_app_dir()))
    return get_app_dir()


def resource_path(*parts: str) -> Path:
    """获取内置资源的绝对路径，如 ``resource_path('assets', 'icon.ico')``。"""
    return get_resource_dir().joinpath(*parts)


def get_user_data_dir() -> Path:
    """用户目录下的数据目录（回退方案）。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_NAME / "data"


def _is_writable_dir(path: Path) -> bool:
    """尝试创建目录并写入临时文件，判断目录是否真正可写。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".write_test_", delete=True):
            pass
        return True
    except OSError:
        return False


@lru_cache(maxsize=1)
def _default_data_dir() -> Path:
    portable = get_app_dir() / "data"
    if _is_writable_dir(portable):
        return portable
    fallback = get_user_data_dir()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def get_data_dir() -> Path:
    """数据根目录（自动创建）。"""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        path = Path(override).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    return _default_data_dir()


def get_images_dir(data_dir: Path | None = None) -> Path:
    """图片缓存目录 data/images（自动创建）。"""
    path = Path(data_dir or get_data_dir()) / IMAGES_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_db_path(data_dir: Path | None = None) -> Path:
    return Path(data_dir or get_data_dir()) / DB_FILENAME


def get_config_path(data_dir: Path | None = None) -> Path:
    return Path(data_dir or get_data_dir()) / CONFIG_FILENAME


def to_data_relative(path: str | os.PathLike, data_dir: Path | None = None) -> str:
    """把数据目录内的绝对路径转换为相对路径（统一使用 '/' 分隔，如 'images/abc.png'）。"""
    base = Path(data_dir or get_data_dir()).resolve()
    p = Path(path).resolve()
    try:
        return p.relative_to(base).as_posix()
    except ValueError:
        # 不在数据目录内则原样返回绝对路径
        return str(p)


def resolve_data_path(rel_path: str | os.PathLike, data_dir: Path | None = None) -> Path:
    """把数据库中存储的相对路径还原为绝对路径（已是绝对路径则原样返回）。"""
    p = Path(rel_path)
    if p.is_absolute():
        return p
    return Path(data_dir or get_data_dir()) / p
