"""Test fixture evidence writer: retain every row without retaining history in RAM."""
import json
from pathlib import Path
import shutil
from threading import RLock


class FixtureRecordLog:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.lock = RLock()
        self.counts = {'requests': 0, 'responses': 0}
        for kind in self.counts:
            with (self.folder / (kind + '.json')).open('xb') as stream:
                stream.write(b'[\n]\n')

    def _append(self, kind, row):
        # Stage one complete row before touching the retained JSON array.
        # iterencode/copy chunks avoid json.dumps(all_previous_responses).
        with self.lock:
            stage = self.folder / (kind + '-next.tmp')
            try:
                with stage.open('x', encoding='utf-8') as stream:
                    for chunk in json.JSONEncoder(ensure_ascii=True, separators=(',', ':')).iterencode(row):
                        stream.write(chunk)
                    stream.write('\n')
                with (self.folder / (kind + '.json')).open('r+b') as target, stage.open('rb') as source:
                    target.seek(-2, 2)  # replace only the closing ]\n
                    if self.counts[kind]:
                        target.write(b',\n')
                    shutil.copyfileobj(source, target, length=64 * 1024)
                    target.write(b']\n')
                    target.truncate()
                self.counts[kind] += 1
            finally:
                if stage.exists():
                    stage.unlink()

    def request(self, method, path):
        with self.lock:
            request_id = self.counts['requests'] + 1
            self._append('requests', {'method': method, 'path': path, 'request_id': request_id})
            return request_id

    def response(self, status, payload, path, request_id):
        with self.lock:
            if not isinstance(request_id, int) or not 1 <= request_id <= self.counts['requests']:
                raise ValueError('Response requires one retained request identity')
            self._append('responses', {'status': status, 'payload': payload,
                                       'path': path, 'request_id': request_id})
