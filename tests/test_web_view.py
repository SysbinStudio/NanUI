"""WebView（Web 引擎控件）基础测试。

与 test_widgets.py 的约定一致：只验证创建、关键行为与主题切换，
不做像素级视觉断言。

无头 / CI 注意：
- 顶部在导入任何 Qt/NanUI 模块之前设置 QTWEBENGINE_CHROMIUM_FLAGS，
  让 GitHub Actions 的 Linux 无头环境不需要 root 也能启动 Chromium；
- WebEngine 是可选依赖，缺失时整个文件跳过；
- 每个用例结束都会 deleteLater() + 处理事件循环，避免残留幽灵进程；
- 所有等待信号的循环都有 10 秒硬超时，网络异常时不会挂死。

运行（仓库根目录）：
    python -m pytest tests/test_web_view.py -v
"""

import os

# 必须早于任何 QtWebEngine / NanUI 导入
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-gpu --disable-dev-shm-usage",
)

from NanUI import WEBENGINE_AVAILABLE, WebView  # noqa: E402
from NanUI.utils.theme_manager import apply_theme, get_color  # noqa: E402

import pytest  # noqa: E402

pytestmark = pytest.mark.skipif(
    not WEBENGINE_AVAILABLE, reason="PySide6 WebEngine 组件未安装"
)
pytest.importorskip("PySide6.QtWebEngineWidgets")
pytest.importorskip("PySide6.QtWebChannel")

from PySide6.QtCore import QElapsedTimer, QUrl  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QSignalSpy, QTest  # noqa: E402

HARD_TIMEOUT_MS = 10000


@pytest.fixture
def make_view(qapp):
    """创建 WebView，并在用例结束后删掉所有实例 + 跑事件循环。

    QWebEngineView 背后是 Chromium 的子进程/渲染线程，只 close() 不
    deleteLater() 会在 CI 里留下幽灵进程，所以统一在这里兜底清理。
    """
    views = []

    def factory(**kwargs):
        view = WebView(**kwargs)
        views.append(view)
        return view

    yield factory

    for view in views:
        view.deleteLater()
    # 给事件循环几轮机会完成 deferred delete，并让 Chromium 页面正式释放
    for _ in range(10):
        qapp.processEvents()
        QTest.qWait(10)
    qapp.processEvents()


def _wait_for(spy, qapp, timeout_ms=HARD_TIMEOUT_MS):
    """等信号触发，带 10 秒硬超时，避免网络问题导致测试永久挂起。"""
    timer = QElapsedTimer()
    timer.start()
    while not spy.count() and timer.elapsed() < timeout_ms:
        qapp.processEvents()
        QTest.qWait(10)
    return spy.count() > 0


# ---------- 导出与创建 ----------


def test_webengine_available_flag():
    """装了 WebEngine 时标志为 True，且顶层可直接拿到 WebView。"""
    assert WEBENGINE_AVAILABLE is True
    assert WebView is not None


def test_webview_create(qapp, make_view):
    """WebView 可创建，初始是空白页。"""
    view = make_view()
    assert view.url().toString() in ("", "about:blank")
    assert view._web_view is not None


def test_to_qurl_local_path():
    """本地路径自动转 file://，网址原样保留。"""
    assert WebView._to_qurl("https://example.com").toString() == "https://example.com"
    local = WebView._to_qurl(r"C:\tmp\test.html")
    assert local.isLocalFile()
    assert local.toLocalFile().endswith("test.html")


# ---------- 消息桥 ----------


def test_bridge_send_signal(make_view):
    """桥对象的 send 槽转发到 WebView.messageReceived。"""
    view = make_view()
    spy = QSignalSpy(view.messageReceived)
    view._bridge.send("hello")
    assert spy.count() == 1
    assert spy.at(0)[0] == "hello"


def test_js_post_message(qapp, make_view):
    """页面里的 NanUI.postMessage() 能到达 Python 侧。"""
    view = make_view()
    spy = QSignalSpy(view.messageReceived)
    view.setHtml(
        "<html><body><script>NanUI.postMessage('from-js');</script></body></html>"
    )
    assert _wait_for(spy, qapp)
    received = [str(spy.at(i)[0]) for i in range(spy.count())]
    assert "from-js" in received


def test_python_post_message_uses_json_payload(make_view):
    """postMessage 生成可被 JS 解析的调用（含引号/换行转义）。"""
    view = make_view()
    captured = []
    view.runJavaScript = captured.append
    view.postMessage('a "quoted"\nmessage')
    assert len(captured) == 1
    assert "NanUI._emitMessage(" in captured[0]
    assert 'a \\"quoted\\"\\nmessage' in captured[0]


# ---------- 导航拦截 ----------


def test_blocked_host_exact_and_subdomain(make_view):
    """黑名单按主机名精确匹配（含子域），不做子串误伤。"""
    view = make_view()
    view.addBlockedHost("Evil.com")
    assert view._should_block(QUrl("https://evil.com/a")) is True
    assert view._should_block(QUrl("https://sub.evil.com/a")) is True
    assert view._should_block(QUrl("https://notevil.com/a")) is False
    view.clearBlockedHosts()
    assert view._should_block(QUrl("https://evil.com/a")) is False


def test_block_external_navigation(make_view):
    """全局开关拦 http/https，但不影响 file:///about:blank。"""
    view = make_view()
    view.blockExternalNavigation(True)
    assert view._should_block(QUrl("http://example.com")) is True
    assert view._should_block(QUrl("https://example.com")) is True
    assert view._should_block(QUrl("file:///tmp/a.html")) is False
    assert view._should_block(QUrl("about:blank")) is False


def test_navigation_blocked_signal(qapp, make_view):
    """真实导航请求被拦时发射 navigationBlocked（本地 HTML 内的链接）。"""
    view = make_view()
    view.addBlockedHost("blocked.example")
    spy = QSignalSpy(view.navigationBlocked)
    view.setHtml(
        """<html><body><a id="link" href="https://blocked.example/x">go</a>
        <script>document.getElementById('link').click();</script></body></html>"""
    )
    assert _wait_for(spy, qapp)
    assert "blocked.example" in str(spy.at(0)[0])


# ---------- 主题 ----------


def test_webview_theme_switch(qapp, make_view):
    """页面底色随主题切换，切回 light 不污染其他测试。"""
    view = make_view()
    apply_theme(qapp, "dark")
    assert view.page().backgroundColor() == QColor(get_color("web_bg"))
    apply_theme(qapp, "light")
    assert view.page().backgroundColor() == QColor(get_color("web_bg"))


def test_webview_transparent(make_view):
    """transparent=True 时页面底色为透明。"""
    view = make_view(transparent=True)
    assert view.page().backgroundColor().alpha() == 0
