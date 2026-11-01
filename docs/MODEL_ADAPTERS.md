# Engine and scientific adapters

## 1. Engine adapter interface (`backend/app/engines/base.py`)

```python
class EngineAdapter(ABC):
    name: str
    capabilities: set[str]          # chat, completion, embeddings, tools, streaming, image, video, asr, tts, scientific, multi_model
    on_demand_container: bool       # container created/stopped by engine-manager
    def health_check(self) -> Health            # required
    def inspect(self, model) -> dict
    def check_compatibility(self, model) -> dict
    def estimate_resources(self, model) -> dict
    def install(self, model, **kw) -> None
    def load(self, model, params) -> LoadResult  # seconds + measured memory
    def unload(self, model) -> None
    def loaded_models(self) -> list[dict]
    def infer(self, model, payload) -> dict
    def benchmark(self, model, config) -> dict
    def cancel(self, ref) -> None
    def openai_base(self) -> str                 # LLM engines only
```

Methods that do not make sense for an engine raise `NotSupported`; callers check `capabilities` instead of assuming an
LLM API for every modality. Compatibility and resource estimation are centralised in `services/compat.py` (deterministic
rules) so that verdicts are comparable across engines.

| Adapter | File | Capabilities | Lifecycle |
|---|---|---|---|
| `OllamaAdapter` | engines/ollama.py | chat, completion, embeddings, streaming, multi_model (+tools/vision per model from `/api/show`) | always-on server; load = `keep_alive`, unload = `keep_alive: 0` |
| `VllmAdapter` | engines/vllm.py | chat, completion, embeddings, streaming, tools (parser required) | one container per model via engine-manager |
| `ComfyUIAdapter` | engines/containers.py | image, video | on-demand container; `/prompt`, `/history`, `/interrupt`, `/free` |
| `MLWorkerAdapter` | engines/containers.py | asr, tts, scientific | on-demand container; `/asr`, `/tts`, `/sci/infer`, `/cancel` |

### Adding an engine

1. Implement a subclass in `backend/app/engines/<name>.py` and register it in `engines/__init__.py::_REGISTRY`.
2. If it needs a container, add a typed spec + allow-listed route in `engine-manager/app/main.py` (fixed image, mounts,
   network; validate every parameter) and a `compose.optional.yaml` build entry.
3. Add rules to `services/compat.py::analyse` (formats → verdict, estimate, reasons).
4. Add a smoke test branch in `worker/handlers/models.py`.
5. Unit-test the rules (`backend/tests`) and add an acceptance test.

## 2. Scientific adapter specification (Scientific Lab)

Scientific adapters are **trusted code shipped with the hub** that runs inside the ML worker (GPU, uid 1000, no network,
read-only model/dataset mounts). Users upload **data only**:

* `model.safetensors` (header-validated server-side; loaded with `safetensors`, never pickle)
* `config.json` (validated against the adapter schema)
* optional `.json/.txt/.md` metadata

Contract of an adapter (`engines/mlworker/app/main.py`):

```python
def adapter_<name>(body: SciIn, model_dir: Path, dataset: Path, out: Path) -> dict:
    # body.options: user options (validated JSON), body.job_id for cooperative cancellation (_cancel set)
    # read ONLY model_dir and dataset; write ONLY into out
    return {"rows": int, "metrics": dict, "files": ["predictions.csv", ...]}
```

Register it in `ADAPTERS` (worker) and describe it in `backend/app/services/sci_adapters.py::ADAPTERS`
(`required_files`, `config_schema`, `dataset_formats`, `metrics`) with a server-side `validate_artifacts` branch.

### Included adapters

| Adapter | Input | Model | Metrics | Example (verified) |
|---|---|---|---|---|
| `linear_regression` | CSV/NPY/JSON(L) with `feature_columns` | `weight [out,in]`, `bias [out]` (or 2-layer MLP `fc1.*`,`fc2.*`), optional `x_mean/x_std` | MSE, MAE, R² per target (when targets present) | `adapters/examples/make_regression_example.py` → R² 0.9987, MSE 0.010786 = numpy reference |
| `image_classifier` | ZIP of images (folders = labels, optional) | timm architecture from allow-list, safetensors state dict | top-1/top-5 accuracy if folder names match label names | `timm/resnet18.a1_in1k` registered from HF snapshot → top-1 0.667, top-5 1.0 on 6 generated images |

### Converting a PyTorch checkpoint to safetensors (on your own machine)

```python
import torch
from safetensors.torch import save_file
state = torch.load("model.pt", map_location="cpu", weights_only=True)   # only if you trust the file
save_file({k: v.contiguous() for k, v in state.items()}, "model.safetensors")
```

Models needing arbitrary Python code are not executed by the hub. They require a new trusted adapter reviewed by an
administrator (or an isolated environment outside this platform).
