"""Cross-platform system + hardware introspection (todo §1.3 / C8 admin overlay).

All functions return JSON-friendly dicts with deliberately conservative
shapes — when a probe fails (psutil refused, GPU absent, container
restricted) the field is set to None rather than the call raising. The
admin dashboard renders "—" for None values.

Functions exposed:
  - sample_cpu()       CPU model, count, percent, load avg
  - sample_memory()    Total / used / available bytes
  - sample_disks()     Per-mount stats
  - sample_gpu()       NVIDIA-first via torch.cuda or nvidia-smi shell
  - sample_processes() Top N by CPU; the API process + ML worker
  - sample_uptime()    Boot time + app uptime
  - sample_redis()     Ping + memory_usage + queue depth
  - sample_minio()     Bucket sizes via storage client
  - sample_db_pool()   Async engine pool stats

Nothing here is performance-critical: every endpoint that calls these
is admin-only and not on a hot path.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

import psutil

from backend.config import settings

logger = logging.getLogger(__name__)

# Stamp the moment the module was imported as a proxy for "API process
# uptime". Per-worker process uptime would need an env var seeded at
# Dockerfile entrypoint — not strictly accurate for hot-reload, but
# good enough for the dashboard.
_PROCESS_BOOT_TS = time.time()


# ---------- CPU ----------


def _cpu_brand() -> str | None:
    """Best-effort cross-platform CPU model string. None when nothing
    short of platform-specific code would give a sensible answer."""
    name = platform.processor() or ""
    if name and name.lower() not in {"", "unknown"}:
        return name
    # Linux: read /proc/cpuinfo
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def sample_cpu() -> dict[str, Any]:
    # `percent=None` returns the value from the prior call; first call
    # returns 0 (no baseline). To keep the dashboard's first paint
    # meaningful we take a 200 ms snapshot — short enough that the
    # admin endpoint stays responsive.
    pct = psutil.cpu_percent(interval=0.2)
    per_core = psutil.cpu_percent(interval=None, percpu=True)
    load_avg: tuple[float, float, float] | None
    try:
        load_avg = psutil.getloadavg()  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        load_avg = None
    freq = None
    try:
        f = psutil.cpu_freq()
        if f is not None:
            freq = {"current_mhz": round(f.current, 1) if f.current else None,
                    "max_mhz": round(f.max, 1) if f.max else None}
    except (NotImplementedError, OSError):
        freq = None
    return {
        "brand": _cpu_brand(),
        "logical_cores": psutil.cpu_count(logical=True),
        "physical_cores": psutil.cpu_count(logical=False),
        "percent": pct,
        "per_core_percent": per_core,
        "load_avg_1_5_15": list(load_avg) if load_avg else None,
        "freq": freq,
    }


# ---------- Memory ----------


def sample_memory() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return {
        "total_bytes": vm.total,
        "available_bytes": vm.available,
        "used_bytes": vm.used,
        "percent": vm.percent,
        "swap_total_bytes": sw.total,
        "swap_used_bytes": sw.used,
    }


# ---------- Disks ----------


def sample_disks() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception:
        parts = []
    for p in parts:
        # Skip pseudo-mounts (procfs, devfs, etc.) that disk_usage()
        # would either fail on or return useless zero rows.
        if p.fstype in {"squashfs", "tmpfs", "devtmpfs", "proc", "sysfs", "overlay"}:
            continue
        try:
            u = shutil.disk_usage(p.mountpoint)
        except (OSError, PermissionError):
            continue
        out.append({
            "device": p.device,
            "mountpoint": p.mountpoint,
            "fstype": p.fstype,
            "total_bytes": u.total,
            "used_bytes": u.used,
            "free_bytes": u.free,
            "percent": round(u.used / u.total * 100, 1) if u.total else 0,
        })
    return out


# ---------- GPU ----------


def _try_torch_cuda() -> dict[str, Any] | None:
    """Use torch's CUDA introspection if torch is available *and* CUDA
    is built. Returns None to signal "fall through to nvidia-smi"."""
    try:
        import torch
    except ImportError:
        return None
    try:
        if not torch.cuda.is_available():
            return {"available": False, "backend": "torch", "devices": []}
    except Exception:
        return None
    try:
        count = torch.cuda.device_count()
        devices = []
        for i in range(count):
            name = torch.cuda.get_device_name(i)
            props = torch.cuda.get_device_properties(i)
            total = getattr(props, "total_memory", None)
            allocated = None
            try:
                allocated = torch.cuda.memory_allocated(i)
            except Exception:
                pass
            devices.append({
                "index": i,
                "name": name,
                "total_memory_bytes": int(total) if total else None,
                "allocated_memory_bytes": int(allocated) if allocated else None,
            })
        return {"available": True, "backend": "torch.cuda", "devices": devices}
    except Exception:
        return None


def _try_nvidia_smi() -> dict[str, Any] | None:
    """Shell out to nvidia-smi as a fallback when torch isn't loaded
    in the API process (the user's split-container setup has ML in a
    sibling worker). Returns None if the binary isn't on PATH."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,name,memory.total,memory.used,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    devices = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            devices.append({
                "index": int(parts[0]),
                "name": parts[1],
                # nvidia-smi reports MiB; multiply by 1024*1024 for bytes.
                "total_memory_bytes": int(parts[2]) * 1024 * 1024,
                "used_memory_bytes": int(parts[3]) * 1024 * 1024,
                "utilization_percent": int(parts[4]),
            })
        except ValueError:
            continue
    return {"available": bool(devices), "backend": "nvidia-smi", "devices": devices}


