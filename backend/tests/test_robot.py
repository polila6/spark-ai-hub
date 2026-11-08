"""Robot suites: reading requests leniently, DoF variants, the address shown, and which models the library offers."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from app.services import robot

SCRIPT = Path(__file__).resolve().parents[2] / "engines" / "robot" / "app" / "normalize.py"
if not SCRIPT.exists():  # the backend image only ships backend/
    pytest.skip("engines/ not available here", allow_module_level=True)
spec = importlib.util.spec_from_file_location("robot_normalize", SCRIPT)
n = importlib.util.module_from_spec(spec)
import sys  # noqa: E402

sys.modules["robot_normalize"] = n  # dataclasses look the module up while it loads
spec.loader.exec_module(n)


@pytest.mark.parametrize("body", [
    {"prompt": "wave", "duration": 5, "diffusion_steps": 50},
    {"text": "wave", "seconds": 5, "steps": 50},
    {"instruction": "wave", "num_frames": 150, "num_steps": 50},
])
def test_every_usual_spelling_means_the_same(body):
    r = n.parse_generate(body)
    assert (r.prompts, r.frames, r.steps, r.model, r.dof) == (["wave"], [150], 50, "Kimodo-G1-RP-v1", 29)


def test_sequential_prompts_share_the_duration():
    assert n.parse_generate({"prompts": ["walk", "wave"], "duration": 10}).frames == [150, 150]
    assert n.parse_generate({"prompts": ["walk", "wave"], "duration": [4, 6]}).frames == [120, 180]
    with pytest.raises(n.BadRequest):
        n.parse_generate({"prompts": ["a", "b", "c"], "duration": [4, 6]})


@pytest.mark.parametrize("body", [{}, {"prompt": "  "}, {"prompt": "x", "duration": 999}, {"prompt": "x", "duration": "soon"},
                                  {"prompt": "x", "dof": 12}, {"prompt": "x", "steps": 0}, {"prompt": "x", "num_steps": 5000}, {"prompt": "x", "poses": "a"}])
def test_bad_requests_say_why(body):
    with pytest.raises(n.BadRequest):
        n.parse_generate(body)


def test_the_request_storyteller_really_sends():
    r = n.parse_generate({"prompt": "a person trying to be seen by a rescue airplane", "duration": 5, "model": "Kimodo-G1-RP-v1",
                          "diffusion_steps": 100, "applyRootMotion": False, "rootYawMode": "last", "rootMarkers": []})
    assert r.root_motion is False and r.frames == [150] and r.ignored == []


def test_floor_markers_are_reported_when_they_cannot_be_followed():
    assert n.parse_generate({"prompt": "x", "rootMarkers": [{"x": 1, "y": 2}]}).ignored == ["rootMarkers"]


def test_root_motion_flag_understands_text():
    assert n.parse_generate({"prompt": "x", "root_motion": "false"}).root_motion is False
    assert n.parse_generate({"prompt": "x"}).root_motion is True


def test_23_dof_drops_the_six_joints_that_robot_lacks():
    qpos = np.arange(36.0)[None, None].repeat(2, axis=1)
    out = n.select_dof(qpos, 23)
    assert out.shape == (1, 2, 30) and len(n.joint_names(23)) == 23 and "left_wrist_yaw" not in n.joint_names(23)
    assert n.select_dof(qpos, 29) is qpos
    back = n.expand_23_to_29(out[0])
    assert back.shape == (2, 36)
    names = n.JOINTS_29
    assert back[0, 7 + names.index("waist_pitch")] == 0 and back[0, 7 + names.index("left_knee")] == 7 + names.index("left_knee")


def test_without_root_motion_the_robot_stays_in_place():
    qpos = np.array([[[0.0, 0.0, 0.8, 1], [1.0, 2.0, 0.7, 1]]])
    out = n.freeze_root(qpos)
    assert out[0, 1, 0] == 0 and out[0, 1, 1] == 0 and out[0, 1, 2] == 0.7 and qpos[0, 1, 0] == 1


def test_physics_reads_rows_or_csv():
    assert n.parse_qpos({"qpos": [[0.0] * 36], "fps": 60}) == ([[0.0] * 36], 60.0)
    rows, fps = n.parse_qpos({"csv": n.to_csv(np.zeros((2, 30)))})
    assert len(rows) == 2 and len(rows[0]) == 30 and fps == n.FPS
    for bad in ({}, {"qpos": [[0.0] * 5]}, {"csv": "a,b"}):
        with pytest.raises(n.BadRequest):
            n.parse_qpos(bad)


def test_memory_fit_keeps_a_margin():
    assert n.fits(23, 18, 4) and not n.fits(21.9, 18, 4)


def test_official_encoder_wins_as_soon_as_it_is_complete():
    assert n.encoder_mode(False, False) is None
    assert n.encoder_mode(False, True) == "provisional"
    assert n.encoder_mode(True, True) == "official"
    assert n.encoder_mode(True, False) == "official"


def test_a_model_is_installed_only_when_every_shard_is_there():
    index = {"weight_map": {"a": "model-1.safetensors", "b": "model-2.safetensors", "c": "model-2.safetensors"}}
    assert not n.weights_complete(index, {"model-1.safetensors"})
    assert n.weights_complete(index, {"model-1.safetensors", "model-2.safetensors", "config.json"})
    assert not n.weights_complete(None, {"model-1.safetensors"}) and not n.weights_complete({"weight_map": {}}, set())


def test_request_log_shortens_what_it_keeps():
    out = n.summarize({"prompt": "x" * 500, "qpos": [[0.0] * 36] * 150, "steps": 5})
    assert len(out["prompt"]) == 160 and out["qpos"] == "[list of 150]" and out["steps"] == 5


def test_address_follows_tailscale():
    u = robot.urls({"ipv4": "100.64.1.2", "dns": "spark.example.ts.net"})
    assert u["generate"] == "http://100.64.1.2:5000/generate" and u["generate_dns"] == "http://spark.example.ts.net:5000/generate"
    assert robot.urls({})["generate"] is None


def test_library_offers_only_what_the_robot_can_play():
    server = {"installed": ["nvidia/Kimodo-G1-RP-v1"], "loaded": [{"repo": "nvidia/Kimodo-G1-RP-v1"}],
              "downloads": {"nvidia/Kimodo-G1-SEED-v1": {"state": "running", "bytes": 5, "proc": None}}}
    lib = {m["name"]: m for m in robot.library(server)}
    assert lib["Kimodo-G1-RP-v1"]["loaded"] and lib["Kimodo-G1-RP-v1"]["compatible"]
    assert lib["Kimodo-G1-SEED-v1"]["download"]["state"] == "running" and not lib["Kimodo-G1-SEED-v1"]["installed"]
    assert not lib["Kimodo-SOMA-RP-v1.2"]["compatible"] and all(("G1" in m["name"]) == m["compatible"] for m in lib.values())


def test_llama_access_reports_each_case(monkeypatch):
    class Denied(Exception):
        class response:
            status_code = 403

    class Api:
        def __init__(self, exc=None):
            self.exc = exc

        def auth_check(self, repo):
            if self.exc:
                raise self.exc

    assert robot.llama_access(None) == {"state": "no_token"}
    monkeypatch.setattr(robot.hf, "api", lambda token: Api(Denied()))
    assert robot.llama_access("bad")["state"] == "denied"
    monkeypatch.setattr(robot.hf, "api", lambda token: Api())
    assert robot.llama_access("good")["state"] == "granted"


def test_a_lost_gpu_is_recognised_so_the_container_restarts_itself():
    assert n.gpu_lost(RuntimeError("CUDA error: unknown error"))
    assert n.gpu_lost("Failed to initialize NVML: Unknown Error")
    assert n.gpu_lost(RuntimeError("No CUDA GPUs are available"))
    assert not n.gpu_lost(ValueError("duration must be between 0.5 and 10 s"))
    assert not n.gpu_lost(RuntimeError("CUDA out of memory"))  # a real OOM is not a lost device
