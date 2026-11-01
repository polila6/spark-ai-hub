# ML worker on the NVIDIA NGC PyTorch image (aarch64 + CUDA for GB10). Torch from the base is kept.
FROM nvcr.io/nvidia/pytorch:26.09-py3
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip list --format=freeze | grep -iE '^(torch|torchvision|torchaudio|numpy|pillow)==' > /tmp/constraints.txt; \
    pip install -c /tmp/constraints.txt -r /tmp/requirements.txt && pip freeze > /opt/mlworker-freeze.txt
WORKDIR /srv
COPY app ./app
USER 1000:1000
ENV HOME=/cache
EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100", "--no-access-log"]
