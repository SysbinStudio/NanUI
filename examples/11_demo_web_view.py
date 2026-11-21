"""
11 一个 WebView（需要 PySide6-Addons / WebEngine 组件）。
"""

from NanUI import WEBENGINE_AVAILABLE, PushButton, WebView, Window
from NanUI.utils import apply_theme
from NanUI.utils.theme_manager import get_color

from PySide6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout

if not WEBENGINE_AVAILABLE:
    raise SystemExit('WebView 需要 WebEngine 组件：pip install "PySide6-Addons>=6.5"')


class MainWindow(Window):
    def __init__(self):
        super().__init__(resizable=True)
        self.resize(800, 560)
        self.setTitle("一个 WebView")

        self.web = WebView()
        self.web.messageReceived.connect(self._on_js_message)
        # 演示导航拦截：外链点击会被拦下，地址通过 navigationBlocked 发出来
        self.web.blockExternalNavigation(True)
        self.web.navigationBlocked.connect(lambda url: print("已拦截导航:", url))
        self.web.setHtml(self._build_html())

        self.btnSend = PushButton("Python → JS")
        self.btnSend.clicked.connect(self._send_to_js)
        self.btnEval = PushButton("执行 JavaScript")
        self.btnEval.clicked.connect(
            lambda: self.web.runJavaScript(
                "document.getElementById('eval').textContent = '已执行';"
            )
        )

        buttonLayout = QHBoxLayout()
        buttonLayout.addWidget(self.btnSend)
        buttonLayout.addWidget(self.btnEval)

        self.mainLayout = QVBoxLayout()
        self.mainLayout.addWidget(self.web)
        self.mainLayout.addLayout(buttonLayout)
        self.setLayout(self.mainLayout)

    @staticmethod
    def _build_html():
        """页面底色取自当前主题，切主题后 WebView 会自动刷新页面底色。"""
        bg = get_color("web_bg")
        fg = get_color("text")
        return f"""
        <!doctype html><html><head><meta charset="utf-8"><title>NanUI WebView</title>
        <style>
          body {{ background: {bg}; color: {fg}; font-family: 'Microsoft YaHei', sans-serif;
                 padding: 24px; }}
          button {{ padding: 8px 16px; margin: 8px 8px 8px 0; }}
        </style></head><body>
          <h2>NanUI × QtWebEngine</h2>
          <p>点击下面的按钮，从 JS 给 Python 发消息：</p>
          <button onclick="NanUI.postMessage('来自网页的消息')">JS → Python</button>
          <p>来自 Python 的消息：<b id="from-python">等待中…</b></p>
          <p>runJavaScript 结果：<b id="eval">未执行</b></p>
          <p><a href="https://www.qt.io/">会被拦截的外链</a></p>
          <script>
            NanUI.onMessage(function (text) {{
              document.getElementById('from-python').textContent = text;
            }});
          </script>
        </body></html>
        """

    def _on_js_message(self, text):
        print("收到网页消息:", text)

    def _send_to_js(self):
        self.web.postMessage("来自 Python 的消息")


if __name__ == "__main__":
    app = QApplication([])
    apply_theme(app, "light")
    window = MainWindow()
    window.show()
    app.exec()
