FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app

ENV UV_SYSTEM_PYTHON=1 UV_COMPILE_BYTECODE=1

COPY requirements.txt .
RUN uv pip install -r requirements.txt
RUN playwright install --with-deps chromium

ENV DOCKER_CONTAINER=1

RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080

COPY . .

CMD ["python", "-m", "agent"]
