# Throwaway eval image for nvidia/LocateAnything-3B (NON-COMMERCIAL license —
# research/benchmark ONLY, never shipped). Derives from neuthek-backend:latest
# purely to inherit the *validated Blackwell* torch wheel (2.12.0+cu130, sm_120
# confirmed working on the 5070) plus opencv/pillow/torchvision/numpy — so we
# don't risk pulling a torch build that lacks sm_120 kernels.
#
# It ONLY adds the LocateAnything deps on top, in THIS image. The running
# neuthek-backend container keeps transformers==4.49.0 untouched (different
# container filesystem). Build:
#   docker build -f eval/locate_anything/Dockerfile -t neuthek-eval-locate:latest .
FROM neuthek-backend:latest

USER root

# Model card pins (https://huggingface.co/nvidia/LocateAnything-3B). transformers
# is bumped 4.49.0 -> 4.57.1 (the custom trust_remote_code modeling targets it).
# numpy stays at the image's 2.1.3 (the card's 1.25.0 pin is just what NVIDIA
# tested; 2.x is ABI-compatible for this use). accelerate/einops/timm added
# defensively — Qwen2.5 + MoonViT custom code commonly imports them.
RUN /opt/venv/bin/pip install --no-cache-dir \
        transformers==4.57.1 \
        peft \
        decord==0.6.0 \
        lmdb==1.7.5 \
        accelerate \
        einops \
        timm \
        sentencepiece

USER neuthek
