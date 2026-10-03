"""Explicit desktop selection and the existing runtime launcher's identity gate."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from threading import RLock
from urllib.parse import urlsplit

from scripts import product_publication_runtime as runtime
from shared_platform.runtime_identity import read_profile


STATUS_LABELS = {
    'READY': '已连接', 'STOPPED': '服务未启动', 'PORT_IN_USE': '端口被其他程序占用',
    'WRONG_SERVICE': '连接到其他服务', 'SOURCE_MISMATCH': '工程或版本不一致',
    'ASSET_MISSING': '页面资源不完整', 'ASSET_MISMATCH': '页面资源版本不一致',
    'CONFIG_MISMATCH': '运行配置不一致', 'DATA_PROFILE_MISMATCH': '数据目录不一致',
    'DEPENDENCY_UNAVAILABLE': '服务依赖不完整', 'UNKNOWN': '运行身份尚未确认',
    'PROJECT_REQUIRED': '请选择工程', 'PROJECT_INVALID': '工程目录不完整',
    'PROFILE_REQUIRED': '请选择运行档案', 'PROFILE_INVALID': '运行档案不完整',
    'PYTHON_REQUIRED': '启动服务需要指定 Python', 'CONFIG_REQUIRED': '工程配置尚未准备',
}


@dataclass(frozen=True)
class DesktopSelection:
    project_root: Path | None
    profile_path: Path | None
    python: Path | None
    port: int = 8765
    page: str = '/'

    def __post_init__(self):
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('端口必须为 1–65535')
        parsed = urlsplit(self.page)
        if not self.page.startswith('/') or self.page.startswith('//') or parsed.scheme or parsed.netloc or '\\' in self.page:
            raise ValueError('页面必须是当前服务内的路径，例如 /product-workspace')
        for name in ('project_root', 'profile_path', 'python'):
            value = getattr(self, name)
            if value is not None:
                if not value.is_absolute():
                    raise ValueError(f'{name} 必须使用绝对路径')
                object.__setattr__(self, name, value.expanduser().resolve())

    @property
    def origin(self):
        return f'http://127.0.0.1:{self.port}'

    @property
    def url(self):
        return self.origin + self.page


class DesktopSession:
    def __init__(self, selection: DesktopSelection, state_dir: Path):
        self.selection = selection
        self.state_dir = state_dir.resolve()
        self._operation_lock = RLock()
        self.last_result = self._result('UNKNOWN')

    def _result(self, state, detail=None):
        return {'state': state, 'healthy': state == 'READY', 'label': STATUS_LABELS.get(state, state),
                'detail': detail, 'project_root': str(self.selection.project_root) if self.selection.project_root else None,
                'profile_path': str(self.selection.profile_path) if self.selection.profile_path else None,
                'url': self.selection.url, 'business_execution_verified': False}

    def validate_selection(self):
        root, profile = self.selection.project_root, self.selection.profile_path
        if root is None:
            return self._result('PROJECT_REQUIRED', '在“连接”菜单选择工程文件夹。')
        if not all((root / name).is_file() for name in ('main.py', 'web/index.html', 'shared_platform/runtime_identity.py')):
            return self._result('PROJECT_INVALID', '请选择包含 main.py、web 和 shared_platform 的完整 OrbitHive 工程。')
        if profile is None or not profile.is_file():
            return self._result('PROFILE_REQUIRED', '选择工程的非秘密运行档案；可用 --create-profile 生成。')
        if profile.name.lower() in {'settings.json', 'tiktok_tokens.json', 'lingshi.local.json', '.env'}:
            return self._result('PROFILE_INVALID', '请选择非秘密的运行档案，不能选择凭据或设置文件。')
        try:
            if profile.stat().st_size > 65536:
                raise ValueError('profile too large')
            raw = json.loads(profile.read_text(encoding='utf-8'))
            paths = [raw['settings_path'], *raw['stores'].values()]
            if not all(isinstance(p, str) and Path(p).is_absolute() for p in paths):
                raise ValueError('profile paths must be absolute')
            if read_profile(root, profile) is None:
                raise ValueError('profile fields missing')
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return self._result('PROFILE_INVALID', '运行档案需要 profile_id、配置路径和四类数据位置，均使用绝对路径。')
        return None

    def spec(self):
        return runtime.service_specs(root=self.selection.project_root,
            executable=str(self.selection.python) if self.selection.python else sys.executable,
            profile_path=self.selection.profile_path, product_port=self.selection.port)[0]

    def check(self):
        with self._operation_lock:
            return self._check()

    def _check(self):
        invalid = self.validate_selection()
        if invalid:
            self.last_result = invalid
        else:
            row = runtime.probe_service(self.spec())
            self.last_result = {**self._result(str(row['state']), row.get('detail')), 'runtime': row}
        return self.last_result

    def start(self):
        with self._operation_lock:
            return self._start()

    def _start(self):
        current = self.check()
        if current['state'] != 'STOPPED':
            return current
        executable = self.selection.python
        if executable is None or not executable.is_file() or (getattr(sys, 'frozen', False) and executable == Path(sys.executable).resolve()):
            self.last_result = self._result('PYTHON_REQUIRED', '在“连接”菜单选择此工程使用的 Python；已运行的服务不需要此设置。')
            return self.last_result
        # The runtime profile is an independent expectation, not a store redirect.
        # Requiring this exact file prevents core.config's historical fallback.
        settings = self.selection.project_root / 'config/settings.json'
        profile = read_profile(self.selection.project_root, self.selection.profile_path)
        if not settings.is_file() or str(settings.resolve()) != profile['settings_path']:
            self.last_result = self._result('CONFIG_REQUIRED', '显式启动前，请准备所选工程的 config/settings.json，并使运行档案指向该文件。APP 不读取、复制或创建凭据。')
            return self.last_result
        result = runtime.start_runtime(specs=(self.spec(),), runtime_dir=self.state_dir / 'service-logs')
        current = self.check()
        self.last_result = {**current, 'start_result': result}
        return self.last_result

    def same_origin(self, url):
        try:
            parsed = urlsplit(url)
            return (parsed.scheme == 'http'
                    and parsed.hostname == '127.0.0.1' and parsed.port == self.selection.port
                    and parsed.username is None and parsed.password is None)
        except ValueError:
            return False

    def allows_document(self, url):
        return self.last_result['state'] == 'READY' and self.same_origin(url)

    def verify_document(self, url):
        # A former READY response does not authorize a replacement at the port.
        return self.same_origin(url) and self.check()['state'] == 'READY'

    def close(self):
        # A desktop window does not own durable backend work or its lifecycle.
        return {'background_services_stopped': False, 'business_requests_sent': 0}


def external_destination(url):
    """External web links may leave the app; local services require identity checks."""
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and bool(parsed.hostname) and parsed.username is None
                and parsed.password is None and parsed.hostname not in {'localhost', '127.0.0.1', '::1'}
                and not parsed.hostname.startswith('127.') and not any(c in url for c in '\r\n\x00'))
    except ValueError:
        return False
