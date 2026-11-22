# The clabe hub: follows a fleet of rigs (through their daemons) and serves the fleet page.
# Build from the repository root:  docker build -f docker/hub/Dockerfile -t clabe-hub .
# Run:  docker run -p 8080:8080 --env-file docker/hub/.env clabe-hub

FROM mcr.microsoft.com/dotnet/sdk:10.0 AS ui
WORKDIR /src
COPY ui-web/ ui-web/
RUN dotnet publish ui-web/Clabe.Web.csproj -c Release -o /out -nologo -v q

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy GIT_PYTHON_REFRESH=quiet
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --extra otel --no-install-project
COPY src/ src/
COPY --from=ui /out/wwwroot src/clabe/server/static
RUN uv sync --frozen --no-dev --extra otel --no-editable
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/openapi.json')"
CMD ["clabe", "hub", "run"]
