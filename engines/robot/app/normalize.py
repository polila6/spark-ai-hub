"""What a robot suite sends and receives, without torch or Kimodo, so it can be unit tested anywhere.

The suites this serves were written against their own servers, so requests are read leniently: every usual spelling of a
field is accepted, and the response carries the motion in the forms clients commonly read."""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MODEL = "Kimodo-G1-RP-v1"
FPS = 30
MIN_S, MAX_S = 0.5, 60.0
JOINTS_29 = [
    "left_hip_pitch", "left_hip_roll", "left_hip_yaw", "left_knee", "left_ankle_pitch", "left_ankle_roll",
    "right_hip_pitch", "right_hip_roll", "right_hip_yaw", "right_knee", "right_ankle_pitch", "right_ankle_roll",
    "waist_yaw", "waist_roll", "waist_pitch",
    "left_shoulder_pitch", "left_shoulder_roll", "left_shoulder_yaw", "left_elbow",
    "left_wrist_roll", "left_wrist_pitch", "left_wrist_yaw",
    "right_shoulder_pitch", "right_shoulder_roll", "right_shoulder_yaw", "right_elbow",
    "right_wrist_roll", "right_wrist_pitch", "right_wrist_yaw",
]
NOT_IN_23 = {"waist_roll", "waist_pitch", "left_wrist_pitch", "left_wrist_yaw", "right_wrist_pitch", "right_wrist_yaw"}
ALIASES = {
    "prompt": ("prompt", "text", "instruction", "description", "caption", "query", "texts", "prompts"),
    "duration": ("duration", "duration_s", "durationS", "seconds", "length", "motion_length", "time"),
    "frames": ("num_frames", "numFrames", "frames", "n_frames"),
    "steps": ("diffusion_steps", "diffusionSteps", "num_denoising_steps", "steps", "num_steps", "numSteps", "denoising_steps", "n_steps"),
    "model": ("model", "model_name", "modelName", "modelname", "checkpoint"),
    "cfg_weight": ("cfg_weight", "cfgWeight", "cfg_scale", "cfgScale", "cfg", "guidance", "guidance_scale", "guidanceScale"),
    "cfg_type": ("cfg_type", "cfgType"),
    "root": ("applyRootMotion", "apply_root_motion", "root_motion", "rootMotion", "apply_root", "use_root_motion"),
    "seed": ("seed",),
    "samples": ("num_samples", "numSamples", "samples", "n_samples"),
    "dof": ("dof", "dofs", "num_dof", "robot_dof"),
    "poses": ("poses", "target_poses", "targetPoses", "pose_targets", "poseTargets", "keyframes"),
    "constraints": ("constraints", "constraint_lst"),
    "transitions": ("num_transition_frames", "numTransitionFrames", "transition_frames"),
    "markers": ("rootMarkers", "root_markers"),
}


class BadRequest(ValueError):
    pass


def pick(body: dict, name: str) -> Any:
    for key in ALIASES[name]:
        if body.get(key) is not None:
            return body[key]
    return None


def _number(value: Any, label: str, kind=float) -> Any:
    if value is None:
        return None
    try:
        return kind(value)
    except (TypeError, ValueError):
        raise BadRequest(f"{label} must be a number, got {value!r}") from None


