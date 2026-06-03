import importlib

import pytest


def fresh():
    import backend.vision.vram_manager as m
    return importlib.reload(m)


def test_register_and_touch_orders_lru():
    m = fresh()
    m.register("a", est_gb=1.0, evictable=True)
    m.register("b", est_gb=1.0, evictable=True)
    m.mark_resident("a"); m.touch("a")
    m.mark_resident("b"); m.touch("b")
    m.touch("a")  # a now most-recently-used
    assert m._lru_evictable_order() == ["b", "a"]


def test_ensure_room_evicts_until_fit():
    m = fresh()
    freed = []
    state = {"free": 2.0}
    m._free_gb_hook = lambda: state["free"]
    m._empty_cache_hook = lambda: None  # no reserved pool to reclaim in the test

    def fake_evict(key):
        freed.append(key)
        state["free"] += 1.5
    m._evict_hook = fake_evict
    m.register("a", est_gb=1.5, evictable=True); m.mark_resident("a"); m.touch("a")
    m.register("b", est_gb=1.5, evictable=True); m.mark_resident("b"); m.touch("b")
    m.ensure_room(3.0, margin=0.0)
    assert freed == ["a"]


def test_ensure_room_empty_cache_first_avoids_evict():
    m = fresh()
    state = {"emptied": False}
    # Looks full (1 GB) until the reserved cache is emptied, then 9 GB free.
    m._free_gb_hook = lambda: 9.0 if state["emptied"] else 1.0
    m._empty_cache_hook = lambda: state.__setitem__("emptied", True)
    evicted = []
    m._evict_hook = lambda k: evicted.append(k)
    m.register("a", est_gb=1.0, evictable=True); m.mark_resident("a"); m.touch("a")
    m.ensure_room(4.0, margin=0.0)
    assert state["emptied"] is True
    assert evicted == []  # empty_cache freed enough; no model evicted


def test_ensure_room_raises_when_no_evictables():
    m = fresh()
    m._free_gb_hook = lambda: 0.5
    m._empty_cache_hook = lambda: None
    m._evict_hook = lambda k: None
    with pytest.raises(m.VramPressure):
        m.ensure_room(4.0, margin=0.0)


def test_run_on_gpu_retries_then_cpu_fallback():
    m = fresh()
    m._free_gb_hook = lambda: 10.0
    m._empty_cache_hook = lambda: None
    calls = {"n": 0}

    class FakeOOM(Exception):
        pass
    m._oom_errors = (FakeOOM,)

    def fn(*, _force_cpu=False):
        calls["n"] += 1
        if not _force_cpu:
            raise FakeOOM()
        return "cpu-result"

    out = m.run_on_gpu(fn, est_gb=1.0)
    assert out == "cpu-result"
    assert calls["n"] == 3  # try, retry after evict, cpu fallback
