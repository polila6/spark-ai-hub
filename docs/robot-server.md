# Robot server

Connects a robot control program to the Spark: text to Unitree G1 motion (NVIDIA Kimodo) and physics checks (MuJoCo). The
program runs on its own computer and only needs the address shown on the hub's **Robot** page:

    http://<tailscale-ip>:5000/generate

The port is reachable from the tailnet only: the container publishes it on loopback and `tailscale serve` forwards it. There
is no password on this port, so anyone on the tailnet can generate motions. Use Tailscale access rules to narrow that down.

## Set up

    scripts/sah.sh robot-build

Then, on the Robot page: **Start**, and follow the steps to install Llama 3 (Kimodo's text encoder; Meta approves each
Hugging Face account). The Kimodo model is installed from the page's Library.

## POST /generate

JSON body. The usual spellings of each field are accepted.

| Field | Also accepted as | Default |
|---|---|---|
| `prompt` (text or list of sequential texts, in English) | `text`, `instruction`, `description`, `prompts` | required |
| `duration` (seconds) | `seconds`, `duration_s`, or `num_frames` at 30 fps | 5 |
| `diffusion_steps` | `steps`, `num_steps` | 100 |
| `model` | `model_name` | `Kimodo-G1-RP-v1` |
| `cfg_weight`, `cfg_type` | `cfg_scale`, `guidance` | Kimodo's |
| `apply_root_motion` | `root_motion` | true |
| `dof` (23 or 29) | `dofs` | 29 |
| `seed`, `num_samples` | | |
| `poses`: `[{"time": s, "qpos": [...]}]` | `target_poses`, `keyframes` | |
| `constraints` (Kimodo's constraint format) | | |

Poses are targets, not guarantees: they are quantised to the nearest frame. With `"format": "csv"` or `Accept: text/csv` the
answer is the CSV alone. Otherwise JSON, with the motion as `qpos` (rows of root xyz, quaternion wxyz, joint angles in
radians, in the order of `joint_names`), `csv`, `root_positions`, `root_quaternions` and `joint_angles`, plus `fps`,
`num_frames`, `duration`, `model` and `elapsed_s`.

Errors are JSON with `code` and `error`: `text_encoder_missing`, `not_enough_memory`, `model_not_installed`, `unknown_model`,
`bad_request`, `generation_failed`.

## POST /physics

Also at `/physics/check`, `/check_physics`, `/simulate`, `/mujoco/check` and `/validate`. Body: `qpos` (rows of 36 values, or
30 for 23 DoF) or `csv`, and `fps`. `"rollout": false` skips the simulated tracking.

Answer: `ok`, `warnings`, `self_collisions`, `floor_penetration_m`, `frames_without_floor_contact`,
`joint_limit_violations`, `torque_estimate_over_limit`, `com_to_contacts_max_m` and `tracking`.

Two of these are estimates. Torque comes from differentiating poses and never fails a motion. `tracking` follows the motion
with generic PD gains, not the robot's controller: a fall there is a reason to look closer, not a prediction of what the real
robot will do. Neither replaces your own safety checks before sending a motion to the robot.

## Other routes

`GET /health` (also `/`, `/status`, `/capabilities`) and `GET /models`.

## Memory

Models stay loaded while they fit. Llama 3 needs about 17 GB plus a 2 GB margin (`SAH_ROBOT_MARGIN_GB`) and is the first to
leave if the machine runs short, so the hub's main model is not affected. Embeddings are cached on disk, so prompts already
seen work without it. The container is limited to 28 GB (`SAH_ROBOT_MEMORY`).

## Licences

Kimodo's code is Apache-2.0; each model has its licence on its Hugging Face page. Llama 3 has Meta's licence.
