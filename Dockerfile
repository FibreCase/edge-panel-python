# flutter_desk_panel_backend

FROM python:3.14-slim AS builder

WORKDIR /app
RUN pip install uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen


FROM python:3.14-slim AS runtime

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app/*.py /app/app/
COPY web /app/web
ENV PATH="/app/.venv/bin:$PATH" \
    PORT=5000
EXPOSE 5000

CMD ["/app/.venv/bin/python", "app/main.py"]