def _flag(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(value)


@dataclass
class Request:
    prompts: list[str]
    frames: list[int]
    model: str = DEFAULT_MODEL
    steps: int = 100
    cfg_weight: Any = None
    cfg_type: str | None = None
    root_motion: bool = True
    seed: int | None = None
    samples: int = 1
    dof: int = 29
    poses: list[dict] = field(default_factory=list)
    constraints: list[dict] = field(default_factory=list)
    transitions: int = 5
    ignored: list[str] = field(default_factory=list)


def parse_generate(body: dict) -> Request:
    prompt = pick(body, "prompt")
    prompts = [str(p).strip() for p in prompt] if isinstance(prompt, (list, tuple)) else [str(prompt or "").strip()]
    prompts = [p for p in prompts if p]
    if not prompts:
        raise BadRequest("a text prompt is required (field: prompt)")
    frames_in = pick(body, "frames")
    if frames_in is not None:
        counts = frames_in if isinstance(frames_in, (list, tuple)) else [frames_in]
        frames = [_number(f, "num_frames", int) for f in counts]
    else:
        seconds = pick(body, "duration")
        if seconds is None:
            seconds = 5
        spans = seconds if isinstance(seconds, (list, tuple)) else [seconds]
        frames = [round(_number(s, "duration") * FPS) for s in spans]
    if len(frames) == 1 and len(prompts) > 1:  # one total length shared between sequential prompts
        frames = [max(1, frames[0] // len(prompts))] * len(prompts)
    if len(frames) != len(prompts):
        raise BadRequest(f"{len(prompts)} prompts but {len(frames)} durations")
    if sum(frames) > MAX_S * FPS or min(frames) < MIN_S * FPS:
        raise BadRequest(f"duration must be between {MIN_S} and {MAX_S} seconds")
    steps = _number(pick(body, "steps"), "diffusion_steps", int)
    steps = 100 if steps is None else steps
    if not 1 <= steps <= 1000:
        raise BadRequest("diffusion_steps must be between 1 and 1000")
    dof = _number(pick(body, "dof"), "dof", int) or 29
    if dof not in (23, 29):
        raise BadRequest("dof must be 23 or 29")
    weight = pick(body, "cfg_weight")
    if weight is not None and not isinstance(weight, (list, tuple)):
        weight = _number(weight, "cfg_weight")
    samples = _number(pick(body, "samples"), "num_samples", int)
    samples = 1 if samples is None else samples
    if not 1 <= samples <= 8:
        raise BadRequest("num_samples must be between 1 and 8")
    poses, constraints = pick(body, "poses") or [], pick(body, "constraints") or []
    if not isinstance(poses, list) or not isinstance(constraints, list):
        raise BadRequest("poses and constraints must be lists")
    ignored = ["rootMarkers"] if pick(body, "markers") else []  # floor markers cannot be followed yet: never drop them silently
    return Request(ignored=ignored, prompts=prompts, frames=frames, model=str(pick(body, "model") or DEFAULT_MODEL).strip(), steps=steps,
                   cfg_weight=weight, cfg_type=pick(body, "cfg_type"), root_motion=_flag(pick(body, "root"), True),
                   seed=_number(pick(body, "seed"), "seed", int), samples=samples, dof=dof, poses=poses, constraints=constraints,
                   transitions=5 if pick(body, "transitions") is None else _number(pick(body, "transitions"), "num_transition_frames", int))


def joint_names(dof: int = 29) -> list[str]:
    return JOINTS_29 if dof == 29 else [j for j in JOINTS_29 if j not in NOT_IN_23]


def select_dof(qpos, dof: int):
    """Keep root (7 values) and the joints of this robot variant. Works on anything shaped (..., 36)."""
    if dof == 29:
        return qpos
    keep = list(range(7)) + [7 + i for i, j in enumerate(JOINTS_29) if j not in NOT_IN_23]
    return qpos[..., keep]


def freeze_root(qpos):
    """Without root motion the robot stays where it started: horizontal position fixed, everything else kept."""
    out = qpos.copy()
    out[..., 0:2] = out[..., 0:1, 0:2]
    return out


def to_csv(qpos) -> str:
    buf = io.StringIO()
    for row in qpos:
        buf.write(",".join(f"{float(v):.6f}" for v in row) + "\n")
    return buf.getvalue()


def parse_qpos(body: dict):
    """(rows, fps) from a physics request: qpos/frames as rows or csv as text."""
    rows = body.get("qpos") or body.get("frames") or body.get("motion") or body.get("trajectory")
    text = body.get("csv") or body.get("csv_text")
    if rows is None and text:
        try:
            rows = [[float(v) for v in line.split(",")] for line in str(text).strip().splitlines() if line.strip()]
        except ValueError:
            raise BadRequest("csv must only hold numbers") from None
    if not rows or not isinstance(rows, list):
        raise BadRequest("a motion is required (field: qpos, or csv)")
    widths = {len(r) for r in rows if isinstance(r, (list, tuple))}
    if len(widths) != 1 or next(iter(widths)) not in (30, 36):
        raise BadRequest(f"each frame must have 36 values (root xyz, quaternion wxyz, 29 joints) or 30 (23 joints), got {sorted(widths)}")
    return rows, float(body.get("fps") or FPS)


def expand_23_to_29(qpos):
    """Physics always runs on the 29-joint model: joints the 23-DoF robot lacks stay at zero."""
    if qpos.shape[-1] == 36:
        return qpos
    import numpy as np
    out = np.zeros((*qpos.shape[:-1], 36), dtype=qpos.dtype)
    out[..., :7] = qpos[..., :7]
    kept = [7 + i for i, j in enumerate(JOINTS_29) if j not in NOT_IN_23]
    out[..., kept] = qpos[..., 7:]
    return out


def encoder_mode(official: bool, provisional: bool) -> str | None:
    """Kimodo was trained on Llama 3, so it is always preferred the moment it is complete; Llama 3.1 only stands in for it."""
    return "official" if official else "provisional" if provisional else None


def weights_complete(index: dict | None, present: set[str]) -> bool:
    """A model counts as installed when every shard its index names is there, not when the first of them lands."""
    shards = set((index or {}).get("weight_map", {}).values())
    return bool(shards) and shards <= present


def cache_key(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


def fits(available_gb: float, need_gb: float, margin_gb: float) -> bool:
    return available_gb >= need_gb + margin_gb


def summarize(value: Any, depth: int = 0) -> Any:
    """Shape of a request, for the request log: texts are shortened and long lists reduced to their length."""
    if isinstance(value, dict) and depth < 3:
        return {str(k)[:60]: summarize(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return f"[list of {len(value)}]" if len(value) > 4 or depth >= 2 else [summarize(v, depth + 1) for v in value]
    if isinstance(value, str):
        return value[:160]
    return value if isinstance(value, (int, float, bool, type(None))) else type(value).__name__
