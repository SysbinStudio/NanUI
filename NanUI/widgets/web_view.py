"""
注：本文件部分代码由 AI 辅助开发。

延迟加载约定
------------
本模块顶层**不** import 任何 QtWebEngine / QtWebChannel 模块，也不定义继承
QWebEngineView 的类。`import NanUI` 只加载 PySide6-Essentials（QWidget 等），
不会碰几百兆的 Chromium 动态库。只有真正构造 WebView 时才导入 WebEngine，
并把一个 QWebEngineView 作为子控件组合进来。
"""

import importlib.util
import json
import os
from typing import Any, Optional, Set, Union

from NanUI.utils.theme_manager import get_color

from PySide6.QtCore import QFile, QIODevice, QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QVBoxLayout, QWidget

WEBENGINE_HINT = (
    "WebView 需要 PySide6 的 WebEngine 组件，"
    '请运行: pip install "PySide6-Addons>=6.5" 或 pip install "PySide6[addons]>=6.5"'
)


def _detect_webengine() -> bool:
    """只探测模块是否存在，不真正 import，避免加载 Chromium 动态库。"""
    try:
        return importlib.util.find_spec("PySide6.QtWebEngineWidgets") is not None
    except (ImportError, ValueError):
        return False


WEBENGINE_AVAILABLE = _detect_webengine()

# qwebchannel.js 的加载顺序：
#   1. Qt WebEngine 内置资源 :/qtwebchannel/qwebchannel.js（最可靠，所有 wheel 都有）；
#   2. 安装目录里散落的 qwebchannel.js（部分 Linux 发行版会单独放一份）；
#   3. 都没有时退化为原始 transport 调用——只保证 JS → Python 的 postMessage 可用。
#
# 注入脚本做两件事：
#   - 通过官方 QWebChannel 初始化 ``nanui`` 桥对象，让 NanUI.postMessage() 走标准协议；
#   - 提供零依赖的 NanUI.onMessage() 回调注册，Python → JS 用 runJavaScript 直接派发，
#     不依赖 qwebchannel.js 是否加载成功。
_QWEBCHANNEL_INJECT_JS = """
(function () {
    if (window.NanUI) return;
    var t = (typeof qt !== "undefined") ? qt.webChannelTransport : null;
    var listeners = [];
    var pending = [];
    var api = {
        bridge: null,
        transport: t,
        postMessage: function (message) {
            var text = String(message);
            // 页面内联脚本可能早于 QWebChannel 握手执行，此时直接走
            // transport.send 会被 C++ 端丢弃；排队等桥就绪后再统一发送。
            if (api.bridge) { api.bridge.send(text); return true; }
            pending.push(text);
            return false;
        },
        onMessage: function (callback) {
            if (typeof callback === "function") listeners.push(callback);
        },
        _emitMessage: function (message) {
            var text = String(message);
            listeners.slice().forEach(function (callback) {
                try { callback(text); } catch (e) {}
            });
        }
    };
    window.NanUI = api;
    if (typeof QWebChannel === "function" && t && QWebChannel.prototype) {
        try {
            new QWebChannel(t, function (channel) {
                api.bridge = channel.objects.nanui;
                pending.splice(0).forEach(function (text) { api.bridge.send(text); });
            });
        } catch (e) {}
    }
})();
"""


def _load_qwebchannel_source() -> str:
    """返回注入页面的桥接脚本，优先使用 Qt 自带的 qwebchannel.js。"""
    try:
        qfile = QFile(":/qtwebchannel/qwebchannel.js")
        if qfile.open(QIODevice.OpenModeFlag.ReadOnly):
            raw = qfile.readAll().data()
            data = bytes(raw).decode("utf-8", errors="replace")
            qfile.close()
            if data:
                return data + "\n" + _QWEBCHANNEL_INJECT_JS
    except Exception:
        pass

    # 退路：某些发行版把 qwebchannel.js 放在 PySide6 安装目录里。
    try:
        import PySide6

        base = os.path.dirname(os.path.abspath(PySide6.__file__))
        for dirpath, _dirnames, filenames in os.walk(base):
            if "qwebchannel.js" in filenames:
                with open(
                    os.path.join(dirpath, "qwebchannel.js"), encoding="utf-8"
                ) as f:
                    return f.read() + "\n" + _QWEBCHANNEL_INJECT_JS
    except (OSError, ImportError):
        pass
    return _QWEBCHANNEL_INJECT_JS


