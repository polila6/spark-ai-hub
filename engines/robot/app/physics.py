"""MuJoCo checks on a G1 motion: what the kinematic preview cannot see.

Reported per motion: self-collisions, floor penetration, joint limits, centre of mass over the feet, torque the motion would
need (inverse dynamics) and a simple tracking rollout. Two of these are estimates and read as such: torque comes from
differentiating poses (information only, it never fails a motion), and the rollout uses generic PD gains, not the robot's
controller, so a fall there is a warning to look closer and not a prediction of what the real robot does."""
from __future__ import annotations

import threading
from pathlib import Path

import mujoco
import numpy as np
from scipy.signal import savgol_filter

_XML = Path("/opt/kimodo/kimodo/assets/skeletons/g1skel34/xml/g1.xml")
_lock = threading.Lock()
_model = None
FLOOR_TOL_M = 0.02
LIMIT_TOL_RAD = 0.01
FALL_HEIGHT_M = 0.45
MAX_ROLLOUT_S = 30


def _load() -> mujoco.MjModel:
    global _model
    if _model is None:
        spec = mujoco.MjSpec.from_file(str(_XML))
        if not any(g.type == mujoco.mjtGeom.mjGEOM_PLANE for g in spec.worldbody.geoms):
            floor = spec.worldbody.add_geom()
            floor.type = mujoco.mjtGeom.mjGEOM_PLANE
            floor.size = [0, 0, 0.05]
        _model = spec.compile()
    return _model


def _names(m, kind, ids):
    return [mujoco.mj_id2name(m, kind, int(i)) or str(int(i)) for i in ids]


def check(qpos: np.ndarray, fps: float, rollout: bool = True) -> dict:
    with _lock:
        return _check(_load(), np.asarray(qpos, dtype=np.float64), fps, rollout)


def _on_floor(m, c) -> bool:
    return m.geom_bodyid[c.geom1] == 0 or m.geom_bodyid[c.geom2] == 0


def _support_margin(m, d) -> float | None:
    """Distance (m) from the centre of mass to the centre of the floor contacts; None without contact."""
    points = [d.contact[i].pos[:2].copy() for i in range(d.ncon) if _on_floor(m, d.contact[i])]
    if not points:
        return None
    return float(np.linalg.norm(d.subtree_com[1][:2] - np.mean(points, axis=0)))


