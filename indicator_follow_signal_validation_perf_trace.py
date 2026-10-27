"""Temporary, import-time opt-in V5 timings. Durations are inclusive, not additive.

No values, identities or arbitrary payloads are collected. OFF decorators return
the original function. Only the daemon touches the append-only capped file.
"""
from functools import wraps
import json
import os
from pathlib import Path
from queue import Queue, Empty, Full
from threading import Thread, current_thread
from time import perf_counter, thread_time

ENABLED = os.environ.get('KIWOOM_SIGNAL_VALIDATION_V5_PERF') == '1'
MAX_BYTES = 9 * 1024 * 1024


class Collector:
    def __init__(self, capacity=2048):
        self.queue = Queue(maxsize=capacity)
        self.path = Path(__file__).resolve().parent / 'runtime/kiwoom_v5_perf_trace.jsonl'
        self.dropped = 0
        self.failed = False
        self.last = {}

    def offer(self, record):
        try:
            if not self.failed:
                self.queue.put_nowait(record)
        except Full:
            self.dropped += 1
        except Exception:
            pass

    def write_batch(self, batch):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            size = self.path.stat().st_size if self.path.exists() else 0
            data = ''.join(json.dumps(r, separators=(',', ':')) + '\n' for r in batch).encode('utf-8')
            if size + len(data) > MAX_BYTES:
                self.failed = True
                return
            with self.path.open('ab') as stream:
                stream.write(data)
        except Exception:
            self.failed = True

    def run(self):
        while not self.failed:
            batch = [self.queue.get()]
            for _ in range(63):
                try:
                    batch.append(self.queue.get_nowait())
                except Empty:
                    break
            self.write_batch(batch)


_collector = None
if ENABLED:
    try:
        _collector = Collector()
        Thread(target=_collector.run, name='kiwoom-v5-perf-writer', daemon=True).start()
    except Exception:
        _collector = None


def emit(event, started, owner=None, **counts):
    try:
        c = _collector
        if c is None or c.failed:
            return
        now = perf_counter()
        key = (event, id(owner), counts.get('cache_hit'), counts.get('preview'))
        previous, suppressed = c.last.get(key, (float('-inf'), 0))
        if now - previous < .05:
            c.last[key] = (previous, suppressed + 1)
            return
        if len(c.last) >= 2048:
            c.last.clear()
        c.last[key] = (now, 0)
        c.offer(dict(event=event, elapsed_ms=round((now-started)*1000, 3),
                     pid=os.getpid(), thread=current_thread().name, monotonic=now,
                     instance_id=id(owner), suppressed=suppressed, dropped=c.dropped, **counts))
    except Exception:
        pass


class PhaseProbe:
    def __init__(self, owner, counts):
        self.owner = owner
        self.counts = counts
        self.wall = perf_counter()
        self.cpu = thread_time()
        self.totals = {}

    def update(self, **counts):
        try:
            self.counts.update(counts)
        except Exception:
            pass

    def checkpoint(self, event, **counts):
        try:
            wall = perf_counter()
            cpu = thread_time()
            payload = dict(self.counts)
            payload.update(counts)
            payload['thread_cpu_ms'] = round((cpu - self.cpu) * 1000, 3)
            emit(event, self.wall, self.owner, **payload)
            self.wall = wall
            self.cpu = cpu
        except Exception:
            pass

    def begin(self):
        try:
            return perf_counter(), thread_time()
        except Exception:
            return None

    def end(self, event, started):
        try:
            if started is None:
                return
            wall = perf_counter() - started[0]
            cpu = thread_time() - started[1]
            total = self.totals.setdefault(event, [0.0, 0.0])
            total[0] += wall
            total[1] += cpu
        except Exception:
            pass

    def flush(self, event, **counts):
        try:
            wall, cpu = self.totals.pop(event)
            payload = dict(self.counts)
            payload.update(counts)
            payload['thread_cpu_ms'] = round(cpu * 1000, 3)
            emit(event, perf_counter() - wall, self.owner, **payload)
        except Exception:
            pass


def phase(owner=None, **counts):
    if not ENABLED or _collector is None or _collector.failed:
        return None
    try:
        return PhaseProbe(owner, counts)
    except Exception:
        return None


def stage(event, owner=None):
    def decorate(function):
        if not ENABLED:
            return function
        @wraps(function)
        def measured(*args, **kwargs):
            target = owner if owner is not None else (args[0] if args else None)
            started = None
            cache = None
            try:
                started = perf_counter()
                cache = getattr(target, '_static_chart_cache', None)
            except Exception:
                pass
            try:
                return function(*args, **kwargs)
            finally:
                try:
                    if started is not None:
                        counts = {}
                        if target is not None:
                            counts['bars'] = len(getattr(target, '_candles', ()))
                            counts['markers'] = len(getattr(target, '_markers',
                                getattr(target, '_signal_marker_entries', ())))
                            counts['cycles'] = len(getattr(target, '_completed_cycle_average_lines',
                                getattr(target, '_completed_cycles', ())))
                            counts['preview'] = bool(getattr(target, '_cycle_label_preview_active', False))
                            frame_owner = getattr(target, '_input_frame_owner', None)
                            counts['window_id'] = id(frame_owner if frame_owner is not None else target)
                            bounds = getattr(target, '_visible_index_bounds', None)
                            if callable(bounds):
                                lo, hi = bounds()
                                counts['visible_bars'] = max(0, hi-lo)
                            if event == 'canvas.paint':
                                counts['cache_hit'] = cache is not None and cache is getattr(target, '_static_chart_cache', None)
                        emit(event, started, target, **counts)
                except Exception:
                    pass
        return measured
    return decorate
