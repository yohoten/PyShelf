"""后台渲染服务：把耗时的位图合成（封面 / 页面）放到 QThreadPool。

设计要点：
- 工作线程只做纯像素运算（PIL），产出 `QImage` 后经信号**排队**回主线程；
- QPixmap 只能在主线程创建，故由接收方在主线程把 QImage 转为 QPixmap；
- key 用于识别结果归属与丢弃过期结果（异步竞态保护）；
- 显式持有在途 QRunnable 引用：PyQt5 下若无人引用，临时任务可能在被调度前
  即被回收，导致任务被静默丢弃（表现为「一直渲染中」）；
- 退出时置 `_closing` 并等待在途任务，避免向已析构对象发信号（进程崩溃）。
"""
from __future__ import annotations

from PIL import Image
from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt5.QtGui import QImage


def pil_to_qimage(img: Image.Image) -> QImage:
    """PIL 图像 → QImage（ARGB32；copy 避免底层缓冲被回收）。"""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    data = img.tobytes("raw", "BGRA")
    return QImage(data, img.width, img.height, img.width * 4,
                  QImage.Format_ARGB32).copy()


def _is_deleted(obj) -> bool:
    """C++ 侧对象是否已析构（SIP）。"""
    try:
        from PyQt5 import sip
        return bool(sip.isdeleted(obj))
    except Exception:
        return False


class _Task(QRunnable):
    def __init__(self, owner: "RenderService", key, fn):
        super().__init__()
        self.owner = owner
        self.key = key
        self.fn = fn
        self.setAutoDelete(True)

    def run(self):
        qimg = None
        try:
            img = self.fn()          # 期望返回 PIL.Image
            if img is not None:
                qimg = pil_to_qimage(img)
        except Exception:
            qimg = None
        try:
            self.owner._deliver(self.key, qimg)
        except Exception:
            pass
        finally:
            try:
                self.owner._finish(id(self))
            except Exception:
                pass


class RenderService(QObject):
    """线程池化渲染。`ready(key, QImage|None)` 始终在主线程发出。"""

    ready = pyqtSignal(object, object)

    def __init__(self, threads: int = 2, parent=None):
        super().__init__(parent)
        self._closing = False
        self._tasks: dict[int, _Task] = {}     # 在途任务：防止被过早回收
        self._pool = QThreadPool(self)
        self.set_threads(threads)

    def set_threads(self, n: int):
        self._pool.setMaxThreadCount(max(1, min(int(n or 1), 16)))

    @property
    def threads(self) -> int:
        return self._pool.maxThreadCount()

    @property
    def inflight(self) -> int:
        return len(self._tasks)

    def submit(self, key, fn):
        """提交一个渲染任务：fn() -> PIL.Image。"""
        if self._closing:
            return
        task = _Task(self, key, fn)
        self._tasks[id(task)] = task
        self._pool.start(task)

    def shutdown(self):
        """退出前清空未开始的任务并等待在途任务结束。

        置 `_closing` 后，任何在途任务都不会再 emit（避免向已析构对象发信号
        导致进程崩溃 0xC0000409）。
        """
        self._closing = True
        try:
            self._pool.clear()
            self._pool.waitForDone(5000)
        except Exception:
            pass
        self._tasks.clear()

    # 工作线程调用；跨线程 emit 由 Qt 自动排队到主线程
    def _deliver(self, key, qimg):
        if self._closing or _is_deleted(self):
            return
        try:
            self.ready.emit(key, qimg)
        except RuntimeError:
            pass

    def _finish(self, task_id: int):
        self._tasks.pop(task_id, None)