def sample_gpu() -> dict[str, Any]:
    return _try_torch_cuda() or _try_nvidia_smi() or {
        "available": False, "backend": None, "devices": [],
    }


# ---------- Processes ----------


def sample_processes(top: int = 12) -> list[dict[str, Any]]:
    """Top N processes by CPU%. Includes the current API process and
    will show ml-worker if it's running in the same OS namespace.
    Skips kernel/idle/system-broker processes that psutil can't
    cleanly attribute. RAM is RSS (resident set)."""
    rows: list[dict[str, Any]] = []
    # First pass: prime cpu_percent so the next read isn't 0.0.
    for p in psutil.process_iter(["pid", "name"]):
        try:
            p.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(0.2)
    # Second pass: collect.
    for p in psutil.process_iter(["pid", "name", "username", "memory_info", "cmdline"]):
        try:
            cpu = p.cpu_percent(interval=None)
            mem_rss = p.info["memory_info"].rss if p.info["memory_info"] else 0
            name = p.info["name"] or "?"
            cmdline = " ".join(p.info["cmdline"] or [])[:200]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        kind = "system"
        lower = (name + " " + cmdline).lower()
        if "uvicorn" in lower or "neuthek" in lower or "fastapi" in lower:
            kind = "api"
        elif "worker" in lower:
            kind = "ai"
        elif "postgres" in lower or "minio" in lower or "redis" in lower:
            kind = "data"
        rows.append({
            "pid": p.info["pid"],
            "name": name,
            "username": p.info.get("username") or "",
            "cpu_percent": round(cpu, 1),
            "memory_rss_bytes": int(mem_rss),
            "kind": kind,
            "cmdline": cmdline,
        })
    # Stable sort by CPU desc, then RSS desc.
    rows.sort(key=lambda r: (r["cpu_percent"], r["memory_rss_bytes"]), reverse=True)
    return rows[:top]


# ---------- Uptime ----------


def sample_uptime() -> dict[str, Any]:
    now = time.time()
    try:
        host_boot = psutil.boot_time()
    except Exception:
        host_boot = None
    return {
        "process_uptime_seconds": max(0, int(now - _PROCESS_BOOT_TS)),
        "host_uptime_seconds": max(0, int(now - host_boot)) if host_boot else None,
        "process_boot_iso": datetime.fromtimestamp(_PROCESS_BOOT_TS, tz=timezone.utc).isoformat(),
        "host_boot_iso": (
            datetime.fromtimestamp(host_boot, tz=timezone.utc).isoformat()
            if host_boot else None
        ),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
    }


# ---------- Redis ----------


async def sample_redis() -> dict[str, Any]:
    """Ping + DBSIZE + queue depth. Returns the dict even on failure,
    with `reachable=False` so the dashboard can render the failure
    state instead of erroring out."""
    try:
        from backend.jobs import JOB_QUEUE_KEY, _client, queue_depth
    except Exception:
        return {"reachable": False, "error": "jobs module unavailable"}
    try:
        client = _client()
        await client.ping()
        info = await client.info(section="memory")
        dbsize = await client.dbsize()
        depth = await queue_depth()
        active = int(await client.scard("neuthek:jobs:active") or 0)
        return {
            "reachable": True,
            "memory_used_bytes": int(info.get("used_memory", 0)),
            "memory_peak_bytes": int(info.get("used_memory_peak", 0)),
            "dbsize": int(dbsize),
            "queue_key": JOB_QUEUE_KEY,
            "queue_depth": depth,
            "active_jobs": active,
        }
    except Exception as e:
        return {"reachable": False, "error": str(e)[:160]}


