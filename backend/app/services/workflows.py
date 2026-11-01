"""Registered ComfyUI workflows (API format) built from official ComfyUI example graphs.

A workflow is offered as *available* only when every required model file is installed in the
ComfyUI model directories and every node class exists in the running ComfyUI. Users can only
run these templates with validated parameters — arbitrary uploaded workflows are not executed.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings

SAMPLERS = ["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_sde", "ddim", "uni_pc", "lcm"]
SCHEDULERS = ["normal", "karras", "exponential", "sgm_uniform", "simple", "beta"]


@dataclass
class ModelFile:
    kind: str  # ComfyUI models sub-directory
    filename: str
    repo_id: str
    repo_file: str
    size_gb: float
    license: str


@dataclass
class Workflow:
    id: str
    name: dict  # {"es":..., "en":...}
    kind: str  # image | video
    mode: str
    family: str
    models: list[ModelFile]
    nodes: list[str]
    memory_gb: float
    gpu_heavy: bool = False
    params: dict = field(default_factory=dict)
    source: str = ""


DREAMSHAPER = ModelFile("checkpoints", "DreamShaper_8_pruned.safetensors", "Lykon/DreamShaper",
                        "DreamShaper_8_pruned.safetensors", 2.13, "creativeml-openrail-m")
REALESRGAN = ModelFile("upscale_models", "RealESRGAN_x4plus.safetensors", "Comfy-Org/Real-ESRGAN_repackaged",
                       "RealESRGAN_x4plus.safetensors", 0.07, "bsd-3-clause")
WAN21_T2V = ModelFile("diffusion_models", "wan2.1_t2v_1.3B_fp16.safetensors", "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
                      "split_files/diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors", 2.84, "apache-2.0")
UMT5 = ModelFile("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
                 "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", 6.74, "apache-2.0")
WAN21_VAE = ModelFile("vae", "wan_2.1_vae.safetensors", "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
                      "split_files/vae/wan_2.1_vae.safetensors", 0.25, "apache-2.0")
WAN22_TI2V = ModelFile("diffusion_models", "wan2.2_ti2v_5B_fp16.safetensors", "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
                       "split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors", 10.0, "apache-2.0")
WAN22_VAE = ModelFile("vae", "wan2.2_vae.safetensors", "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
                      "split_files/vae/wan2.2_vae.safetensors", 1.41, "apache-2.0")

QWEN_IMAGE = ModelFile("diffusion_models", "qwen_image_fp8_e4m3fn.safetensors", "Comfy-Org/Qwen-Image_ComfyUI",
                       "split_files/diffusion_models/qwen_image_fp8_e4m3fn.safetensors", 20.43, "apache-2.0")
QWEN_TE = ModelFile("text_encoders", "qwen_2.5_vl_7b_fp8_scaled.safetensors", "Comfy-Org/Qwen-Image_ComfyUI",
                    "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors", 9.38, "apache-2.0")
QWEN_VAE = ModelFile("vae", "qwen_image_vae.safetensors", "Comfy-Org/Qwen-Image_ComfyUI",
                     "split_files/vae/qwen_image_vae.safetensors", 0.25, "apache-2.0")
QWEN_LIGHTNING = ModelFile("loras", "Qwen-Image-Lightning-8steps-V2.0-bf16.safetensors", "lightx2v/Qwen-Image-Lightning",
                           "Qwen-Image-Lightning-8steps-V2.0-bf16.safetensors", 0.85, "apache-2.0")
QWEN_EDIT = ModelFile("diffusion_models", "qwen_image_edit_2509_fp8_e4m3fn.safetensors", "Comfy-Org/Qwen-Image-Edit_ComfyUI",
                      "split_files/diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors", 20.43, "apache-2.0")
QWEN_EDIT_LIGHTNING = ModelFile("loras", "Qwen-Image-Edit-2509-Lightning-8steps-V1.0-bf16.safetensors",
                                "lightx2v/Qwen-Image-Lightning",
                                "Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-8steps-V1.0-bf16.safetensors", 0.85,
                                "apache-2.0")

SD_PARAMS = {
    "prompt": {"type": "text", "required": True},
    "negative_prompt": {"type": "text", "default": "blurry, low quality, watermark, text"},
    "seed": {"type": "int", "min": 0, "max": 2**48, "default": None},
    "steps": {"type": "int", "min": 1, "max": 100, "default": 25},
    "cfg": {"type": "float", "min": 1.0, "max": 20.0, "default": 7.0},
    "sampler": {"type": "enum", "values": SAMPLERS, "default": "dpmpp_2m"},
    "scheduler": {"type": "enum", "values": SCHEDULERS, "default": "karras"},
    "width": {"type": "int", "min": 256, "max": 1536, "step": 64, "default": 512},
    "height": {"type": "int", "min": 256, "max": 1536, "step": 64, "default": 768},
    "batch_size": {"type": "int", "min": 1, "max": 4, "default": 1},
}

WORKFLOWS: dict[str, Workflow] = {
    "sd15-txt2img": Workflow(
        "sd15-txt2img", {"es": "Texto a imagen (SD 1.5)", "en": "Text to image (SD 1.5)"}, "image", "txt2img", "sd15",
        [DREAMSHAPER], ["CheckpointLoaderSimple", "CLIPTextEncode", "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage"],
        memory_gb=6.0, params=SD_PARAMS,
        source="ComfyUI default graph; checkpoint per NVIDIA DGX Spark ComfyUI playbook"),
    "sd15-img2img": Workflow(
        "sd15-img2img", {"es": "Imagen a imagen (SD 1.5)", "en": "Image to image (SD 1.5)"}, "image", "img2img", "sd15",
        [DREAMSHAPER], ["CheckpointLoaderSimple", "LoadImage", "VAEEncode", "KSampler", "VAEDecode", "SaveImage"],
        memory_gb=6.0,
        params={**{k: v for k, v in SD_PARAMS.items() if k not in ("width", "height", "batch_size")},
                "image": {"type": "image", "required": True},
                "denoise": {"type": "float", "min": 0.05, "max": 1.0, "default": 0.6}},
        source="ComfyUI examples: img2img"),
    "sd15-inpaint": Workflow(
        "sd15-inpaint", {"es": "Inpainting (SD 1.5)", "en": "Inpainting (SD 1.5)"}, "image", "inpaint", "sd15",
        [DREAMSHAPER], ["CheckpointLoaderSimple", "LoadImage", "VAEEncodeForInpaint", "KSampler", "VAEDecode", "SaveImage"],
        memory_gb=6.0,
        params={**{k: v for k, v in SD_PARAMS.items() if k not in ("width", "height", "batch_size")},
                "image": {"type": "image", "required": True, "help": "PNG with transparent area = region to repaint"},
                "grow_mask_by": {"type": "int", "min": 0, "max": 64, "default": 6}},
        source="ComfyUI examples: inpaint (mask from alpha channel)"),
    "upscale-esrgan-x4": Workflow(
        "upscale-esrgan-x4", {"es": "Escalado ×4 (Real-ESRGAN)", "en": "Upscale ×4 (Real-ESRGAN)"}, "image", "upscale",
        "esrgan", [REALESRGAN], ["LoadImage", "UpscaleModelLoader", "ImageUpscaleWithModel", "SaveImage"], memory_gb=4.0,
        params={"image": {"type": "image", "required": True}}, source="ComfyUI examples: upscale models"),
    "qwen-image-txt2img": Workflow(
        "qwen-image-txt2img", {"es": "Texto a imagen (Qwen-Image, alta calidad)", "en": "Text to image (Qwen-Image, high quality)"},
        "image", "txt2img", "qwen_image", [QWEN_IMAGE, QWEN_TE, QWEN_VAE, QWEN_LIGHTNING],
        ["UNETLoader", "CLIPLoader", "VAELoader", "LoraLoaderModelOnly", "ModelSamplingAuraFlow", "CLIPTextEncode",
         "EmptySD3LatentImage", "KSampler", "VAEDecode", "SaveImage"],
        memory_gb=36.0,
        params={"prompt": {"type": "text", "required": True},
                "negative_prompt": {"type": "text", "default": ""},
                "seed": {"type": "int", "min": 0, "max": 2**48, "default": None},
                "steps": {"type": "int", "min": 4, "max": 50, "default": 8},
                "cfg": {"type": "float", "min": 1.0, "max": 8.0, "default": 1.0},
                "width": {"type": "int", "min": 512, "max": 1664, "step": 16, "default": 1328},
                "height": {"type": "int", "min": 512, "max": 1664, "step": 16, "default": 1328},
                "batch_size": {"type": "int", "min": 1, "max": 2, "default": 1}},
        source="ComfyUI Qwen-Image template + lightx2v Qwen-Image-Lightning 8-step LoRA (Apache-2.0)"),
    "qwen-image-edit": Workflow(
        "qwen-image-edit", {"es": "Editar imagen con instrucciones (Qwen-Image-Edit)", "en": "Instruction image editing (Qwen-Image-Edit)"},
        "image", "edit", "qwen_edit", [QWEN_EDIT, QWEN_TE, QWEN_VAE, QWEN_EDIT_LIGHTNING],
        ["UNETLoader", "CLIPLoader", "VAELoader", "LoraLoaderModelOnly", "ModelSamplingAuraFlow", "CFGNorm", "LoadImage",
         "ImageScaleToTotalPixels", "TextEncodeQwenImageEditPlus", "VAEEncode", "KSampler", "VAEDecode", "SaveImage"],
        memory_gb=36.0,
        params={"prompt": {"type": "text", "required": True, "help": "What to change, e.g. 'make the sky stormy'"},
                "image": {"type": "image", "required": True},
                "seed": {"type": "int", "min": 0, "max": 2**48, "default": None},
                "steps": {"type": "int", "min": 4, "max": 50, "default": 8},
                "cfg": {"type": "float", "min": 1.0, "max": 8.0, "default": 1.0},
                "megapixels": {"type": "float", "min": 0.25, "max": 2.0, "default": 1.0}},
        source="ComfyUI Qwen-Image-Edit-2509 template + lightx2v 8-step Lightning LoRA (Apache-2.0)"),
    "wan21-t2v-1.3b": Workflow(
        "wan21-t2v-1.3b", {"es": "Texto a vídeo (Wan 2.1 1.3B)", "en": "Text to video (Wan 2.1 1.3B)"}, "video", "t2v",
        "wan21", [WAN21_T2V, UMT5, WAN21_VAE],
        ["UNETLoader", "CLIPLoader", "VAELoader", "CLIPTextEncode", "EmptyHunyuanLatentVideo", "ModelSamplingSD3",
         "KSampler", "VAEDecode", "SaveAnimatedWEBP"],
        memory_gb=22.0, gpu_heavy=True,
        params={"prompt": {"type": "text", "required": True},
                "negative_prompt": {"type": "text", "default": "static, blurry, low quality, distorted, watermark"},
                "seed": {"type": "int", "min": 0, "max": 2**48, "default": None},
                "steps": {"type": "int", "min": 4, "max": 50, "default": 30},
                "cfg": {"type": "float", "min": 1.0, "max": 12.0, "default": 6.0},
                "width": {"type": "int", "min": 256, "max": 1024, "step": 16, "default": 832},
                "height": {"type": "int", "min": 256, "max": 1024, "step": 16, "default": 480},
                "frames": {"type": "int", "min": 5, "max": 81, "step": 4, "default": 33},
                "fps": {"type": "int", "min": 4, "max": 30, "default": 16}},
        source="ComfyUI examples: Wan 2.1 text-to-video (Comfy-Org repackaged weights)"),
    "wan22-ti2v-5b": Workflow(
        "wan22-ti2v-5b", {"es": "Imagen/texto a vídeo (Wan 2.2 5B)", "en": "Image/text to video (Wan 2.2 5B)"}, "video",
        "i2v", "wan22", [WAN22_TI2V, UMT5, WAN22_VAE],
        ["UNETLoader", "CLIPLoader", "VAELoader", "CLIPTextEncode", "Wan22ImageToVideoLatent", "ModelSamplingSD3",
         "KSampler", "VAEDecode", "SaveAnimatedWEBP", "LoadImage"],
        memory_gb=40.0, gpu_heavy=True,
        params={"prompt": {"type": "text", "required": True},
                "negative_prompt": {"type": "text", "default": "static, blurry, low quality, distorted, watermark"},
                "image": {"type": "image", "required": False, "help": "Optional start frame (image-to-video)"},
                "seed": {"type": "int", "min": 0, "max": 2**48, "default": None},
                "steps": {"type": "int", "min": 4, "max": 50, "default": 20},
                "cfg": {"type": "float", "min": 1.0, "max": 12.0, "default": 5.0},
                "width": {"type": "int", "min": 256, "max": 1280, "step": 32, "default": 1280},
                "height": {"type": "int", "min": 256, "max": 1280, "step": 32, "default": 704},
                "frames": {"type": "int", "min": 5, "max": 121, "step": 4, "default": 49},
                "fps": {"type": "int", "min": 4, "max": 30, "default": 24}},
        source="ComfyUI examples: Wan 2.2 TI2V 5B"),
}


class WorkflowError(Exception):
    pass


def installed_files() -> set[str]:
    base = get_settings().models_dir / "comfyui"
    out = set()
    if base.exists():
        for p in base.rglob("*"):
            if p.is_file():
                out.add(str(p.relative_to(base)))
    return out


def missing_models(wf: Workflow, files: set[str] | None = None) -> list[ModelFile]:
    files = files if files is not None else installed_files()
    return [m for m in wf.models if f"{m.kind}/{m.filename}" not in files]


def validate(wf: Workflow, inputs: dict) -> dict:
    out = {}
    for name, spec in wf.params.items():
        v = inputs.get(name, spec.get("default"))
        if v is None:
            if spec.get("required"):
                raise WorkflowError(f"'{name}' is required")
            if name == "seed":
                v = random.randint(0, 2**48)
            else:
                continue
        t = spec["type"]
        if t == "int":
            v = int(v)
            if not spec["min"] <= v <= spec["max"]:
                raise WorkflowError(f"'{name}' must be between {spec['min']} and {spec['max']}")
            if spec.get("step"):
                v = v - (v % spec["step"]) if name not in ("frames",) else v
            if name == "frames" and (v - 1) % 4:
                v = ((v - 1) // 4) * 4 + 1
        elif t == "float":
            v = float(v)
            if not spec["min"] <= v <= spec["max"]:
                raise WorkflowError(f"'{name}' must be between {spec['min']} and {spec['max']}")
        elif t == "enum":
            if v not in spec["values"]:
                raise WorkflowError(f"'{name}' must be one of {spec['values']}")
        elif t == "text":
            v = str(v)[:4000]
        elif t == "image":
            v = str(v)
        out[name] = v
    return out


def estimate_memory(wf: Workflow, p: dict) -> float:
    if wf.family in ("qwen_image", "qwen_edit"):
        px = p.get("width", 1024) * p.get("height", 1024) * p.get("batch_size", 1) if wf.family == "qwen_image" \
            else p.get("megapixels", 1.0) * 1024 * 1024
        return round(wf.memory_gb + px / (1024 * 1024) * 2.0, 1)
    if wf.kind == "image":
        px = p.get("width", 512) * p.get("height", 512) * p.get("batch_size", 1)
        return round(wf.memory_gb + px / (512 * 512) * 0.6, 1)
    px = p.get("width", 832) * p.get("height", 480) * p.get("frames", 33)
    return round(wf.memory_gb + px / (832 * 480 * 33) * 6, 1)


def build(wf: Workflow, p: dict, input_name: str | None, prefix: str) -> dict:
    if wf.family == "sd15":
        ckpt = DREAMSHAPER.filename
        g: dict = {
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": p["prompt"], "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("negative_prompt", ""), "clip": ["4", 1]}},
            "3": {"class_type": "KSampler", "inputs": {
                "seed": p["seed"], "steps": p["steps"], "cfg": p["cfg"], "sampler_name": p["sampler"],
                "scheduler": p["scheduler"], "denoise": 1.0, "model": ["4", 0], "positive": ["6", 0],
                "negative": ["7", 0], "latent_image": ["5", 0]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["8", 0]}},
        }
        if wf.mode == "txt2img":
            g["5"] = {"class_type": "EmptyLatentImage", "inputs": {"width": p["width"], "height": p["height"],
                                                                   "batch_size": p.get("batch_size", 1)}}
        elif wf.mode == "img2img":
            g["10"] = {"class_type": "LoadImage", "inputs": {"image": input_name}}
            g["5"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["10", 0], "vae": ["4", 2]}}
            g["3"]["inputs"]["denoise"] = p["denoise"]
        elif wf.mode == "inpaint":
            g["10"] = {"class_type": "LoadImage", "inputs": {"image": input_name}}
            g["5"] = {"class_type": "VAEEncodeForInpaint", "inputs": {"pixels": ["10", 0], "vae": ["4", 2],
                                                                      "mask": ["10", 1], "grow_mask_by": p["grow_mask_by"]}}
        return g
    if wf.family in ("qwen_image", "qwen_edit"):
        edit = wf.family == "qwen_edit"
        g = {
            "37": {"class_type": "UNETLoader", "inputs": {"unet_name": (QWEN_EDIT if edit else QWEN_IMAGE).filename,
                                                          "weight_dtype": "default"}},
            "38": {"class_type": "CLIPLoader", "inputs": {"clip_name": QWEN_TE.filename, "type": "qwen_image",
                                                          "device": "default"}},
            "39": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN_VAE.filename}},
            "73": {"class_type": "LoraLoaderModelOnly", "inputs": {
                "model": ["37", 0], "lora_name": (QWEN_EDIT_LIGHTNING if edit else QWEN_LIGHTNING).filename,
                "strength_model": 1.0}},
            "66": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["73", 0], "shift": 3.0 if edit else 3.1}},
            "3": {"class_type": "KSampler", "inputs": {
                "seed": p["seed"], "steps": p["steps"], "cfg": p["cfg"], "sampler_name": "euler", "scheduler": "simple",
                "denoise": 1.0, "model": ["75", 0] if edit else ["66", 0], "positive": ["6", 0], "negative": ["7", 0],
                "latent_image": ["58", 0]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["39", 0]}},
            "60": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["8", 0]}},
        }
        if edit:
            g["75"] = {"class_type": "CFGNorm", "inputs": {"model": ["66", 0], "strength": 1.0}}
            g["78"] = {"class_type": "LoadImage", "inputs": {"image": input_name}}
            g["93"] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
                "image": ["78", 0], "upscale_method": "lanczos", "megapixels": p.get("megapixels", 1.0),
                "resolution_steps": 16}}  # VAE /8 and 2×2 patches: keep sides multiple of 16
            g["6"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
                "clip": ["38", 0], "prompt": p["prompt"], "vae": ["39", 0], "image1": ["93", 0]}}
            g["7"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
                "clip": ["38", 0], "prompt": "", "vae": ["39", 0], "image1": ["93", 0]}}
            g["58"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["93", 0], "vae": ["39", 0]}}
        else:
            g["6"] = {"class_type": "CLIPTextEncode", "inputs": {"text": p["prompt"], "clip": ["38", 0]}}
            g["7"] = {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("negative_prompt", "") or " ", "clip": ["38", 0]}}
            g["58"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": p["width"], "height": p["height"],
                                                                     "batch_size": p.get("batch_size", 1)}}
        return g
    if wf.family == "esrgan":
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": input_name}},
            "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": REALESRGAN.filename}},
            "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
            "4": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["3", 0]}},
        }
    if wf.family in ("wan21", "wan22"):
        unet = WAN21_T2V.filename if wf.family == "wan21" else WAN22_TI2V.filename
        vae = WAN21_VAE.filename if wf.family == "wan21" else WAN22_VAE.filename
        g = {
            "37": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
            "38": {"class_type": "CLIPLoader", "inputs": {"clip_name": UMT5.filename, "type": "wan", "device": "default"}},
            "39": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": p["prompt"], "clip": ["38", 0]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("negative_prompt", ""), "clip": ["38", 0]}},
            "48": {"class_type": "ModelSamplingSD3", "inputs": {"shift": 8.0, "model": ["37", 0]}},
            "3": {"class_type": "KSampler", "inputs": {
                "seed": p["seed"], "steps": p["steps"], "cfg": p["cfg"], "sampler_name": "uni_pc", "scheduler": "simple",
                "denoise": 1.0, "model": ["48", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["40", 0]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["39", 0]}},
            "28": {"class_type": "SaveAnimatedWEBP", "inputs": {
                "filename_prefix": prefix, "fps": float(p["fps"]), "lossless": False, "quality": 90, "method": "default",
                "images": ["8", 0]}},
        }
        if wf.family == "wan21":
            g["40"] = {"class_type": "EmptyHunyuanLatentVideo", "inputs": {
                "width": p["width"], "height": p["height"], "length": p["frames"], "batch_size": 1}}
        else:
            lat = {"vae": ["39", 0], "width": p["width"], "height": p["height"], "length": p["frames"], "batch_size": 1}
            if input_name:
                g["56"] = {"class_type": "LoadImage", "inputs": {"image": input_name}}
                lat["start_image"] = ["56", 0]
            g["40"] = {"class_type": "Wan22ImageToVideoLatent", "inputs": lat}
        return g
    raise WorkflowError(f"unknown family {wf.family}")


def public(wf: Workflow, files: set[str] | None = None, nodes: set[str] | None = None) -> dict:
    miss = missing_models(wf, files)
    missing_nodes = [n for n in wf.nodes if nodes is not None and n not in nodes]
    return {
        "id": wf.id, "name": wf.name, "kind": wf.kind, "mode": wf.mode, "family": wf.family,
        "params": wf.params, "memory_gb": wf.memory_gb, "gpu_heavy": wf.gpu_heavy, "source": wf.source,
        "models": [{"kind": m.kind, "filename": m.filename, "repo_id": m.repo_id, "repo_file": m.repo_file,
                    "size_gb": m.size_gb, "license": m.license,
                    "installed": m not in miss} for m in wf.models],
        "install_size_gb": round(sum(m.size_gb for m in miss), 2),
        "available": not miss and not missing_nodes,
        "missing_nodes": missing_nodes,
    }


def comfy_output_path(item: dict) -> Path:
    base = get_settings().outputs_dir / "comfyui"
    sub = item.get("subfolder") or ""
    return (base / sub / item["filename"]).resolve()
