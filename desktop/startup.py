"""Portable client CLI. The selected engineering project stays outside the bundle."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from desktop.session import DesktopSelection, DesktopSession


def absolute_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise argparse.ArgumentTypeError('请使用绝对路径')
    return path.resolve()


def parser():
    result = argparse.ArgumentParser(description='OrbitHive 桌面客户端：连接明确工程的同一 Web 入口。')
    result.add_argument('--project-root', type=absolute_path)
    result.add_argument('--profile', type=absolute_path, help='非秘密的运行身份档案')
    result.add_argument('--python', type=absolute_path, help='仅显式启动后端时需要')
    result.add_argument('--port', type=int, default=8765)
    result.add_argument('--page', default='/', help='同一入口内的深链接路径')
    result.add_argument('--state-dir', type=absolute_path)
    result.add_argument('--status', action='store_true', help='只检查连接，不启动窗口或后端')
    result.add_argument('--result', type=absolute_path, help='把本次状态写到新 JSON 文件')
    result.add_argument('--create-profile', action='store_true', help='按明确位置生成新运行档案，不生成配置或数据库')
    result.add_argument('--profile-id')
    result.add_argument('--settings-path', type=absolute_path)
    result.add_argument('--catalog-store', type=absolute_path)
    result.add_argument('--report-store', type=absolute_path)
    result.add_argument('--workbench-store', type=absolute_path)
    result.add_argument('--ozon-dir', type=absolute_path)
    result.add_argument('--diagnostics', type=absolute_path, help='只读的实际 WebView2 诊断输出目录；不自动操作业务')
    result.add_argument('--exit-after-diagnostics', action='store_true')
    result.add_argument('--diagnostic-proxy-port', type=int, help='隔离验收专用：只允许所选服务与自有 loopback 代理，禁止数据库和其他网络')
    result.add_argument('--diagnostic-product-images', action='store_true', help='只读诊断：在明确商品深链接中打开图片页签并截图')
    result.add_argument('--diagnostic-workspace-readonly', action='store_true', help='隔离验收：检查当前商品四页签和当前队列的批量只读刷新')
    return result


def emit(result, destination=None):
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('x', encoding='utf-8') as stream:
            stream.write(text + '\n')
    if sys.stdout:
        print(text)


def create_profile(args):
    if not args.profile or not args.profile_id or not args.profile_id.strip():
        raise ValueError('生成运行档案需要 --profile 和 --profile-id')
    if args.profile.name.lower() in {'settings.json', 'tiktok_tokens.json', 'lingshi.local.json', '.env'}:
        raise ValueError('运行档案不能使用凭据或设置文件名')
    paths = {'catalog': args.catalog_store, 'reports_release': args.report_store,
             'workbench': args.workbench_store, 'ozon': args.ozon_dir}
    if args.settings_path is None or any(path is None for path in paths.values()):
        raise ValueError('请提供 --settings-path、--catalog-store、--report-store、--workbench-store 和 --ozon-dir')
    value = {'profile_id': args.profile_id.strip(), 'settings_path': str(args.settings_path),
             'stores': {name: str(path) for name, path in paths.items()}}
    # Exclusive creation: never replace an existing profile, settings or DB.
    args.profile.parent.mkdir(parents=True, exist_ok=True)
    with args.profile.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return {'state': 'PROFILE_CREATED', 'path': str(args.profile), 'configuration_read': False,
            'database_opened': False}


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    if args.exit_after_diagnostics and not args.diagnostics:
        cli.error('--exit-after-diagnostics 需要 --diagnostics')
    if args.diagnostic_proxy_port and not args.diagnostics:
        cli.error('--diagnostic-proxy-port 需要 --diagnostics')
    if args.diagnostic_product_images and not args.diagnostics:
        cli.error('--diagnostic-product-images 需要 --diagnostics')
    if args.diagnostic_workspace_readonly and (not args.diagnostics or not args.diagnostic_proxy_port):
        cli.error('--diagnostic-workspace-readonly 需要 --diagnostics 和隔离代理')
    try:
        if args.create_profile:
            emit(create_profile(args), args.result)
            return 0
        root = args.project_root
        if root is None and not getattr(sys, 'frozen', False):
            root = Path(__file__).resolve().parents[1]
        state = args.state_dir or Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'OrbitHive/desktop'
        selection = DesktopSelection(root, args.profile, args.python, args.port, args.page)
        session = DesktopSession(selection, state)
        if args.diagnostic_proxy_port:
            from desktop.diagnostic_boundary import install
            install(selection, args.diagnostic_proxy_port, args.diagnostics)
        if args.status:
            result = session.check()
            emit(result, args.result)
            return 0 if result['healthy'] else 1
        from desktop.orbit_desktop_webview import DesktopWindow
        window = DesktopWindow(session, hidden=args.exit_after_diagnostics)
        callback = None
        diagnostic_result = {}
        if args.diagnostics:
            from desktop.renderer_diagnostics import record
            def callback(app):
                diagnostic_result.update(record(app, args.diagnostics, close=args.exit_after_diagnostics,
                                                product_images=args.diagnostic_product_images,
                                                workspace_readonly=args.diagnostic_workspace_readonly))
        code = window.run(callback)
        if code:
            raise RuntimeError(window.engine_error)
        if args.exit_after_diagnostics and not diagnostic_result.get('ok'):
            emit({'state':'DIAGNOSTICS_FAILED','detail':diagnostic_result.get('error')},args.result)
            return 2
        if args.result:
            emit({'state': 'CLOSED', **session.close()}, args.result)
        return 0
    except (OSError, ValueError, ImportError, RuntimeError) as error:
        message = ('客户端未能启动：' + str(error) + '\n请核对工程、运行档案和可写状态目录；'
                   '渲染器需要 Microsoft Edge WebView2 Runtime。源码启动需要安装桌面依赖，'
                   '构建产物无需外部 Python 即可连接已运行服务。详见随附桌面使用说明。')
        emit({'state': 'DESKTOP_UNAVAILABLE', 'detail': message}, args.result)
        if os.name == 'nt' and not args.exit_after_diagnostics and not args.status and not args.create_profile:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'OrbitHive', 0x10)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
