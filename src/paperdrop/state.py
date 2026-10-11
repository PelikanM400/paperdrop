"""Local state: what was published, what was applied, and when.

The state file is what makes a repeated run cheap: when the identity of the
current image is unchanged, a run only checks the desktop state instead of
downloading or re-applying anything.
"""
from __future__ import annotations

import datetime
import pathlib
import zoneinfo

from .util import read_json, write_json

VERSION = 1


def now_iso(tz_name='UTC'):
    try:
        zone = zoneinfo.ZoneInfo(tz_name)
    except Exception:  # pragma: no cover - unknown tz name
        zone = datetime.timezone.utc
    return datetime.datetime.now(zone).isoformat(timespec='seconds')


class State:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.data = {'version': VERSION, 'last_success': None, 'last_attempt': None}

    def load(self):
        stored = read_json(self.path, None)
        if isinstance(stored, dict) and stored.get('version') == VERSION:
            for key in ('last_success', 'last_attempt'):
                value = stored.get(key)
                if isinstance(value, dict):
                    self.data[key] = value
        return self

    def save(self):
        write_json(self.path, self.data)
        return self

    @property
    def last_success(self):
        return self.data.get('last_success') or None

    @property
    def last_attempt(self):
        return self.data.get('last_attempt') or None

    def succeeded_with(self, identity, cache_path=None):
        """True when the recorded success is for this exact image identity."""
        success = self.last_success
        if not success or success.get('identity') != identity:
            return False
        if cache_path is not None:
            recorded = success.get('cache_path')
            if recorded and recorded != str(cache_path):
                return False
        return True

    def record_success(self, **fields):
        fields.setdefault('finished_at', now_iso())
        self.data['last_success'] = fields
        self.data['last_attempt'] = dict(fields, ok=True)
        self.save()
        return self.data['last_success']

    def record_failure(self, reason, **fields):
        fields.update({'ok': False, 'reason': reason, 'finished_at': now_iso()})
        self.data['last_attempt'] = fields
        self.save()
        return self.data['last_attempt']