def _check(m, qpos: np.ndarray, fps: float, rollout: bool) -> dict:
    d = mujoco.MjData(m)
    dt = 1.0 / fps
    T = len(qpos)
    collisions: dict[tuple[str, str], int] = {}
    penetration, airborne, com = 0.0, 0, []
    limits = m.jnt_range[1:]  # the first joint is the floating base
    angles = qpos[:, 7:]
    over = np.maximum(limits[:, 0] - angles, angles - limits[:, 1])
    over[:, m.jnt_limited[1:] == 0] = -np.inf
    limit_hits = [{"joint": mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, j + 1), "frames": int((over[:, j] > LIMIT_TOL_RAD).sum()),
                   "max_excess_rad": round(float(over[:, j].max()), 4)} for j in range(over.shape[1]) if (over[:, j] > LIMIT_TOL_RAD).any()]
    velocities = np.zeros((T, m.nv))
    for t in range(T - 1):
        mujoco.mj_differentiatePos(m, velocities[t], dt, qpos[t], qpos[t + 1])
    velocities[-1] = velocities[-2] if T > 1 else 0
    if T > 7:  # finite differences of 30 fps poses are noisy: without this, accelerations (and torques) are meaningless
        velocities = savgol_filter(velocities, 7, 2, axis=0)
    accelerations = np.gradient(velocities, dt, axis=0) if T > 1 else np.zeros_like(velocities)
    torque_peak = np.zeros(m.nv)
    for t in range(T):
        d.qpos[:] = qpos[t]
        d.qvel[:] = velocities[t]
        d.qacc[:] = accelerations[t]
        mujoco.mj_inverse(m, d)
        torque_peak = np.maximum(torque_peak, np.abs(d.qfrc_inverse))
        mujoco.mj_forward(m, d)
        touching = False
        pairs = set()
        for i in range(d.ncon):
            c = d.contact[i]
            if _on_floor(m, c):
                touching = True
                penetration = max(penetration, -float(c.dist))
            elif c.dist < 0 and m.geom_bodyid[c.geom1] != m.geom_bodyid[c.geom2]:
                pairs.add(tuple(sorted(_names(m, mujoco.mjtObj.mjOBJ_BODY, [m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]]))))
        for pair in pairs:
            collisions[pair] = collisions.get(pair, 0) + 1
        airborne += 0 if touching else 1
        margin = _support_margin(m, d)
        if margin is not None:
            com.append(margin)
    frc = m.jnt_actfrcrange[1:]
    dof = m.jnt_dofadr[1:]
    torque_estimate = [{"joint": mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, j + 1), "peak_nm": round(float(torque_peak[dof[j]]), 1),
                    "limit_nm": round(float(frc[j, 1]), 1)}
                   for j in range(len(dof)) if frc[j, 1] > 0 and torque_peak[dof[j]] > frc[j, 1]]
    result = {
        "frames": T, "fps": fps, "duration_s": round(T / fps, 3),
        "self_collisions": [{"bodies": list(k), "frames": v} for k, v in sorted(collisions.items(), key=lambda kv: -kv[1])][:30],
        "floor_penetration_m": round(penetration, 4), "frames_without_floor_contact": airborne,
        "joint_limit_violations": limit_hits, "torque_estimate_over_limit": torque_estimate,
        "com_to_contacts_max_m": round(max(com), 3) if com else None,
    }
    if rollout:
        result["tracking"] = _rollout(m, qpos, fps)
    warnings = []
    if result["self_collisions"]:
        warnings.append("self_collision")
    if penetration > FLOOR_TOL_M:
        warnings.append("floor_penetration")
    if limit_hits:
        warnings.append("joint_limits")
    if rollout and result["tracking"]["fell"]:
        warnings.append("falls_in_simulation")
    return {**result, "ok": not warnings, "passed": not warnings, "warnings": warnings}


def _rollout(m, qpos: np.ndarray, fps: float) -> dict:
    """Follow the motion with generic joint PD control under gravity and report whether the robot stays up."""
    d = mujoco.MjData(m)
    d.qpos[:] = qpos[0]
    mujoco.mj_forward(m, d)
    kp, kd = np.zeros(m.nv), np.zeros(m.nv)
    for j in range(1, m.njnt):
        limit = m.jnt_actfrcrange[j, 1] or 50.0
        kp[m.jnt_dofadr[j]], kd[m.jnt_dofadr[j]] = limit * 2.0, limit * 0.1
    forces = np.zeros(m.nv)
    for j in range(1, m.njnt):
        forces[m.jnt_dofadr[j]] = m.jnt_actfrcrange[j, 1] or 50.0
    steps_per_frame = max(1, round(1.0 / (fps * m.opt.timestep)))
    frames = min(len(qpos), int(MAX_ROLLOUT_S * fps))
    error, fell_at = 0.0, None
    for t in range(frames):
        target = qpos[t, 7:]
        for _ in range(steps_per_frame):
            d.qfrc_applied[:] = 0
            torque = kp[6:] * (target - d.qpos[7:]) - kd[6:] * d.qvel[6:]
            d.qfrc_applied[6:] = np.clip(torque, -forces[6:], forces[6:])
            mujoco.mj_step(m, d)
        error = max(error, float(np.linalg.norm(d.qpos[:2] - qpos[t, :2])))
        if d.qpos[2] < FALL_HEIGHT_M:
            fell_at = t
            break
    return {"fell": fell_at is not None, "fell_at_s": round(fell_at / fps, 2) if fell_at is not None else None,
            "max_root_drift_m": round(error, 3), "controller": "generic_pd"}