# ---------- MinIO ----------


async def sample_minio() -> dict[str, Any]:
    """Per-bucket object count + total byte size. List-and-sum is O(N)
    over the bucket — fine for a hundred thousand objects but if usage
    explodes we'd switch to a usage-cache table."""
    try:
        from backend.storage import storage
    except Exception:
        return {"reachable": False, "error": "storage module unavailable"}
    try:
        client = storage.client  # the underlying minio.Minio handle
        buckets = []
        for name in [
            settings.minio_bucket_originals,
            settings.minio_bucket_served,
            settings.minio_bucket_faces,
            settings.minio_bucket_quarantine,
        ]:
            try:
                if not client.bucket_exists(name):
                    buckets.append({"name": name, "exists": False, "objects": 0, "size_bytes": 0})
                    continue
                count = 0
                size = 0
                for obj in client.list_objects(name, recursive=True):
                    count += 1
                    size += int(obj.size or 0)
                buckets.append({
                    "name": name, "exists": True,
                    "objects": count, "size_bytes": size,
                })
            except Exception as e:
                buckets.append({"name": name, "exists": None, "error": str(e)[:160]})
        return {
            "reachable": True,
            "endpoint": settings.minio_endpoint,
            "buckets": buckets,
        }
    except Exception as e:
        return {"reachable": False, "error": str(e)[:160]}


# ---------- DB pool ----------


def sample_db_pool() -> dict[str, Any]:
    try:
        from backend.db import engine
    except Exception:
        return {"reachable": False, "error": "engine import failed"}
    try:
        pool = engine.pool
        # Async engine uses NullPool in tests and AsyncAdaptedQueuePool in
        # dev/prod. Both expose `status()`-ish methods but the attribute
        # names differ — guard each.
        info: dict[str, Any] = {
            "reachable": True,
            "size": getattr(pool, "size", lambda: None)() if callable(getattr(pool, "size", None)) else None,
            "checked_in": getattr(pool, "checkedin", lambda: None)() if callable(getattr(pool, "checkedin", None)) else None,
            "checked_out": getattr(pool, "checkedout", lambda: None)() if callable(getattr(pool, "checkedout", None)) else None,
            "overflow": getattr(pool, "overflow", lambda: None)() if callable(getattr(pool, "overflow", None)) else None,
        }
        return info
    except Exception as e:
        return {"reachable": False, "error": str(e)[:160]}


# ---------- Configured models ----------


def list_configured_models() -> list[dict[str, Any]]:
    """Build a registry from `settings` of every ML model neuthek talks
    to. Status is "configured" — actually-loaded state lives inside the
    ml-worker container and would need a worker→API IPC to surface;
    that's tracked in C8.2 (model_runs table) and out of scope here."""
    items: list[dict[str, Any]] = [
        {
            "id": "clip",
            "label": "OpenCLIP (embeddings + concept vocab)",
            "name": settings.clip_model_name,
            "variant": settings.clip_pretrained,
            "role": "image embeddings",
            "enabled": True,
        },
        {
            "id": "florence2",
            "label": "Florence-2 (captions / regions / OD)",
            "name": settings.caption_model_name,
            "variant": None,
            "role": "image captioning",
            "enabled": True,
        },
        {
            "id": "caption_fallback",
            "label": "BLIP fallback (no GPU profile)",
            "name": getattr(settings, "caption_fallback_model_name", "Salesforce/blip-image-captioning-base"),
            "variant": None,
            "role": "image captioning (fallback)",
            "enabled": True,
        },
        {
            "id": "qwen",
            "label": "Qwen2.5-Instruct (rewriter + doc summary)",
            "name": settings.rewriter_model_name,
            "variant": None,
            "role": "summary rewriter",
            "enabled": True,
        },
        {
            "id": "internvl2",
            "label": "InternVL2-4B (heavy VLM)",
            "name": getattr(settings, "heavy_vlm_model", "OpenGVLab/InternVL2-4B"),
            "variant": None,
            "role": "rich VLM description",
            "enabled": bool(getattr(settings, "heavy_vlm_enabled", False)),
        },
        {
            "id": "retinaface",
            "label": "RetinaFace (buffalo_l)",
            "name": "buffalo_l",
            "variant": None,
            "role": "face detection",
            "enabled": True,
        },
        {
            "id": "arcface",
            "label": "ArcFace embeddings",
            "name": "buffalo_l/w600k_r50",
            "variant": None,
            "role": "face embeddings",
            "enabled": True,
        },
    ]
    return items
