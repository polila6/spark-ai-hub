# ComfyUI pinned release on the NVIDIA NGC PyTorch image (CUDA 13 / aarch64, as in the DGX Spark playbook).
FROM nvcr.io/nvidia/pytorch:26.09-py3
ARG COMFYUI_VERSION=v0.42.5
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HUB_OFFLINE=1 DO_NOT_TRACK=1
RUN git clone --depth 1 --branch ${COMFYUI_VERSION} https://github.com/comfyanonymous/ComfyUI.git /opt/ComfyUI
WORKDIR /opt/ComfyUI
# Keep NGC torch: drop torch* lines from ComfyUI requirements and constrain to the installed builds.
RUN pip list --format=freeze | grep -iE '^(torch|torchvision|torchaudio|numpy|pillow)==' > /tmp/constraints.txt; \
    grep -viE '^(torch|torchvision|torchaudio)([<>=~ ]|$)' requirements.txt > /tmp/req.txt; \
    pip install -c /tmp/constraints.txt -r /tmp/req.txt && pip freeze > /opt/comfyui-freeze.txt
RUN mkdir -p models output input user temp && chown -R 1000:1000 /opt/ComfyUI
USER 1000:1000
ENV HOME=/opt/ComfyUI/user
EXPOSE 8188
CMD ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188", "--disable-auto-launch", "--preview-method", "none"]
