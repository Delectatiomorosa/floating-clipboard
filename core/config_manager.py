"""
配置文件管理器 (ConfigManager)：负责读写 data/config.json。

默认配置项：
- hotkey: "Alt+V"                 默认唤起快捷键
- auto_start: False               是否开机自启
- merge_separator: "\\n"          多选合并复制时的分隔符（支持 "\\n"、"\\n\\n"、" "、""）
- clear_selection_after_copy: True
- hide_after_copy: False          合并复制后是否自动隐藏窗口
- max_history: 500                最大保留历史条数
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping

from . import paths

log = logging.getLogger(__name__)

DEFAULT_HOTKEY = "Alt+V"
DEFAULT_TRIGGER_TIMEOUT_MS = 300

DEFAULT_CONFIG: dict[str, Any] = {
    "hotkey": DEFAULT_HOTKEY,
    "trigger_timeout_ms": DEFAULT_TRIGGER_TIMEOUT_MS,
    "auto_start": False,
    "merge_separator": "\n",
    "clear_selection_after_copy": True,
    "hide_after_copy": False,
    "max_history": 500,
}

VALID_SEPARATORS = ("\n", "\n\n", " ", "")


class ConfigManager:
    """管理 data/config.json 的加载、校验与持久化保存。"""

    def __init__(self, config_path: str | Path | None = None, data_dir: str | Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else paths.get_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = Path(config_path) if config_path else paths.get_config_path(self.data_dir)
        self._data: dict[str, Any] = dict(DEFAULT_CONFIG)
        self.load()

    def load(self) -> dict[str, Any]:
        """从磁盘读取配置并与默认值合并；若文件不存在则自动创建默认配置。"""
        merged = dict(DEFAULT_CONFIG)
        if self.config_path.exists():
            try:
                raw = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    merged.update(raw)
            except Exception as exc:  # noqa: BLE001
                log.warning("读取配置文件失败，使用默认配置 (%s): %s", self.config_path, exc)
        self._data = self._sanitize(merged)
        if not self.config_path.exists():
            self.save()
        return dict(self._data)

    @staticmethod
    def _sanitize(data: Mapping[str, Any]) -> dict[str, Any]:
        hotkey = str(data.get("hotkey") or DEFAULT_HOTKEY).strip() or DEFAULT_HOTKEY
        try:
            trigger_ms = max(50, min(5000, int(data.get("trigger_timeout_ms", DEFAULT_TRIGGER_TIMEOUT_MS))))
        except (TypeError, ValueError):
            trigger_ms = DEFAULT_TRIGGER_TIMEOUT_MS
        auto_start = bool(data.get("auto_start", False))
        sep = data.get("merge_separator", "\n")
        if not isinstance(sep, str):
            sep = "\n"
        clear_sel = bool(data.get("clear_selection_after_copy", True))
        hide_after = bool(data.get("hide_after_copy", False))
        try:
            max_hist = max(10, int(data.get("max_history", 500)))
        except (TypeError, ValueError):
            max_hist = 500

        return {
            "hotkey": hotkey,
            "trigger_timeout_ms": trigger_ms,
            "auto_start": auto_start,
            "merge_separator": sep,
            "clear_selection_after_copy": clear_sel,
            "hide_after_copy": hide_after,
            "max_history": max_hist,
        }

    def save(self) -> bool:
        """原子化写入 data/config.json。"""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.config_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.config_path)
            return True
        except OSError as exc:
            log.error("保存配置文件失败 %s: %s", self.config_path, exc)
            return False

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any, auto_save: bool = True) -> None:
        self._data[key] = value
        self._data = self._sanitize(self._data)
        if auto_save:
            self.save()

    def update(self, values: Mapping[str, Any], auto_save: bool = True) -> None:
        self._data.update(values)
        self._data = self._sanitize(self._data)
        if auto_save:
            self.save()

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)

    # ------------------------------------------------------------------ 便捷属性
    @property
    def hotkey(self) -> str:
        return str(self._data["hotkey"])

    @hotkey.setter
    def hotkey(self, value: str) -> None:
        self.set("hotkey", value)

    @property
    def trigger_timeout_ms(self) -> int:
        return int(self._data["trigger_timeout_ms"])

    @trigger_timeout_ms.setter
    def trigger_timeout_ms(self, value: int) -> None:
        self.set("trigger_timeout_ms", int(value))

    @property
    def auto_start(self) -> bool:
        return bool(self._data["auto_start"])

    @auto_start.setter
    def auto_start(self, value: bool) -> None:
        self.set("auto_start", bool(value))

    @property
    def merge_separator(self) -> str:
        return str(self._data["merge_separator"])

    @merge_separator.setter
    def merge_separator(self, value: str) -> None:
        self.set("merge_separator", str(value))

    @property
    def clear_selection_after_copy(self) -> bool:
        return bool(self._data["clear_selection_after_copy"])

    @property
    def hide_after_copy(self) -> bool:
        return bool(self._data["hide_after_copy"])

    @property
    def max_history(self) -> int:
        return int(self._data["max_history"])
