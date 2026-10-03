"""Disabled-by-default, fixture-only lifecycle for a private worker pipe.

This is not the worker entrypoint. It cannot open a port or select production
paths. The parent owns the task/lease and service pipe handle; the child only
receives its one pipe handle. Child exit never triggers automatic restart.
"""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import threading

from shared_platform.worker_category_pipe import BoundWorkerCategoryPipe


MARKER = 'orbit-worker-pipe-offline-fixture/v1'


def _child_main(target, connection, args):
    try:
        target(connection, *args)
    finally:
        connection.close()


class OfflineWorkerPipeSupervisor:
    def __init__(self, bridge, *, task_id, worker_id, lease_token,
                 isolation_root, worker_target, worker_args=(),
                 offline_fixture_enabled=False):
        if not offline_fixture_enabled:
            raise ValueError('worker pipe lifecycle is disabled outside an offline fixture')
        root = Path(isolation_root)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError('isolated worker fixture marker required')
        try:
            marker = (root / '.orbit-worker-pipe-fixture').read_text(encoding='utf-8')
        except OSError:
            marker = None
        if marker != MARKER:
            raise ValueError('isolated worker fixture marker required')
        for path in (bridge.engine.store.path, bridge.domain_store.path):
            resolved = Path(path).resolve()
            if root.resolve() not in resolved.parents:
                raise ValueError('worker fixture stores must be inside isolation root')
        if not callable(worker_target):
            raise ValueError('worker child target required')
        self.bound = BoundWorkerCategoryPipe(bridge, task_id=task_id,
            worker_id=worker_id, lease_token=lease_token)
        self.root = root
        self.target = worker_target
        self.args = tuple(worker_args)
        self.ctx = multiprocessing.get_context('spawn')
        self.process = None
        self.thread = None
        self.service_connection = None
        self._parent_child_connection = None
        self._stop = threading.Event()
        self._state = 'created'
        self._frames = 0
        self._dispatched = 0
        self._lock = threading.Lock()

    def _serve(self):
        connection = self.service_connection
        try:
            while not self._stop.is_set():
                if not connection.poll(0.1):
                    if self.process is not None and not self.process.is_alive():
                        break
                    continue
                if self._stop.is_set():
                    break
                try:
                    response = self.bound.serve_once(connection)
                except (EOFError, OSError):
                    break
                with self._lock:
                    self._frames += 1
                    try:
                        if json.loads(response).get('dispatched_this_call'):
                            self._dispatched += 1
                    except (ValueError, TypeError):
                        pass
        finally:
            connection.close()

    def start(self):
        if self._state != 'created':
            raise ValueError('a worker fixture supervisor can start only once')
        service, child = self.ctx.Pipe()
        self.service_connection = service
        self._parent_child_connection = child
        self.process = self.ctx.Process(target=_child_main,
                                        args=(self.target, child, self.args))
        try:
            self.process.start()
        except Exception:
            service.close()
            self.service_connection = None
            self._state = 'start_failed'
            raise
        finally:
            # The parent must not retain the child end. Otherwise EOF after a
            # child crash is masked by its own duplicate handle.
            child.close()
        self.thread = threading.Thread(target=self._serve,
                                       name='offline-worker-category-pipe', daemon=True)
        try:
            self.thread.start()
        except Exception:
            self._stop.set()
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(10)
            service.close()
            self._state = 'start_failed'
            raise
        self._state = 'running'
        return self.receipt()

    def wait(self, timeout=10):
        if self.process is not None:
            self.process.join(timeout)
        if self.thread is not None:
            self.thread.join(timeout)
        if self._state == 'running' and self.process is not None and not self.process.is_alive() and not self.thread.is_alive():
            self._state = 'exited' if self.process.exitcode == 0 else 'child_failed'
        return self.receipt()

    def stop(self, timeout=10):
        if self._state == 'created':
            self._state = 'stopped'
            return self.receipt()
        self._stop.set()
        if self.process is not None and self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout)
        if self.thread is not None:
            self.thread.join(timeout)
        if self.thread is not None and self.thread.is_alive():
            self._state = 'stop_pending'
        else:
            self._state = 'stopped'
        return self.receipt()

    def receipt(self):
        with self._lock:
            frames = self._frames
            dispatched = self._dispatched
        return {'state': self._state,
                'child_pid': self.process.pid if self.process else None,
                'child_exitcode': self.process.exitcode if self.process and not self.process.is_alive() else None,
                'child_alive': bool(self.process and self.process.is_alive()),
                'service_alive': bool(self.thread and self.thread.is_alive()),
                'parent_retains_child_handle': bool(self._parent_child_connection and not self._parent_child_connection.closed),
                'responses_sent': frames,
                'dispatched_in_sent_responses': dispatched,
                'domain_outcome_inferred_from_child_exit': False,
                'automatic_restart': False, 'production_activation': False}
