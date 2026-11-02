# Sandbox for code run from the chat and workspaces: Python data/document stack + Node (docx, pptxgenjs) + pandoc +
# LibreOffice + TeX Live (pdflatex/xelatex/lualatex, latexmk, biber)
# (headless conversions used by Agent Skills). Containers run with no network, read-only root, non-root user,
# memory/CPU/pids limits and a time limit (see engine-manager sandbox_spec).
FROM python:3.12.16-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MPLBACKEND=Agg \
    NODE_PATH=/usr/local/lib/node_modules HOME=/tmp
RUN apt-get update && apt-get install -y --no-install-recommends \
      nodejs npm pandoc libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
      fonts-dejavu fonts-liberation poppler-utils qpdf imagemagick ghostscript socat \
      texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended texlive-fonts-extra \
      texlive-lang-spanish texlive-lang-english texlive-xetex texlive-luatex texlive-science texlive-pictures \
      texlive-bibtex-extra texlive-plain-generic texlive-publishers latexmk biber lmodern cm-super \
      fonts-crosextra-carlito fonts-crosextra-caladea \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && pip freeze > /opt/sandbox-freeze.txt
RUN npm install -g docx@9.15.1 pptxgenjs@3.12.0 && npm cache clean --force
RUN useradd -u 1000 -m -d /home/sandbox sandbox
USER 1000:1000
WORKDIR /work
CMD ["python", "--version"]
