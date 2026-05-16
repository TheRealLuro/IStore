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


def sample_thermals() -> dict[str, Any]:
    """Best-effort temperature + fan readings. psutil's sensors_*
    family is Linux-only (reads /sys/class/hwmon); on Windows we ask
    PowerShell for thermal zones via WMI. Returns empty lists rather
    than None so the FE can always iterate."""
    temps: list[dict[str, Any]] = []
    fans: list[dict[str, Any]] = []

    # Linux + macOS path (when available)
    sensors = getattr(psutil, "sensors_temperatures", None)
    if sensors is not None:
        try:
            data = sensors(fahrenheit=False) or {}
            for chip, readings in data.items():
                for r in readings:
                    temps.append({
                        "source": chip,
                        "label": r.label or chip,
                        "current_c": r.current,
                        "high_c": r.high,
                        "critical_c": r.critical,
                    })
        except Exception:
            pass
    fan_fn = getattr(psutil, "sensors_fans", None)
    if fan_fn is not None:
        try:
            data = fan_fn() or {}
            for chip, readings in data.items():
                for r in readings:
                    fans.append({
                        "source": chip,
                        "label": r.label or chip,
                        "rpm": r.current,
                    })
        except Exception:
            pass

    # Windows fallback via WMI thermal zones (Kelvin → °C).
    if os.name == "nt" and not temps:
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue | "
                 "Select-Object -Property InstanceName,CurrentTemperature | "
                 "ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=4,
            )
            if out.returncode == 0 and out.stdout.strip():
                import json as _json
                data = _json.loads(out.stdout)
                if isinstance(data, dict):
                    data = [data]
                for d in data:
                    raw = d.get("CurrentTemperature")
                    if raw is None:
                        continue
                    # WMI reports tenths of a Kelvin.
                    celsius = (raw / 10.0) - 273.15
                    temps.append({
                        "source": "ACPI",
                        "label": (d.get("InstanceName") or "thermal_zone").split("\\")[-1],
                        "current_c": round(celsius, 1),
                        "high_c": None,
                        "critical_c": None,
                    })
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass

    return {"temps": temps, "fans": fans}


def sample_network() -> dict[str, Any]:
    """Active NICs + traffic counters. Filters out loopback and
    docker/wsl/veth interfaces by default to keep the dashboard
    readable; raw counters are returned so the FE can compute deltas
    between polls if it wants live rates."""
    nics: list[dict[str, Any]] = []
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        counters = psutil.net_io_counters(pernic=True)
    except Exception:
        return {"interfaces": [], "totals": {"bytes_sent": 0, "bytes_recv": 0}}

    skip_prefixes = ("lo", "docker", "veth", "br-", "vEthernet", "WSL",
                     "Loopback", "isatap.", "Teredo")
    for name, addr_list in addrs.items():
        if any(name.startswith(p) or p in name for p in skip_prefixes):
            continue
        stat = stats.get(name)
        if stat is None or not stat.isup:
            continue
        ipv4 = next((a.address for a in addr_list
                     if hasattr(a, "family") and getattr(a.family, "name", "") == "AF_INET"), None)
        if not ipv4:
            # fall back to first non-MAC addr
            ipv4 = next((a.address for a in addr_list if "." in (a.address or "")), None)
        mac = next((a.address for a in addr_list
                    if a.address and (":" in a.address or "-" in a.address) and "." not in a.address), None)
        c = counters.get(name)
        nics.append({
            "name": name,
            "ipv4": ipv4,
            "mac": mac,
            "is_up": bool(stat.isup),
            "speed_mbps": int(stat.speed) if stat.speed else None,
            "bytes_sent": int(c.bytes_sent) if c else 0,
            "bytes_recv": int(c.bytes_recv) if c else 0,
            "packets_sent": int(c.packets_sent) if c else 0,
            "packets_recv": int(c.packets_recv) if c else 0,
        })
    try:
        total = psutil.net_io_counters(pernic=False)
        totals = {"bytes_sent": int(total.bytes_sent), "bytes_recv": int(total.bytes_recv)}
    except Exception:
        totals = {"bytes_sent": 0, "bytes_recv": 0}
    return {"interfaces": nics, "totals": totals}


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
    """Inspect torch's CUDA capabilities. Returns:
      - device dict when torch is CUDA-built AND devices are present
      - None when torch is CPU-only OR no devices are visible to torch
        (so the caller continues to other probes — driver-level GPUs
        still show up via nvidia-smi/WMI even when torch can't use them)"""
    try:
        import torch
    except ImportError:
        return None
    # CPU-only torch builds have torch.version.cuda == None. Falling
    # through lets us still report the hardware via vendor tools.
    if not getattr(torch.version, "cuda", None):
        return None
    try:
        if not torch.cuda.is_available():
            return None
    except Exception:
        return None
    try:
        count = torch.cuda.device_count()
        if count == 0:
            return None
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
                "vendor": "NVIDIA",
                "total_memory_bytes": int(total) if total else None,
                "allocated_memory_bytes": int(allocated) if allocated else None,
            })
        return {
            "available": True,
            "backend": "torch.cuda",
            "devices": devices,
            "notes": [],
        }
    except Exception:
        return None


