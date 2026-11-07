"""
悬浮剪贴板 —— 程序入口。

支持命令：
    python main.py                 启动悬浮剪贴板主界面 + 后台剪贴板实时监听
    python main.py --console       仅后台监听并打印到控制台（Ctrl+C 退出）
    python main.py --list          打印历史记录
    python main.py --list --category favorite --search 关键词
    python main.py --clear         清空未收藏记录
    python main.py --verify        执行验收标准端到端自检
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import paths
from core.clipboard_watcher import ClipboardWatcher
from core.config_manager import ConfigManager
from core.database import CATEGORIES, ClipboardDatabase
from core.hotkey_manager import HotkeyManager
from core.single_instance import SingleInstanceLock
from ui.main_window import MainWindow
from ui.tray_manager import SystemTrayManager


def _setup_console() -> None:
    # 打包为 --windowed 时 stdout 为 None；控制台编码不支持的字符用 ? 代替
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        datefmt="%H:%M:%S")


def _preview(item: dict, width: int = 60) -> str:
    text = item["content"].replace("\r", "").replace("\n", "⏎")
    return text if len(text) <= width else text[: width - 1] + "…"


def _format_item(index: int, item: dict) -> str:
    ts = datetime.fromtimestamp(item["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
    star = "★" if item["is_favorite"] else " "
    return f"{index:>3}. {star} #{item['id']:<4} [{item['item_type']:<5}] {item['meta_info']:<12} {ts}  {_preview(item)}"


def cmd_list(db: ClipboardDatabase, category: str, search: str) -> None:
    items = db.get_items(category, search)
    print(f"数据目录: {db.data_dir}")
    print(f"分类={category} 搜索={search!r} 共 {len(items)} 条，收藏 {db.get_favorite_count()} 条")
    for i, item in enumerate(items, 1):
        print(_format_item(i, item))


def cmd_monitor(db: ClipboardDatabase) -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    watcher = ClipboardWatcher(db)

    def on_recorded(item_id: int, action: str) -> None:
        item = db.get_item(item_id)
        if item:
            tag = "新增" if action == "inserted" else "置顶"
            print(f"[{tag}] {_format_item(1, item)}", flush=True)

    watcher.item_recorded.connect(on_recorded)
    watcher.start()

    # 让 Python 有机会处理 Ctrl+C
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(200)

    print(f"数据目录: {db.data_dir}")
    print("正在监听剪贴板……复制文字 / 截图 / 在资源管理器复制文件试试（Ctrl+C 退出）", flush=True)
    code = app.exec()
    watcher.stop()
    return code


def cmd_verify(db: ClipboardDatabase) -> int:
    """按照第一步验收标准执行端到端自检。"""
    import json
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtTest import QTest

    app = QApplication.instance() or QApplication(sys.argv)
    clipboard = app.clipboard()
    saved_text = clipboard.text()

    watcher = ClipboardWatcher(db)
    watcher.start()
    QTest.qWait(150)

    try:
        print("=" * 68)
        print("【验收项 1】复制文字、截图、复制两个文件 -> 验证类型识别与 meta_info")
        print("=" * 68)

        # 1. 复制一段 169 字符的文字
        base_text = "悬浮剪贴板第一步验收测试：支持文本、图像与本地文件列表自动识别，具备 SHA256 去重置顶与防回环合并复制保护。"
        sample_text = (base_text * 4)[:169]
        assert len(sample_text) == 169
        clipboard.setText(sample_text)
        QTest.qWait(350)

        text_items = db.get_items("text")
        assert text_items and text_items[0]["meta_info"] == "169 字符", f"文本识别异常: {text_items[:1]}"
        print(f"  [✓] 文本识别成功: item_type={text_items[0]['item_type']!r}, meta_info={text_items[0]['meta_info']!r}")

        # 2. 截一张图 (1203 X 903)
        img = QImage(1203, 903, QImage.Format.Format_RGB32)
        img.fill(QColor("#3b78e7"))
        clipboard.setImage(img)
        QTest.qWait(450)

        image_items = db.get_items("image")
        assert image_items and image_items[0]["meta_info"] == "1203 X 903", f"图像识别异常: {image_items[:1]}"
        img_file = paths.resolve_data_path(image_items[0]["content"], db.data_dir)
        assert img_file.exists(), f"图像缓存文件未生成: {img_file}"
        print(
            f"  [✓] 图像识别成功: item_type={image_items[0]['item_type']!r}, "
            f"meta_info={image_items[0]['meta_info']!r}, 缓存路径={image_items[0]['content']!r}"
        )

        # 3. 在资源管理器复制两个文件
        app_dir = paths.get_app_dir()
        f1 = app_dir / "requirements.txt"
        f2 = app_dir / "参考UI.png"
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(f1)), QUrl.fromLocalFile(str(f2))])
        clipboard.setMimeData(mime)
        QTest.qWait(350)

        file_items = db.get_items("files")
        assert file_items and file_items[0]["meta_info"] == "2 个文件", f"文件识别异常: {file_items[:1]}"
        parsed_files = json.loads(file_items[0]["content"])
        assert len(parsed_files) == 2, f"文件列表数量异常: {parsed_files}"
        print(
            f"  [✓] 文件识别成功: item_type={file_items[0]['item_type']!r}, "
            f"meta_info={file_items[0]['meta_info']!r}, 文件列表={parsed_files}"
        )

        print("\n" + "=" * 68)
        print("【验收项 2】收藏统计与按分类/关键词查询接口验证")
        print("=" * 68)

        target_id = text_items[0]["id"]
        new_state, fav_count = db.toggle_favorite(target_id)
        assert new_state is True and fav_count >= 1
        print(f"  [✓] toggle_favorite({target_id}) -> 状态={new_state}, 当前收藏总数={fav_count}")

        for cat in CATEGORIES:
            rows = db.get_items(category=cat)
            summary = [(r["id"], r["item_type"], r["meta_info"], "★" if r["is_favorite"] else "-") for r in rows]
            print(f"  [✓] get_items(category={cat!r:<10}) -> 共 {len(rows)} 条: {summary}")

        search_rows = db.get_items(category="all", search_query="requirements.txt")
        assert len(search_rows) >= 1
        print(f"  [✓] get_items(search_query='requirements.txt') -> 命中 {len(search_rows)} 条 (id={search_rows[0]['id']})")

        print("\n" + "=" * 68)
        print("【当前数据库记录列表】")
        print("=" * 68)
        cmd_list(db, "all", "")
        print("\n✅ 第一步全部验收标准均已通过！")
        return 0
    finally:
        watcher.stop()
        if saved_text:
            clipboard.setText(saved_text)
            QTest.qWait(80)


def cmd_gui(db: ClipboardDatabase, minimized: bool = False) -> int:
    """启动无边框悬浮剪贴板主界面、单实例防重开锁、全局热键、系统托盘与后台剪贴板监听引擎。"""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    single_lock = SingleInstanceLock()
    if not single_lock.try_acquire():
        logging.info("已有悬浮剪贴板实例在后台运行，已通知其唤起主窗口，当前进程退出。")
        return 0

    config = ConfigManager(data_dir=db.data_dir)
    hotkey_mgr = HotkeyManager(trigger_timeout_ms=config.trigger_timeout_ms)
    watcher = ClipboardWatcher(db)
    window = MainWindow(db, watcher, config_manager=config, hotkey_manager=hotkey_mgr)
    single_lock.activate_requested.connect(window.summon_window)
    tray = SystemTrayManager(window)
    tray.show()
    watcher.start()

    ok, msg = hotkey_mgr.register_hotkey(config.hotkey, trigger_timeout_ms=config.trigger_timeout_ms)
    if not ok:
        logging.warning("启动时注册全局热键失败: %s", msg)

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(200)

    if not minimized:
        window.summon_window()

    code = app.exec()
    hotkey_mgr.unregister_hotkey()
    tray.hide()
    watcher.stop()
    single_lock.release()
    return code


def main() -> int:
    _setup_console()
    parser = argparse.ArgumentParser(description="悬浮剪贴板工具")
    parser.add_argument("--console", action="store_true", help="仅后台监听并输出到控制台")
    parser.add_argument("--list", action="store_true", help="打印历史记录后退出")
    parser.add_argument("--category", default="all", choices=CATEGORIES, help="配合 --list 使用")
    parser.add_argument("--search", default="", help="配合 --list 使用")
    parser.add_argument("--clear", action="store_true", help="清空未收藏记录后退出")
    parser.add_argument("--verify", action="store_true", help="执行验收标准端到端自检")
    parser.add_argument("--minimized", action="store_true", help="（预留）静默启动到托盘")
    args = parser.parse_args()

    db = ClipboardDatabase(paths.get_db_path(), paths.get_data_dir())
    try:
        if args.verify:
            return cmd_verify(db)
        if args.clear:
            print(f"已清空 {db.clear_unfavorited()} 条未收藏记录")
            return 0
        if args.list:
            cmd_list(db, args.category, args.search)
            return 0
        if args.console:
            return cmd_monitor(db)
        return cmd_gui(db, minimized=args.minimized)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
