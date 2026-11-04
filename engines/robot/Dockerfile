# Motion server for robot suites: Kimodo (text to motion) and MuJoCo (physics checks). Built on the local NVIDIA PyTorch
# image, the one with a CUDA build for this GPU. Kimodo is pinned to a commit; its demo UI and SOMA extras are left out.
FROM nvcr.io/nvidia/pytorch:26.09-py3
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
ARG KIMODO_COMMIT=e1c219c
RUN git clone -q https://github.com/nv-tlabs/kimodo.git /opt/kimodo \
 && git -C /opt/kimodo checkout -q ${KIMODO_COMMIT}
COPY requirements.txt /opt/requirements.txt
RUN python -m pip install -q -r /opt/requirements.txt \
 && SKIP_MOTION_CORRECTION_IN_SETUP=1 python -m pip install -q --no-deps -e /opt/kimodo
COPY app /opt/robot/app
WORKDIR /opt/robot
USER 1000:1000
ENV HOME=/tmp HF_HUB_CACHE=/models/robot HF_HOME=/tmp/hf HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 LOCAL_CACHE=true TEXT_ENCODER_MODE=local
EXPOSE 5000
ENTRYPOINT ["python", "-m", "uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "5000", "--no-access-log"]
