"""The GPU reading of the panel and how the monitor recovers when its container loses the GPU."""
from types import SimpleNamespace

from app import monitor
from app.services import telemetry


def _smi(stdout="", stderr="", code=0):
    return lambda *a, **k: SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)


def test_a_lost_gpu_is_reported_with_its_real_reason_not_an_empty_one(monkeypatch):
    """Found in production: the monitor container lost the GPU, nvidia-smi wrote the error to stdout and the panel showed nothing."""
    monkeypatch.setattr(telemetry.shutil, "which", lambda n: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(telemetry.subprocess, "run", _smi(stdout="Failed to initialize NVML: Unknown Error\n", code=255))
    g = telemetry.gpu()
    assert g["available"] is False and "NVML" in g["reason"] and g["nvml_lost"] is True


def test_an_ordinary_failure_is_not_taken_for_a_lost_gpu(monkeypatch):
    monkeypatch.setattr(telemetry.shutil, "which", lambda n: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(telemetry.subprocess, "run", _smi(stderr="some other error", code=1))
    g = telemetry.gpu()
    assert g["available"] is False and g["reason"] == "some other error" and not g.get("nvml_lost")


def test_a_healthy_reading_still_works_on_unified_memory(monkeypatch):
    out = "NVIDIA GB10, 94, 59, 42.05, 2411, [N/A], [N/A], 580.95, P0\n"
    monkeypatch.setattr(telemetry.shutil, "which", lambda n: "/usr/bin/nvidia-smi")
    calls = iter([SimpleNamespace(stdout=out, stderr="", returncode=0), SimpleNamespace(stdout="", stderr="", returncode=0)])
    monkeypatch.setattr(telemetry.subprocess, "run", lambda *a, **k: next(calls))
    g = telemetry.gpu()
    assert g["available"] and g["utilization_percent"] == 94.0 and g["temperature_c"] == 59.0 and g["unified_memory"] is True


def test_the_monitor_restarts_itself_after_two_minutes_without_gpu_but_never_in_a_loop():
    f = monitor.should_restart_for_gpu
    assert not f(10, 600, False)  # not yet two minutes
    assert f(24, 600, False)  # two minutes lost, running for a while: restart
    assert not f(24, 60, False)  # it has only just started: a fresh start that cannot see the GPU is not restarted again
    assert not f(500, 600, True)  # restarted in the last half hour already