def _nvidia_smi_path() -> str | None:
    """nvidia-smi search across the platforms that hide it from PATH.
    Windows ships it under System32 (always on PATH in cmd, sometimes
    not when Python is launched via PowerShell-without-profile)."""
    p = shutil.which("nvidia-smi")
    if p:
        return p
    if os.name == "nt":
        candidate = r"C:\Windows\System32\nvidia-smi.exe"
        if os.path.isfile(candidate):
            return candidate
    return None


def _try_nvidia_smi() -> dict[str, Any] | None:
    """Driver-level NVIDIA enumeration via shell. Used when torch is
    CPU-only or GPU passthrough isn't configured in this container."""
    path = _nvidia_smi_path()
    if not path:
        return None
    try:
        out = subprocess.run(
            [path,
             "--query-gpu=index,name,memory.total,memory.used,utilization.gpu,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    devices = []
    driver_version: str | None = None
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            devices.append({
                "index": int(parts[0]),
                "name": parts[1],
                "vendor": "NVIDIA",
                "total_memory_bytes": int(parts[2]) * 1024 * 1024,
                "used_memory_bytes": int(parts[3]) * 1024 * 1024,
                "utilization_percent": int(parts[4]),
                "driver_version": parts[5] if len(parts) >= 6 else None,
            })
            if len(parts) >= 6 and not driver_version:
                driver_version = parts[5]
        except ValueError:
            continue
    if not devices:
        return None
    return {
        "available": True,
        "backend": "nvidia-smi",
        "devices": devices,
        "driver_version": driver_version,
        "notes": [],
    }


def _try_wmi_windows() -> list[dict[str, Any]]:
    """WMI Win32_VideoController for any vendor — picks up the iGPU
    (Intel Arc, AMD APU) plus discrete NVIDIA/AMD/Intel ARC. We use
    PowerShell to avoid taking a `pywin32`/`wmi` dependency. Returns
    a possibly-empty list; never raises."""
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | "
             "Select-Object -Property Name,AdapterRAM,DriverVersion,VideoProcessor | "
             "ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=6,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if out.returncode != 0 or not out.stdout.strip():
        return []
    import json as _json
    try:
        data = _json.loads(out.stdout)
    except _json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    rows: list[dict[str, Any]] = []
    for d in data:
        name = (d.get("Name") or "").strip()
        if not name:
            continue
        vendor = "NVIDIA" if "nvidia" in name.lower() \
            else "AMD" if any(s in name.lower() for s in ("amd", "radeon")) \
            else "Intel" if any(s in name.lower() for s in ("intel", "arc")) \
            else "Unknown"
        rows.append({
            "name": name,
            "vendor": vendor,
            "total_memory_bytes": int(d.get("AdapterRAM") or 0) or None,
            "driver_version": (d.get("DriverVersion") or None),
            "video_processor": (d.get("VideoProcessor") or None),
        })
    return rows


def _try_lspci_linux() -> list[dict[str, Any]]:
    """`lspci -nnk` is broadly available on Linux base images and
    surfaces "VGA compatible controller" / "3D controller" rows for
    every GPU on the host PCIe bus (even when no kernel module is
    loaded). Useful inside containers without GPU passthrough so the
    dashboard reads "GPU present but inaccessible to this process"
    instead of the misleading "no GPU detected"."""
    if not shutil.which("lspci"):
        return []
    try:
        out = subprocess.run(
            ["lspci", "-nn"], capture_output=True, text=True, timeout=4,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if out.returncode != 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in out.stdout.splitlines():
        ll = line.lower()
        if "vga compatible" not in ll and "3d controller" not in ll and "display controller" not in ll:
            continue
        # Format: "01:00.0 VGA compatible controller [0300]: NVIDIA Corporation GA106M [GeForce RTX 3060 Mobile] [10de:2520] (rev a1)"
        try:
            _, rest = line.split(":", 1)
            _, payload = rest.split(":", 1)
            name = payload.strip()
        except ValueError:
            name = line.strip()
        vendor = "NVIDIA" if "nvidia" in ll \
            else "AMD" if "amd" in ll or "radeon" in ll or "advanced micro" in ll \
            else "Intel" if "intel" in ll \
            else "Unknown"
        rows.append({
            "name": name,
            "vendor": vendor,
            "total_memory_bytes": None,
            "driver_version": None,
        })
    return rows


def _try_openvino() -> dict[str, Any] | None:
    """OpenVINO Core enumerates every accelerator the Intel runtime
    can target — CPU, GPU (Intel Arc / iGPU), NPU. Only available when
    the `openvino` package is installed. Returns a uniform device list
    so the dashboard can show iGPUs + NPUs alongside CUDA cards."""
    try:
        from openvino import Core  # type: ignore
    except Exception:
        return None
    try:
        core = Core()
        names = list(core.available_devices)
    except Exception:
        return None
    if not names:
        return None
    devices: list[dict[str, Any]] = []
    for n in names:
        # Device names are 'CPU' | 'GPU' | 'GPU.0' | 'NPU' | etc.
        try:
            full = core.get_property(n, "FULL_DEVICE_NAME")
        except Exception:
            full = n
        vendor = (
            "Intel" if "intel" in str(full).lower() or n in ("GPU", "NPU") else
            "AMD" if "amd" in str(full).lower() else
            "NVIDIA" if "nvidia" in str(full).lower() else
            "Unknown"
        )
        kind = (
            "NPU" if n.startswith("NPU") else
            "iGPU/Arc" if n.startswith("GPU") else
            "CPU"
        )
        devices.append({
            "name": str(full),
            "vendor": vendor,
            "kind": kind,
            "openvino_device": n,
        })
    return {"backend": "openvino", "devices": devices}


def _try_torch_xpu() -> dict[str, Any] | None:
    """Intel Extension for PyTorch (IPEX-XPU) exposes the Intel iGPU /
    Arc as a torch device named 'xpu'. Only available with IPEX installed
    AND a supported Intel GPU."""
    try:
        import torch
    except ImportError:
        return None
    if not hasattr(torch, "xpu"):
        return None
    try:
        if not torch.xpu.is_available():
            return None
        count = torch.xpu.device_count()
    except Exception:
        return None
    if count == 0:
        return None
    devices: list[dict[str, Any]] = []
    for i in range(count):
        try:
            name = torch.xpu.get_device_name(i)
        except Exception:
            name = f"xpu:{i}"
        devices.append({"index": i, "name": name, "vendor": "Intel", "kind": "iGPU/Arc"})
    return {"backend": "torch.xpu", "devices": devices}


def _try_pnp_npu_windows() -> list[dict[str, Any]]:
    """Intel NPU (Meteor Lake / Ultra-series) registers as a PnP device
    with caption containing "AI Boost". Check Win32_PnPEntity. Returns
    [] when not on Windows or no NPU present."""
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance -ClassName Win32_PnPEntity -ErrorAction SilentlyContinue | "
             "Where-Object { $_.Caption -match 'AI Boost|Neural|NPU' } | "
             "Select-Object -Property Caption,DeviceID,Manufacturer | "
             "ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=6,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if out.returncode != 0 or not out.stdout.strip():
        return []
    import json as _json
    try:
        data = _json.loads(out.stdout)
    except _json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    rows: list[dict[str, Any]] = []
    for d in data:
        cap = d.get("Caption") or "?"
        rows.append({
            "name": cap,
            "vendor": d.get("Manufacturer") or "Intel",
            "kind": "NPU",
            "device_id": d.get("DeviceID"),
        })
    return rows


def probe_accelerators_full() -> dict[str, Any]:
    """One-shot full accelerator enumeration. Designed to run inside the
    ML worker at startup — captures everything torch.cuda, IPEX-XPU,
    OpenVINO, and host-OS tools can see, packages it into a single dict
    the worker pings in its heartbeat metadata. The API container reads
    this dict to surface a real GPU + NPU view on the Hardware tab even
    when it can't probe locally."""
    notes: list[str] = []
    devices: list[dict[str, Any]] = []

    # Primary path: torch.cuda inside the worker.
    cuda = _try_torch_cuda()
    if cuda:
        for d in cuda.get("devices", []):
            devices.append({**d, "kind": "CUDA"})
        primary_backend = "torch.cuda"
    else:
        primary_backend = None
        smi = _try_nvidia_smi()
        if smi:
            for d in smi.get("devices", []):
                devices.append({**d, "kind": "CUDA"})
            primary_backend = "nvidia-smi"

    # Add IPEX-XPU devices (Intel Arc / iGPU exposed to torch)
    xpu = _try_torch_xpu()
    if xpu:
        for d in xpu.get("devices", []):
            devices.append({**d, "inaccessible": False})
        if primary_backend is None:
            primary_backend = "torch.xpu"

    # Add OpenVINO-visible devices (NPU + Intel iGPU regardless of IPEX)
    ov = _try_openvino()
    if ov:
        existing_names = {d.get("name", "") for d in devices}
        for d in ov.get("devices", []):
            if d.get("openvino_device") == "CPU":
                continue  # CPU is not interesting on the GPU tab
            if d.get("name") in existing_names:
                continue
            devices.append(d)
        if primary_backend is None:
            primary_backend = "openvino"

    # WMI + PnP fallbacks (Windows host) for adapters none of the above caught
    if os.name == "nt":
        wmi_rows = _try_wmi_windows()
        npu_rows = _try_pnp_npu_windows()
        seen = {d.get("name", "") for d in devices}
        for row in wmi_rows + npu_rows:
            if row.get("name") in seen:
                continue
            devices.append({**row, "inaccessible": True})
            seen.add(row.get("name", ""))

    # Linux fallback for cases the above missed (e.g. AMD ROCm)
    if os.name == "posix":
        for row in _try_lspci_linux():
            if any(d.get("name") == row["name"] for d in devices):
                continue
            devices.append({**row, "inaccessible": True})

    if not devices:
        return {"available": False, "backend": None, "devices": [], "notes": notes}

    if primary_backend is None:
        primary_backend = "wmi" if os.name == "nt" else "lspci"

    return {
        "available": True,
        "backend": primary_backend,
        "devices": devices,
        "notes": notes,
    }


async def _try_from_worker_heartbeats() -> dict[str, Any] | None:
    """Read the latest ml-worker heartbeat and return its GPU enumeration.
    Lets the API container surface CUDA / NPU / Arc info even when the
    API process can't probe directly (no torch, no nvidia-smi, no GPU
    passthrough). The worker writes this every 30 s; we look back at
    most ~2 minutes."""
    try:
        from datetime import timedelta
        from backend.db import SessionLocal
        from backend.models import WorkerHeartbeat
        from sqlalchemy import select
    except Exception:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=120)
    try:
        async with SessionLocal() as session:
            row = (
                await session.execute(
                    select(WorkerHeartbeat)
                    .where(WorkerHeartbeat.last_seen > cutoff)
                    .order_by(WorkerHeartbeat.last_seen.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
    except Exception:
        return None
    if row is None or not row.extra_metadata:
        return None
    gpu = row.extra_metadata.get("gpu")
    if not isinstance(gpu, dict) or not gpu.get("devices"):
        return None
    # Mark provenance so the dashboard can show "(via ml-worker)".
    gpu = dict(gpu)
    gpu["source"] = f"ml-worker ({row.worker_id.split('/')[0]})"
    return gpu


async def sample_gpu_async() -> dict[str, Any]:
    """Async sample_gpu that reads worker_heartbeats first.

    Order:
      1. worker_heartbeats — most authoritative; the worker actually
         loads the models, so its torch.cuda view IS the source of truth.
      2. Local torch.cuda — when API ↔ worker live in the same process.
      3. nvidia-smi, OpenVINO, WMI / lspci — vendor fallbacks.

    Anything found via 2-4 is merged after 1 with deduplication by name.
    """
    notes: list[str] = []
    primary = await _try_from_worker_heartbeats()
    if primary:
        return primary  # the worker's view is complete; don't muddle it
    # Fall back to the synchronous local-only probe.
    return sample_gpu()


def sample_gpu() -> dict[str, Any]:
    """Combine probes so the dashboard surfaces every GPU the host has,
    even when this process can't use it.

    Order:
      1. torch.cuda — gives us memory + utilization when torch is a
         CUDA build with devices visible.
      2. nvidia-smi — driver-level NVIDIA enumeration; works even when
         torch is CPU-only.
      3. WMI (Windows) / lspci (Linux) — vendor + name for every video
         adapter, so iGPUs and non-NVIDIA discrete GPUs also show up.
      4. OpenVINO + WMI PnP — Intel Arc + NPU.

    A `notes` array carries human-readable hints when the configured
    state is suboptimal (e.g. "GPU detected but PyTorch is CPU-only —
    reinstall with the right CUDA wheel for inference acceleration").
    """
    notes: list[str] = []

    # Probe 1: torch.cuda (full memory stats when available).
    torch_result = _try_torch_cuda()
    if torch_result:
        return torch_result

    # If torch is imported but CPU-only, note it so the user knows
    # there's a wheel mismatch even when no GPU is detected here.
    try:
        import torch  # noqa
        if not getattr(torch.version, "cuda", None):
            notes.append(
                "PyTorch is installed but CPU-only (torch.version.cuda is None). "
                "If you have an NVIDIA GPU, reinstall torch with the matching CUDA "
                "wheel: `pip install --index-url https://download.pytorch.org/whl/cu128 "
                "torch torchvision`."
            )
    except ImportError:
        pass

    # Probe 2: nvidia-smi (driver-level NVIDIA enumeration).
    smi_result = _try_nvidia_smi()

    # Probe 3: WMI / lspci (every video adapter regardless of vendor).
    fallback_rows = _try_wmi_windows() or _try_lspci_linux()

    # Probe 4: OpenVINO devices (Intel iGPU / Arc / NPU) + NPU PnP.
    ov = _try_openvino()
    npu_rows = _try_pnp_npu_windows()

    # Compose. Prefer nvidia-smi rows when available (they carry memory
    # numbers); fall back to WMI/lspci rows for non-NVIDIA adapters,
    # and surface OpenVINO + NPU as their own entries when present.
    devices: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    if smi_result:
        for d in smi_result["devices"]:
            devices.append({**d, "kind": "CUDA"})
            seen_names.add(d["name"])
    for row in fallback_rows:
        if row["name"] in seen_names:
            continue
        devices.append({
            "index": len(devices),
            "inaccessible": True,
            "kind": "GPU",
            **row,
        })
        seen_names.add(row["name"])
    if ov:
        for d in ov["devices"]:
            if d.get("openvino_device") == "CPU":
                continue
            if d.get("name") in seen_names:
                # Annotate the existing row: OpenVINO can target it.
                for existing in devices:
                    if existing.get("name") == d.get("name"):
                        existing["openvino_device"] = d.get("openvino_device")
                continue
            devices.append({**d, "inaccessible": False})
            seen_names.add(d["name"])
    for row in npu_rows:
        if row["name"] in seen_names:
            continue
        devices.append({**row, "inaccessible": False})
        seen_names.add(row["name"])

    if not devices:
        return {
            "available": False, "backend": None, "devices": [],
            "notes": notes,
        }

    # When fallback found something but torch+nvidia-smi didn't, add a
    # specific hint depending on what's going on.
    if smi_result:
        backend = "nvidia-smi"
        notes.append(
            "GPU visible via nvidia-smi but not to PyTorch — the ml-worker "
            "container likely needs a CUDA-enabled torch build."
        )
    else:
        backend = "wmi" if os.name == "nt" else "lspci"
        notes.append(
            "GPU detected at the OS/PCIe level, but this process can't access "
            "it. Inside Docker that usually means GPU passthrough isn't "
            "configured (`deploy.resources.reservations.devices` + nvidia-"
            "container-toolkit). On bare metal it means the NVIDIA driver "
            "tools aren't on PATH."
        )

    return {
        "available": True,
        "backend": backend,
        "devices": devices,
        "notes": notes,
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


_MINIO_SAMPLE_CACHE: dict[str, Any] = {"at": 0.0, "value": None}
_MINIO_SAMPLE_TTL = 60.0  # seconds — the admin overlay polls every 6s
_MINIO_OBJECT_CAP = 20_000  # per-bucket counting cap (see notes below)


async def sample_minio() -> dict[str, Any]:
    """Per-bucket object count + total byte size.

    Previously this walked every object in every bucket synchronously,
    on the event loop, on every admin-overlay poll. With a few
    thousand objects per bucket that was a 20-30 second blocker that
    made the entire admin console feel frozen on first paint.

    Three changes:
      1. Cache the result for 60 s — admin polls every 6 s, but
         "how many bytes in the served bucket" doesn't change at
         the second granularity that justifies re-listing.
      2. Cap per-bucket iteration at 20 000 objects; report
         `objects: 20000` + `capped: true` past that so the dashboard
         shows ">= 20000" instead of stalling on a runaway bucket.
      3. Run the actual sync `client.list_objects` work in a worker
         thread so the event loop stays responsive.
    """
    import time
    now = time.monotonic()
    cached = _MINIO_SAMPLE_CACHE.get("value")
    if cached and now - _MINIO_SAMPLE_CACHE.get("at", 0.0) < _MINIO_SAMPLE_TTL:
        return cached

    try:
        from backend.storage import storage
    except Exception:
        return {"reachable": False, "error": "storage module unavailable"}

    def _sample_sync():
        try:
            client = storage.client
            buckets = []
            bucket_names = [
                settings.minio_bucket_originals,
                settings.minio_bucket_served,
                settings.minio_bucket_faces,
                settings.minio_bucket_quarantine,
            ]
            models_bucket = getattr(settings, "minio_bucket_models", None)
            if models_bucket:
                bucket_names.append(models_bucket)
            for name in bucket_names:
                try:
                    if not client.bucket_exists(name):
                        buckets.append({
                            "name": name, "exists": False,
                            "objects": 0, "size_bytes": 0, "capped": False,
                        })
                        continue
                    count = 0
                    size = 0
                    capped = False
                    for obj in client.list_objects(name, recursive=True):
                        count += 1
                        size += int(obj.size or 0)
                        if count >= _MINIO_OBJECT_CAP:
                            capped = True
                            break
                    buckets.append({
                        "name": name, "exists": True,
                        "objects": count, "size_bytes": size, "capped": capped,
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

    import asyncio
    result = await asyncio.to_thread(_sample_sync)
    _MINIO_SAMPLE_CACHE["value"] = result
    _MINIO_SAMPLE_CACHE["at"] = now
    return result


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


# ---------- Worker heartbeats ----------


async def list_workers(stale_after_seconds: int = 120) -> list[dict[str, Any]]:
    """Read every row from worker_heartbeats and tag the alive vs.
    stale ones. Anything older than `stale_after_seconds` (default
    120 s = 4× the recommended 30s heartbeat interval) is treated as
    dead but still surfaced so an operator sees "worker X died at T"."""
    from datetime import timedelta
    try:
        from backend.db import SessionLocal
        from backend.models import WorkerHeartbeat
        from sqlalchemy import select
    except Exception:
        return []
    now = datetime.now(timezone.utc)
    stale_at = now - timedelta(seconds=stale_after_seconds)
    try:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen.desc())
                )
            ).scalars().all()
    except Exception:
        return []
    return [
        {
            "worker_id": w.worker_id,
            "kind": w.kind,
            "hostname": w.hostname,
            "pid": w.pid,
            "version": w.version,
            "last_seen": w.last_seen.isoformat(),
            "alive": w.last_seen > stale_at,
            "seconds_since_seen": max(0, int((now - w.last_seen).total_seconds())),
            "metadata": w.extra_metadata,
        }
        for w in rows
    ]


async def list_model_runs() -> list[dict[str, Any]]:
    """Latest model_runs row per (model_id, worker_id) — i.e. the
    most-recent state transition. We use a window function so the
    table can grow without an explicit cleanup pass."""
    try:
        from backend.db import SessionLocal
        from sqlalchemy import text as _text
    except Exception:
        return []
    try:
        async with SessionLocal() as session:
            rows = (
                await session.execute(_text("""
                    SELECT DISTINCT ON (model_id, worker_id)
                        model_id, worker_id, state, device,
                        memory_allocated_bytes, last_used_at,
                        created_at, metadata
                    FROM model_runs
                    ORDER BY model_id, worker_id, created_at DESC
                """))
            ).all()
    except Exception:
        return []
    return [
        {
            "model_id": r.model_id,
            "worker_id": r.worker_id,
            "state": r.state,
            "device": r.device,
            "memory_allocated_bytes": r.memory_allocated_bytes,
            "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
            "created_at": r.created_at.isoformat(),
            "metadata": r.metadata,
        }
        for r in rows
    ]


# ---------- Health rollup + user activity ----------


async def sample_user_activity() -> dict[str, Any]:
    """Active-user counts for the admin overlay banner.

    "Active in last 24 h / 7 d" is computed from the audit log
    (any event whose `user_id` is non-null). Total user count comes
    from the users table.

    We compute the cutoff timestamps in Python and bind them as
    `timestamptz` rather than building a SQL INTERVAL — asyncpg's
    INTERVAL binding refuses str literals (it wants a `timedelta`)
    and SQLAlchemy's `cast(..., INTERVAL)` lowers to a parameterized
    cast, not the literal. A Python timestamp keeps it portable."""
    from datetime import timedelta
    try:
        from backend.db import SessionLocal
        from backend.models import AuditLog, User
        from sqlalchemy import distinct, func as sa_func, select
    except Exception:
        return {"total_users": 0, "active_24h": 0, "active_7d": 0}
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)
    try:
        async with SessionLocal() as session:
            total = (
                await session.execute(select(sa_func.count(User.id)))
            ).scalar_one() or 0
            active_24h = (
                await session.execute(
                    select(sa_func.count(distinct(AuditLog.user_id)))
                    .where(
                        AuditLog.user_id.is_not(None),
                        AuditLog.created_at > cutoff_24h,
                    )
                )
            ).scalar_one() or 0
            active_7d = (
                await session.execute(
                    select(sa_func.count(distinct(AuditLog.user_id)))
                    .where(
                        AuditLog.user_id.is_not(None),
                        AuditLog.created_at > cutoff_7d,
                    )
                )
            ).scalar_one() or 0
            return {
                "total_users": int(total),
                "active_24h": int(active_24h),
                "active_7d": int(active_7d),
            }
    except Exception:
        return {"total_users": 0, "active_24h": 0, "active_7d": 0}


def rollup_health(*, db_pool: dict, redis_info: dict, minio_info: dict,
                  disks: list[dict], queue_depth: int) -> dict[str, Any]:
    """Combine subsystem probes into a single ok|warn|error verdict
    the admin overlay can render as a colored banner.

    Each check carries its own state + short detail. Overall is the
    worst of the parts. Thresholds are conservative so a healthy box
    looks green at a glance and red genuinely means "go look now"."""
    checks: list[dict[str, Any]] = []

    db_ok = db_pool.get("reachable", True)  # default True so an unknown shape doesn't false-fail
    if "error" in db_pool:
        db_ok = False
    checks.append({
        "name": "Database",
        "state": "ok" if db_ok else "error",
        "detail": (
            f"{db_pool.get('checked_out', 0)}/{db_pool.get('size', '?')} connections"
            if db_ok else (db_pool.get("error") or "unreachable")
        ),
    })

    redis_ok = redis_info.get("reachable", False)
    checks.append({
        "name": "Redis",
        "state": "ok" if redis_ok else "error",
        "detail": (
            f"{redis_info.get('dbsize', 0)} keys, {redis_info.get('queue_depth', '?')} queued"
            if redis_ok else (redis_info.get("error") or "unreachable")
        ),
    })

    minio_ok = minio_info.get("reachable", False)
    checks.append({
        "name": "Object storage",
        "state": "ok" if minio_ok else "error",
        "detail": (
            f"{sum(b.get('objects', 0) or 0 for b in minio_info.get('buckets', []))} objects"
            if minio_ok else (minio_info.get("error") or "unreachable")
        ),
    })

    if disks:
        worst = max(disks, key=lambda d: d.get("percent", 0))
        pct = worst.get("percent", 0)
        if pct >= 95:
            disk_state = "error"
        elif pct >= 85:
            disk_state = "warn"
        else:
            disk_state = "ok"
        checks.append({
            "name": "Disk",
            "state": disk_state,
            "detail": f"{worst.get('mountpoint', '?')} at {pct}% ({worst.get('device', '?')})",
        })

    if queue_depth is not None and queue_depth >= 0:
        if queue_depth >= 200:
            qstate = "error"
        elif queue_depth >= 50:
            qstate = "warn"
        else:
            qstate = "ok"
        checks.append({
            "name": "Job queue",
            "state": qstate,
            "detail": f"{queue_depth} pending",
        })

    rank = {"ok": 0, "warn": 1, "error": 2}
    overall = "ok"
    for c in checks:
        if rank[c["state"]] > rank[overall]:
            overall = c["state"]
    return {"overall": overall, "checks": checks}


def list_configured_models() -> list[dict[str, Any]]:
    """Build a registry from `settings` of every ML model neuthek talks
    to. Status is "configured" — actually-loaded state lives inside the
    ml-worker container and would need a worker→API IPC to surface;
    that's tracked in C8.2 (model_runs table) and out of scope here.

    VRAM accounting (`vram_resident_mb` + `vram_per_inference_mb`)
    powers the admin Models tab's "VRAM for N concurrent users"
    estimator. Numbers are conservative defaults sourced from each
    model card + measured peak in our profile harness; tweak them
    when you swap variants. The math the API does is:

        total_for_n = resident + n * per_inference

    `resident` is the weights cost — paid once per ml-worker that
    has the model loaded. `per_inference` is the activation peak for
    a single forward pass; serial workers (current default) only pay
    this once at a time, but a multi-worker fleet pays it per worker.
    The dashboard's "Concurrent users" input doubles as a fleet
    sizing dial: 1 user ≈ 1 worker (today's reality), 10 users ≈ 10
    workers if you scale out.

    All numbers are MB. Defaults assume fp16 where the model
    supports it; bump if you're running fp32 / no-quant variants.
    """
    items: list[dict[str, Any]] = [
        {
            "id": "clip",
            "label": "OpenCLIP (embeddings + concept vocab)",
            "name": settings.clip_model_name,
            "variant": settings.clip_pretrained,
            "role": "image embeddings",
            "enabled": True,
            "vram_resident_mb": 1200,
            "vram_per_inference_mb": 400,
        },
        {
            "id": "florence2",
            "label": "Florence-2 (captions / regions / OD)",
            "name": settings.caption_model_name,
            "variant": None,
            "role": "image captioning",
            "enabled": True,
            "vram_resident_mb": 1700,
            "vram_per_inference_mb": 900,
        },
        {
            "id": "caption_fallback",
            "label": "BLIP fallback (no GPU profile)",
            "name": getattr(settings, "caption_fallback_model_name", "Salesforce/blip-image-captioning-base"),
            "variant": None,
            "role": "image captioning (fallback)",
            "enabled": True,
            "vram_resident_mb": 900,
            "vram_per_inference_mb": 300,
        },
        {
            "id": "qwen",
            "label": "Qwen2.5-Instruct (rewriter + doc summary)",
            "name": settings.rewriter_model_name,
            "variant": None,
            "role": "summary rewriter",
            "enabled": True,
            "vram_resident_mb": 3000,
            "vram_per_inference_mb": 500,
        },
        {
            "id": "internvl2",
            "label": "InternVL2-4B (heavy VLM)",
            "name": getattr(settings, "heavy_vlm_model", "OpenGVLab/InternVL2-4B"),
            "variant": None,
            "role": "rich VLM description",
            "enabled": bool(getattr(settings, "heavy_vlm_enabled", False)),
            "vram_resident_mb": 8500,
            "vram_per_inference_mb": 1500,
        },
        {
            "id": "retinaface",
            "label": "RetinaFace (buffalo_l)",
            "name": "buffalo_l",
            "variant": None,
            "role": "face detection",
            "enabled": True,
            "vram_resident_mb": 300,
            "vram_per_inference_mb": 200,
        },
        {
            "id": "arcface",
            "label": "ArcFace embeddings",
            "name": "buffalo_l/w600k_r50",
            "variant": None,
            "role": "face embeddings",
            "enabled": True,
            "vram_resident_mb": 500,
            "vram_per_inference_mb": 200,
        },
    ]
    return items