class WebView(QWidget):
    """
    Web 引擎控件（基于 Chromium 的 QtWebEngine 封装）。

    这是一个普通的 QWidget 容器，内部组合一个 QWebEngineView；与直接继承
    QWebEngineView 相比，这样能做到真正的**延迟导入**：``import NanUI``
    不会加载 Chromium，只有 ``WebView()`` 构造时才导入 WebEngine 模块。
    底层 QWebEngineView 的原生方法（``page()`` / ``url()`` / ``title()`` /
    ``reload()`` 等）通过属性代理透明转发，用起来和裸控件基本一致。

    相比裸用 QWebEngineView，这里做了三件事：

    1. **主题适配**：页面底色取自 theme_manager 的 web_bg 键，切主题时由
       apply_theme() 自动刷新，深色主题下不会出现刺眼的白底。
    2. **信号收敛**：把 Qt 分散的信号统一转发成参数为基本类型的 NanUI
       风格信号（loadStarted / loadFinished / titleChanged / urlChanged）。
    3. **扩展能力**：
       - 导航拦截：域名黑名单或全局外链拦截，被拦时发射 navigationBlocked；
       - JS → Python 消息通道：``NanUI.postMessage(text)`` 已自动注入所有
         frame（底层走官方 QWebChannel 协议），页面里直接调用：

             NanUI.postMessage("hello");   // Python 侧收到 messageReceived

         Python → JS 方向：

             NanUI.onMessage(function (text) { ... });
             view.postMessage("hello");    // JS 回调收到 text

    环境中没有 PySide6 的 WebEngine 组件时，``import NanUI`` 不会崩溃，
    ``WEBENGINE_AVAILABLE`` 为 False，只有构造时才抛出带安装提示的
    RuntimeError。

    Args:
        parent (QWidget, optional): 父控件对象。默认为 None。
        url (str, optional): 初始加载的网址、file:// 地址或本地文件路径。
            默认为 None，即空白页。
        transparent (bool): 是否让页面底色透明（叠在窗口背景上）。默认 False。

    Signals:
        loadStarted(): 开始加载页面。
        loadFinished(bool): 页面加载结束，参数为是否成功。
        titleChanged(str): 页面标题变化。
        urlChanged(str): 页面地址变化。
        navigationBlocked(str): 导航被拦截，参数为被阻止的地址。
        messageReceived(str): 收到页面通过 NanUI.postMessage(...) 发来的消息。
    """

    loadStarted = Signal()
    loadFinished = Signal(bool)
    titleChanged = Signal(str)
    urlChanged = Signal(str)
    navigationBlocked = Signal(str)
    messageReceived = Signal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        url: Optional[str] = None,
        transparent: bool = False,
    ) -> None:
        super().__init__(parent)
        if not WEBENGINE_AVAILABLE:
            raise RuntimeError(WEBENGINE_HINT)

        # ---- 真正的延迟导入：到这里才加载 Chromium 相关模块 ----
        from PySide6.QtWebChannel import QWebChannel
        from PySide6.QtWebEngineCore import QWebEngineScript
        from PySide6.QtWebEngineWidgets import QWebEngineView

        self._WebEngineScript = QWebEngineScript
        self._transparent = transparent
        self._block_external = False
        self._blocked_hosts: Set[str] = set()

        # 内部原生控件铺满整个容器
        self._web_view = QWebEngineView(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._web_view)

        page = self._web_view.page()

        # --- 主题底色（paintEvent 阶段生效，页面没设 body 背景也不露白）---
        self.apply_theme_colors()

        # --- Qt 信号 → NanUI 风格信号（统一转成基本类型，方便纯 Python 消费）---
        page.loadStarted.connect(self.loadStarted)
        page.loadFinished.connect(self.loadFinished)
        page.titleChanged.connect(self.titleChanged)
        self._web_view.urlChanged.connect(
            lambda qurl: self.urlChanged.emit(qurl.toString())
        )

        # --- 导航拦截（Qt 6.4+ 的 navigationRequested 信号）---
        nav_signal = getattr(page, "navigationRequested", None)
        if nav_signal is not None:
            nav_signal.connect(self._on_navigation_requested)

        # --- JS ↔ Python 消息通道 ---
        self._bridge = _JsBridge(self)
        self._channel = QWebChannel(self)
        self._channel.registerObject("nanui", self._bridge)
        page.setWebChannel(self._channel)
        self._inject_qwebchannel(page)

        if url is not None:
            self.setUrl(url)

    def __getattr__(self, name: str) -> Any:
        """把未定义的属性转发给内部 QWebEngineView（page/url/title/reload 等）。"""
        view = self.__dict__.get("_web_view")
        if view is not None:
            return getattr(view, name)
        raise AttributeError(name)

    # ---------- 公开接口 ----------

    def setUrl(self, url: Union[str, QUrl]) -> None:  # 覆盖以接受 str
        """加载页面。参数可以是 str（http/file/本地路径）或 QUrl。"""
        if isinstance(url, str):
            url = self._to_qurl(url)
        self._web_view.setUrl(url)

    def setHtml(self, html: str, baseUrl: str = "about:blank") -> None:
        """加载一段 HTML 字符串（如内嵌文档、富文本预览）。"""
        self._web_view.setHtml(html, self._to_qurl(baseUrl))

    def runJavaScript(self, code: str) -> None:
        """在页面上下文中执行 JavaScript（Python → JS 的一次性求值）。"""
        self._web_view.page().runJavaScript(code)

    def stopLoading(self) -> None:
        """中断当前加载。"""
        self._web_view.stop()

    def isLoading(self) -> bool:
        """页面是否仍在加载。"""
        return self._web_view.page().isLoading()

    def postMessage(self, message: str) -> None:
        """向页面广播消息（JS 侧 NanUI.onMessage 注册的回调会收到）。"""
        payload = json.dumps(str(message))
        self.runJavaScript(f"window.NanUI && NanUI._emitMessage({payload});")

    # ---------- 导航拦截 ----------

    def blockExternalNavigation(self, blocked: bool = True) -> None:
        """开启/关闭全局外链拦截（阻止一切 http/https 导航）。"""
        self._block_external = blocked

    def addBlockedHost(self, host: str) -> None:
        """加入域名黑名单：导航到该域名（含子域）时被阻止并发射 navigationBlocked。"""
        clean = host.strip().lower().lstrip(".")
        if clean:
            self._blocked_hosts.add(clean)

    def clearBlockedHosts(self) -> None:
        """清空域名黑名单。"""
        self._blocked_hosts.clear()

    def _should_block(self, url: QUrl) -> bool:
        """判断一条导航请求是否应被拦截。"""
        if self._block_external and url.scheme().lower() in ("http", "https"):
            return True
        host = url.host().lower()
        return any(
            host == blocked or host.endswith("." + blocked)
            for blocked in self._blocked_hosts
        )

    def _on_navigation_requested(self, request: Any) -> None:
        """Qt 每次导航前调用；命中规则时 reject，并发射 navigationBlocked。"""
        url = request.url()
        if self._should_block(url):
            request.reject()
            self.navigationBlocked.emit(url.toString())

    # ---------- 主题适配 ----------

    def apply_theme_colors(self) -> None:
        """把当前主题的 web_bg 颜色写进页面底色；切主题后由 apply_theme 再调。"""
        web_view = self.__dict__.get("_web_view")
        if web_view is None:
            return
        page = web_view.page()
        if page is None:
            return
        if self._transparent:
            page.setBackgroundColor(Qt.GlobalColor.transparent)
        else:
            page.setBackgroundColor(QColor(get_color("web_bg")))

    # ---------- 内部工具 ----------

    def _inject_qwebchannel(self, page: Any) -> None:
        """把 qwebchannel.js + NanUI 辅助脚本注入所有 frame。"""
        script_class = self._WebEngineScript
        script = script_class()
        script.setName("nanui-qwebchannel")
        script.setInjectionPoint(script_class.InjectionPoint.DocumentCreation)
        script.setWorldId(script_class.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        script.setSourceCode(_load_qwebchannel_source())
        page.scripts().insert(script)

    @staticmethod
    def _to_qurl(url: str) -> QUrl:
        """字符串转 QUrl：本地路径自动加 file:// 前缀。"""
        if url.startswith(("http://", "https://", "file://", "data:", "about:")):
            return QUrl(url)
        return QUrl.fromLocalFile(url)


class _JsBridge(QObject):
    """暴露给页面的桥对象（在 QWebChannel 里注册为 ``nanui``）。"""

    def __init__(self, view: WebView) -> None:
        super().__init__(view)
        self._view = view

    @Slot(str)
    def send(self, message: str) -> None:
        """JS 侧入口：把字符串消息转发给 WebView.messageReceived。"""
        self._view.messageReceived.emit(str(message))
