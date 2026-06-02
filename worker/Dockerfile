FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI via Anthropic's official installer.
# Resolve the dangling symlink to a stable system path so any user can run it.
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && cp "$(readlink -f /root/.local/bin/claude)" /usr/local/bin/claude \
    && chmod +x /usr/local/bin/claude \
    && /usr/local/bin/claude --version

# Non-root user so `--dangerously-skip-permissions` is allowed by the CLI.
# Sandboxing is at the container level (gVisor on Scaleway Jobs); inside the
# container, the worker should have full freedom to use any tools.
RUN useradd -m -u 1000 runner

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install -e .

USER runner
ENV HOME=/home/runner

CMD ["python", "-m", "worker"]
