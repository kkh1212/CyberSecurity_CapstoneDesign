from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    psutil = None


def _query_nvidia_smi() -> dict[str, float | None]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return {"gpu_util_pct": None, "gpu_mem_mb": None}
    line = (completed.stdout or "").splitlines()[0:1]
    if not line:
        return {"gpu_util_pct": None, "gpu_mem_mb": None}
    parts = [part.strip() for part in line[0].split(",")]
    try:
        return {"gpu_util_pct": float(parts[0]), "gpu_mem_mb": float(parts[1])}
    except Exception:
        return {"gpu_util_pct": None, "gpu_mem_mb": None}


def _query_rocm_smi() -> dict[str, float | None]:
    try:
        completed = subprocess.run(
            ["rocm-smi", "--showuse", "--showmemuse"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return {"gpu_util_pct": None, "gpu_mem_mb": None}
    text = completed.stdout or ""
    util = None
    mem = None
    for token in text.replace("%", " ").replace("MiB", " ").split():
        try:
            value = float(token)
        except ValueError:
            continue
        if util is None:
            util = value
        elif mem is None:
            mem = value
            break
    return {"gpu_util_pct": util, "gpu_mem_mb": mem}


def _query_gpu() -> dict[str, float | None]:
    nvidia = _query_nvidia_smi()
    if nvidia["gpu_util_pct"] is not None or nvidia["gpu_mem_mb"] is not None:
        return {**nvidia, "gpu_monitor": "nvidia-smi"}
    rocm = _query_rocm_smi()
    if rocm["gpu_util_pct"] is not None or rocm["gpu_mem_mb"] is not None:
        return {**rocm, "gpu_monitor": "rocm-smi"}
    return {"gpu_util_pct": None, "gpu_mem_mb": None, "gpu_monitor": ""}


def _process_group_snapshot() -> dict[str, float | None]:
    if psutil is None:
        return {}

    groups = {
        "ollama": {"cpu": 0.0, "rss": 0.0},
        "guardrail": {"cpu": 0.0, "rss": 0.0},
        "vllm": {"cpu": 0.0, "rss": 0.0},
    }
    try:
        processes = psutil.process_iter(["name", "cmdline", "memory_info"])
    except Exception:
        return {}

    for process in processes:
        try:
            name = str(process.info.get("name") or "").lower()
            cmdline = " ".join(process.info.get("cmdline") or []).lower()
            rss = process.memory_info().rss / (1024 * 1024)
            cpu = process.cpu_percent(interval=None)
        except Exception:
            continue

        if "ollama" in name or "ollama" in cmdline or "llama-server" in cmdline:
            groups["ollama"]["cpu"] += float(cpu)
            groups["ollama"]["rss"] += float(rss)
        if "serve_llama_guard_stack.py" in cmdline or "llama_guard_stack" in cmdline:
            groups["guardrail"]["cpu"] += float(cpu)
            groups["guardrail"]["rss"] += float(rss)
        if "vllm" in name or "vllm" in cmdline:
            groups["vllm"]["cpu"] += float(cpu)
            groups["vllm"]["rss"] += float(rss)

    return {
        "ollama_cpu_pct": groups["ollama"]["cpu"],
        "ollama_rss_mb": groups["ollama"]["rss"],
        "guardrail_cpu_pct": groups["guardrail"]["cpu"],
        "guardrail_rss_mb": groups["guardrail"]["rss"],
        "vllm_cpu_pct": groups["vllm"]["cpu"],
        "vllm_rss_mb": groups["vllm"]["rss"],
    }


@dataclass
class ResourceMonitor:
    interval_sec: float = 0.5
    samples: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._context: dict[str, Any] = {
            "pipeline": "",
            "question_id": "",
            "question_group": "",
            "repeat": "",
        }
        self._process = psutil.Process() if psutil is not None else None
        if self._process is not None:
            self._process.cpu_percent(interval=None)

    @property
    def psutil_available(self) -> bool:
        return self._process is not None

    def set_context(self, **context: Any) -> None:
        with self._lock:
            self._context.update(context)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while self._running:
            self.sample()
            time.sleep(self.interval_sec)

    def sample(self) -> None:
        with self._lock:
            context = dict(self._context)
        cpu = None
        rss = None
        group_snapshot = _process_group_snapshot()
        if self._process is not None:
            try:
                cpu = self._process.cpu_percent(interval=None)
                rss = self._process.memory_info().rss / (1024 * 1024)
            except Exception:
                cpu = None
                rss = None
        total_cpu = None
        total_rss = None
        if cpu is not None:
            total_cpu = float(cpu) + sum(
                float(group_snapshot.get(key) or 0.0)
                for key in ("ollama_cpu_pct", "guardrail_cpu_pct", "vllm_cpu_pct")
            )
        if rss is not None:
            total_rss = float(rss) + sum(
                float(group_snapshot.get(key) or 0.0)
                for key in ("ollama_rss_mb", "guardrail_rss_mb", "vllm_rss_mb")
            )
        gpu = _query_gpu()
        self.samples.append(
            {
                "timestamp": time.time(),
                **context,
                "cpu_pct": total_cpu,
                "rss_mb": total_rss,
                "pipeline_cpu_pct": cpu,
                "pipeline_rss_mb": rss,
                **group_snapshot,
                "gpu_util_pct": gpu.get("gpu_util_pct"),
                "gpu_mem_mb": gpu.get("gpu_mem_mb"),
                "gpu_monitor": gpu.get("gpu_monitor", ""),
            }
        )


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, float | None]:
    def values(key: str) -> list[float]:
        result = []
        for sample in samples:
            value = sample.get(key)
            if isinstance(value, (int, float)):
                result.append(float(value))
        return result

    cpu = values("cpu_pct")
    rss = values("rss_mb")
    gpu = values("gpu_util_pct")
    gpu_mem = values("gpu_mem_mb")
    return {
        "cpu_avg": sum(cpu) / len(cpu) if cpu else None,
        "cpu_max": max(cpu) if cpu else None,
        "mem_avg_mb": sum(rss) / len(rss) if rss else None,
        "mem_max_mb": max(rss) if rss else None,
        "gpu_avg": sum(gpu) / len(gpu) if gpu else None,
        "gpu_max": max(gpu) if gpu else None,
        "gpu_mem_avg_mb": sum(gpu_mem) / len(gpu_mem) if gpu_mem else None,
        "gpu_mem_max_mb": max(gpu_mem) if gpu_mem else None,
    }
