"""Portable catalog/help/doctor/preview with explicit provider execution routes."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys


def parser():
    result = argparse.ArgumentParser(description='显式 runtime/profile 的工具目录；默认无外部调用')
    result.add_argument('--runtime-root', type=Path, required=True)
    result.add_argument('--project-root', type=Path)
    result.add_argument('--profile', type=Path, help='项目根内的非秘密 profile JSON')
    sub = result.add_subparsers(dest='command', required=True)
    sub.add_parser('catalog', help='读取统一机器目录，不检查账户')
    help_cmd = sub.add_parser('help', help='读取一个能力的真实入口与限制')
    help_cmd.add_argument('capability')
    doctor_cmd = sub.add_parser('doctor', help='本地文件/依赖/环境键存在性；不读取密钥、不联网')
    doctor_cmd.add_argument('--capability')
    read_cmd = sub.add_parser('read', help='实际 DuoPlus 只读 API；读取选定进程凭据并联网，不是 preview')
    read_cmd.add_argument('provider', choices=['duoplus'])
    read_cmd.add_argument('operation', choices=['devices', 'info', 'status', 'apps', 'installed-apps'])
    read_cmd.add_argument('--payload', type=Path, required=True)
    for command in ('preview', 'execute'):
        cmd = sub.add_parser(command, help='离线请求计划' if command == 'preview' else '消费既有精确授权；可能收费或安装 APP')
        cmd.add_argument('provider', choices=['lingshi', 'tikhub', 'duoplus'])
        cmd.add_argument('operation')
        cmd.add_argument('--payload', type=Path, required=True)
        if command == 'execute': cmd.add_argument('--authorization', type=Path, required=True)
    analyze = sub.add_parser('analyze-tikhub', help='分析规范化本地样本；不是市场总量')
    analyze.add_argument('--snapshot', type=Path, required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path: sys.path.insert(0, str(root))
    from shared_platform import capability_runtime as runtime
    try:
        if runtime.checked_path(args.runtime_root, '.').resolve() != root:
            raise runtime.ToolContextError('runtime-root must match this exact launcher location')
        runtime.validate_runtime(root)
        if args.command in {'catalog', 'help'}:
            result = runtime.catalog(root)
            if args.command == 'help':
                result = next((r for r in result['skills']+result['tools'] if r['id'] == args.capability), None)
                if result is None: raise runtime.ToolContextError('unknown capability ID')
        else:
            if args.project_root is None or args.profile is None:
                raise runtime.ToolContextError('--project-root and --profile are required; personal config fallback is disabled')
            context = runtime.load_profile(args.project_root, args.profile)
            if args.command == 'doctor': result = runtime.doctor(root, context, args.capability)
            elif args.command == 'analyze-tikhub':
                from modules.tools.tikhub import analyze
                result = analyze(runtime.read_object(runtime.checked_path(context['project_root'], args.snapshot)))
            else:
                payload = runtime.read_object(runtime.checked_path(context['project_root'], args.payload))
                if args.command == 'preview': result = runtime.preview(root, context, args.provider, args.operation, payload)
                elif args.command == 'read': result = runtime.read_provider(context, args.provider, args.operation, payload)
                else:
                    authorization = runtime.read_object(runtime.checked_path(context['project_root'], args.authorization))
                    result = runtime.execute(context, args.provider, args.operation, payload, authorization)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1 if result.get('ok') is False else 0
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
        print(json.dumps({'ok': False, 'error_type': type(error).__name__, 'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
