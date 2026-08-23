# MinerU on macOS for ProjectRAG V0.1

MinerU is intentionally installed separately from the ProjectRAG `.venv`.

## Automatic local install

From the ProjectRAG directory:

```bash
./install_mineru_mac.sh
```

This creates:

```text
.mineru-venv/bin/mineru
```

ProjectRAG automatically detects that path on the next start.

## Official manual form

The current MinerU documentation recommends pip/uv installation and lists `mineru[all]` as the general package for Windows/Linux/macOS:

```bash
pip install --upgrade pip
pip install uv
uv pip install -U "mineru[all]"
```

ProjectRAG can also point to any other executable:

```bash
export MINERU_BIN=/absolute/path/to/mineru
./run.sh
```

## Backend

Leave `MINERU_BACKEND` blank to use MinerU's normal selection.

For CPU pipeline mode:

```bash
export MINERU_BACKEND=pipeline
./run.sh
```

If a persistent MinerU API is already running:

```bash
export MINERU_API_URL=http://127.0.0.1:8000
./run.sh
```

ProjectRAG then invokes the CLI with `--api-url` rather than coupling its application code to MinerU internals.

Official docs used for V0.1 compatibility:

- https://opendatalab.github.io/MinerU/quick_start/
- https://opendatalab.github.io/MinerU/usage/quick_usage/
