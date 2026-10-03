"""WebView2 client for the verified top-level OrbitHive website. No JS service bridge."""
from __future__ import annotations

from dataclasses import replace
import base64
from html import escape
from pathlib import Path
from threading import Event, Thread
import webbrowser

from desktop.session import DesktopSession, external_destination


def runtime_page(result, notice=''):
    title = escape(str(result.get('label') or '运行信息'))
    fields = [('工程', result.get('project_root')), ('运行档案', result.get('profile_path')),
              ('入口', result.get('url')), ('状态代码', result.get('state'))]
    rows = ''.join(f'<tr><th>{escape(key)}</th><td>{escape(str(value or "尚未选择"))}</td></tr>' for key, value in fields)
    detail = escape(str(notice or result.get('detail') or '服务身份只说明当前连接；业务结果请查看对应工作页面。'))
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>OrbitHive · 运行信息</title>
<style>body{{margin:0;background:#f1f5f9;color:#172334;font:14px/1.6 'Segoe UI','Microsoft YaHei',sans-serif}}
main{{max-width:980px;margin:32px auto;padding:24px;background:white;border:1px solid #dbe3ed;border-radius:8px}}
h1{{font-size:23px;margin:0 0 12px}}p{{color:#475569}}table{{width:100%;border-collapse:collapse;table-layout:fixed}}
th,td{{padding:9px;text-align:left;border-bottom:1px solid #e2e8f0;overflow-wrap:anywhere}}th{{width:100px}}
.note{{padding:10px 12px;background:#edf4ff;border-left:3px solid #3b82f6}}code{{font-size:12px}}</style>
<main><h1>{title}</h1><p class="note">{detail}</p><table>{rows}</table>
<p>通过顶部“连接”菜单选择工程和运行档案，然后“重新检查并打开”。服务未启动时，可先选择 Python，再点击“启动所选服务”。</p>
<p>任务首页与商品目录、商品上架、供应链、利润、知识工具五个一级入口共用网站导航。下载请使用“在浏览器中打开”；关闭 APP 会保留后台服务和任务。</p>
<p>首次配置见随附桌面使用说明：可通过 <code>--create-profile</code> 按明确路径生成运行档案，无需手工编辑 JSON。</p></main></html>'''


class DesktopWindow:
    def __init__(self, session: DesktopSession, *, hidden=False, external_opener=webbrowser.open):
        import webview
        from webview.menu import Menu, MenuAction
        self.session = session
        self.external_opener = external_opener
        self.events = []
        self.engine_ready = Event()
        self.engine_error = None
        self.native = None
        webview.settings['ALLOW_FILE_URLS'] = False
        webview.settings['ALLOW_DOWNLOADS'] = False
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
        webview.settings['IGNORE_SSL_ERRORS'] = False
        webview.settings['OPEN_DEVTOOLS_IN_DEBUG'] = False
        webview.settings['REMOTE_DEBUGGING_PORT'] = None
        menu = [Menu('连接', [MenuAction('选择工程…', self.choose_project),
            MenuAction('选择运行档案…', self.choose_profile), MenuAction('选择 Python…', self.choose_python),
            MenuAction('重新检查并打开', self.refresh), MenuAction('启动所选服务', self.start_service),
            MenuAction('运行信息', self.show_runtime), MenuAction('在浏览器中打开', self.open_browser)])]
        initial_html = runtime_page(session.last_result)
        self._internal_document = self._internal_url(initial_html)
        self.window = webview.create_window('OrbitHive', html=initial_html, js_api=None,
            width=1440, height=920, min_size=(900, 600), hidden=hidden, menu=menu)
        self.window.events.before_show += self._attach_native
        self.window.events.closing += self._closing

    def _attach_native(self):
        self.native = self.window.native
        self.native.webview.NavigationStarting += self._navigation_starting
        self.native.webview.CoreWebView2InitializationCompleted += self._engine_initialized

    def _engine_initialized(self, sender, args):
        if not args.IsSuccess:
            self.engine_error = str(args.InitializationException)
            self.engine_ready.set()
            return
        core = sender.CoreWebView2
        # Replace pywebview's automatic window.open handler before any business page.
        core.NewWindowRequested -= self.native.browser.on_new_window_request
        core.NewWindowRequested += self._new_window
        core.DownloadStarting += self._download
        core.WebResourceRequested += self._document_requested
        self.engine_ready.set()

    def _document_requested(self, sender, args):
        from Microsoft.Web.WebView2.Core import CoreWebView2WebResourceContext
        if args.ResourceContext != CoreWebView2WebResourceContext.Document:
            return
        url = str(args.Request.Uri)
        if url == 'about:blank' or url == self._internal_document or self.session.verify_document(url):
            return
        # Reject at the document-request boundary as well as the navigation
        # event: no replacement document is fetched from a stale READY port.
        args.Response = sender.Environment.CreateWebResourceResponse(None, 409, 'Identity mismatch',
            "Content-Type: text/plain\r\nContent-Security-Policy: default-src 'none'\r\n")
        if self.session.same_origin(url):
            self._identity_rejected(url, phase='document-request')

    def _navigation_starting(self, sender, args):
        url = str(args.Uri)
        if url == 'about:blank' or url == self._internal_document:
            return
        if self.session.verify_document(url):
            return
        args.Cancel = True
        if self.session.same_origin(url):
            self._identity_rejected(url)
        else:
            self._external(url, bool(args.IsUserInitiated))

    def _new_window(self, sender, args):
        # Finish the native popup event before probing or starting another
        # navigation. WebView2 does not support event-handler reentrancy.
        args.Handled = True
        url = str(args.Uri)
        user_initiated = bool(args.IsUserInitiated)
        Thread(target=lambda: self._open_requested_window(url, user_initiated), daemon=True).start()

    def _open_requested_window(self, url, user_initiated):
        if self.session.verify_document(url):
            self.window.load_url(url)
            self.events.append({'action': 'same-origin-new-window', 'url': url})
        elif self.session.same_origin(url):
            self._identity_rejected(url)
        else:
            self._external(url, user_initiated)

    def _identity_rejected(self, url, phase='navigation'):
        self.events.append({'action': 'identity-rejected', 'url': url, 'state': self.session.last_result['state'], 'phase':phase})
        result = self.session.last_result
        Thread(target=lambda: self._show_runtime_page(result), daemon=True).start()

    @staticmethod
    def _internal_url(html):
        # NavigateToString reports this exact data URI in NavigationStarting.
        # Only the HTML generated by this application is admitted.
        return 'data:text/html;charset=utf-8;base64,' + base64.b64encode(html.encode('utf-8')).decode('ascii')

    def _show_runtime_page(self, result):
        html = runtime_page(result)
        self._internal_document = self._internal_url(html)
        self.window.load_html(html)

    def _external(self, url, user_initiated):
        allowed = user_initiated and external_destination(url)
        self.events.append({'action': 'external-open' if allowed else 'navigation-blocked', 'url': url[:2048]})
        if allowed:
            self.external_opener(url)

    def _download(self, sender, args):
        args.Cancel = True
        self.events.append({'action': 'download-blocked', 'url': str(args.DownloadOperation.Uri)})

    def _closing(self):
        self.events.append({'action': 'window-close', **self.session.close()})

    def refresh(self):
        result = self.session.check()
        if result['state'] == 'READY':
            self.window.load_url(self.session.selection.url)
        else:
            self._show_runtime_page(result)
        return result

    def start_service(self):
        result = self.session.start()
        if result['state'] == 'READY':
            self.window.load_url(self.session.selection.url)
        else:
            self._show_runtime_page(result)
        return result

    def show_runtime(self):
        result = self.session.check()
        self._show_runtime_page(result)
        return result

    def open_browser(self):
        result = self.session.check()
        if result['state'] == 'READY':
            current = self.window.get_current_url()
            self.external_opener(current if current and self.session.allows_document(current) else self.session.selection.url)
        else:
            self._show_runtime_page(result)

    def _choose(self, kind):
        import webview
        folder = kind == 'project_root'
        selected = self.window.create_file_dialog(webview.FileDialog.FOLDER if folder else webview.FileDialog.OPEN,
            allow_multiple=False, file_types=() if folder else ('JSON (*.json)',) if kind == 'profile_path' else ('Python (*.exe)',))
        if not selected:
            return
        updates = {kind: Path(selected[0]).resolve()}
        if folder:
            updates['profile_path'] = None
        self.session = DesktopSession(replace(self.session.selection, **updates), self.session.state_dir)
        self.refresh()

    def choose_project(self):
        self._choose('project_root')

    def choose_profile(self):
        self._choose('profile_path')

    def choose_python(self):
        self._choose('python')

    def run(self, after_open=None):
        import webview

        def opened():
            if not self.engine_ready.wait(25) or self.engine_error:
                self.engine_error = self.engine_error or 'WebView2 initialization timeout'
                self.events.append({'action': 'renderer-unavailable', 'error': self.engine_error})
                self.window.destroy()
                return
            self.refresh()
            if after_open is not None:
                after_open(self)

        # Persist only this client's browser state. Closing does not stop services.
        webview.start(opened, gui='edgechromium', debug=False, private_mode=False,
                      storage_path=str(self.session.state_dir / 'webview2'))
        return 1 if self.engine_error else 0


def main(argv=None):
    from desktop.startup import main as start
    return start(argv)


if __name__ == '__main__':
    raise SystemExit(main())
