"""
单实例运行锁与进程间唤醒管理器 (SingleInstanceLock)。

基于 PySide6 `QLocalServer` + `QLocalSocket`（Windows 底层为命名管道 Named Pipe）实现：
1. 程序启动时尝试连接命名管道 `FloatingClipboardTool_SingleInstance_v1`：
   - 若连接成功：说明已有实例在后台运行，立即向已有实例发送 `"SUMMON"` 唤醒指令并退出当前新进程；
   - 若连接失败：说明当前无运行实例，清理可能残留的失效管道并启动 `QLocalServer` 监听。
2. 当已有实例收到新进程发来的 `"SUMMON"` 指令时，发射 `activate_requested` 信号，直接呼出并激活剪贴板悬浮窗。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

DEFAULT_SERVER_NAME = "FloatingClipboardTool_SingleInstance_v1"
ACTIVATE_COMMAND = b"SUMMON\n"


class SingleInstanceLock(QObject):
    """单实例进程锁：防止程序多开，并支持二次启动时自动唤醒已有主窗口。"""

    #: 当第二个进程尝试启动并请求唤醒已有窗口时触发
    activate_requested = Signal()

    def __init__(self, server_name: str = DEFAULT_SERVER_NAME, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.server_name = server_name
        self._server: QLocalServer | None = None
        self._is_primary = False

    @property
    def is_primary(self) -> bool:
        return self._is_primary

    def try_acquire(self, timeout_ms: int = 250) -> bool:
        """
        尝试获取单实例锁：
        - 若当前为首个实例：启动本地服务监听并返回 True；
        - 若已有实例在运行：自动向已有实例发送唤醒信号并返回 False。
        """
        if self._is_primary and self._server is not None and self._server.isListening():
            return True

        if self.notify_existing_instance(timeout_ms=timeout_ms):
            log.info("检测到已有实例正在运行，已发送唤醒窗口信号。")
            return False

        # 无活动实例：清理可能因异常退出残留的同名管道并开始监听
        QLocalServer.removeServer(self.server_name)
        server = QLocalServer(self)
        server.newConnection.connect(self._on_new_connection)
        if not server.listen(self.server_name):
            # 再做一次强制清理后重试监听
            QLocalServer.removeServer(self.server_name)
            if not server.listen(self.server_name):
                log.error("启动单实例 QLocalServer 失败: %s", server.errorString())
                return False

        self._server = server
        self._is_primary = True
        log.info("已获取单实例运行锁: %s", self.server_name)
        return True

    def notify_existing_instance(self, timeout_ms: int = 250) -> bool:
        """尝试连接已存在的实例并发送唤醒指令，成功返回 True。"""
        sock = QLocalSocket(self)
        sock.connectToServer(self.server_name)
        if not sock.waitForConnected(timeout_ms):
            sock.abort()
            sock.deleteLater()
            return False

        try:
            sock.write(ACTIVATE_COMMAND)
            sock.flush()
            sock.waitForBytesWritten(timeout_ms)
            sock.disconnectFromServer()
            return True
        except Exception:  # noqa: BLE001
            log.exception("向已有实例发送唤醒指令失败")
            return False
        finally:
            sock.deleteLater()

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            if conn is None:
                continue
            conn.readyRead.connect(lambda c=conn: self._on_client_ready_read(c))
            conn.disconnected.connect(conn.deleteLater)
            # 若数据已经到达，立即读取处理
            if conn.bytesAvailable() > 0:
                self._on_client_ready_read(conn)

    def _on_client_ready_read(self, conn: QLocalSocket) -> None:
        try:
            data = bytes(conn.readAll()).strip()
            if not data or b"SUMMON" in data.upper():
                log.info("收到二次启动唤醒请求，呼出主窗口")
                self.activate_requested.emit()
        except Exception:  # noqa: BLE001
            log.exception("读取单实例唤醒消息异常")

    def release(self) -> None:
        """释放单实例监听服务。"""
        if self._server is not None:
            try:
                if self._server.isListening():
                    self._server.close()
                QLocalServer.removeServer(self.server_name)
            except Exception:  # noqa: BLE001
                pass
            self._server = None
        self._is_primary = False
