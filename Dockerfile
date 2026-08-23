# Secure Self-Correcting Text-to-SQL Agent — API container.
#
#   docker build -t textsql-agent .
#   docker run --rm -p 8000:8000 \
#     -e OLLAMA_HOST=http://host.docker.internal:11434 \
#     --add-host=host.docker.internal:host-gateway \
#     textsql-agent
#
# Two things this image deliberately does NOT contain, because pretending
# otherwise would produce a container that fails the moment anyone runs it:
#
#   The model. Qwen2.5-Coder-3B is ~2 GB and Ollama is a service, not a
#   library. The container talks to an Ollama running on the host - which is
#   also how you would deploy it, pointing at a model server rather than
#   embedding one.
#
#   The full dataset. The Olist CSVs are ~60 MB and gitignored, so the image
#   builds its database from the committed 1 MB sample. Mount the real data to
#   use it:  -v "$(pwd)/data:/app/data" -e DATA_SOURCE=full

FROM python:3.11-slim

# Faster, quieter, and no .pyc litter in the layer.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_SOURCE=sample

WORKDIR /app

# Dependencies first and alone, so a source edit does not invalidate the
# pip layer. The lock file, not requirements.txt - a project claiming
# deterministic regression gating cannot install floating versions.
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt

COPY src/ ./src/
COPY evals/ ./evals/
COPY scripts/ ./scripts/
COPY data/sample/ ./data/sample/
COPY app.py DECISIONS.md ./

# Build the database at image build time rather than on every start, so the
# container comes up in seconds.
RUN python src/db.py

# Run as a non-root user. The agent only ever reads, and the connection is
# opened ?mode=ro, but defence in depth is the whole argument of this project
# and it would be odd to abandon it at the container boundary.
RUN useradd --create-home --uid 10001 agent && chown -R agent:agent /app
USER agent

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"

CMD ["uvicorn", "api:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
