# MinerU compatibility notes for ProjectRAG V0.1

ProjectRAG treats MinerU as an external parsing engine.

Official usage verified during V0.1 development:

- CLI basic form: `mineru -p <input_path> -o <output_path>`
- input may be PDF, image, DOCX, PPTX, XLSX or a directory;
- when `--api-url` is supplied, the CLI can connect to an existing MinerU FastAPI service;
- output includes Markdown and structured artifacts depending on backend;
- `content_list.json` is the preferred ProjectRAG secondary-development input because readable blocks include `page_idx`; text blocks may include `text_level` for heading hierarchy.

Official references:

- https://opendatalab.github.io/MinerU/usage/quick_usage/
- https://opendatalab.github.io/MinerU/reference/output_files/
- https://github.com/opendatalab/MinerU

ProjectRAG deliberately searches output directories recursively rather than assuming one exact nested output path, reducing coupling to MinerU output-directory changes.
