# Ollama Local AI Analyzer

This tool is a local read-only analysis helper for this project.

It reads selected project files, combines them with a read-only prompt, sends the combined prompt to Ollama through the Ollama CLI, and saves the analysis report under `reports/ai_analysis/`.

## Non-Negotiable Boundaries

- Do not modify project files.
- Do not delete files.
- Do not move files.
- Do not create project code or runtime files.
- Do not commit or push.
- Do not modify `runtime` files.
- Do not modify any `rules.json`.
- Do not connect Queue Commit, Execution Commit, SendOrder, Broker, Kiwoom, GUI, or automatic trading execution logic.
- Reports only.

## Model Choice

- Default: `qwen2.5-coder:3b`
- Optional larger model: `qwen2.5-coder:7b`

Use 3B for quick local impact checks and call-flow summaries. Use 7B for broader dependency analysis or complicated multi-file reasoning when your local machine can handle it.

## Usage

From the project root:

```bat
AI_ANALYZE.bat path\to\file.py
```

Direct Python usage:

```bat
python tools\ai_analyzer\ai_analyze.py path\to\file.py
python tools\ai_analyzer\ai_analyze.py path\to\file.py --model qwen2.5-coder:7b
python tools\ai_analyzer\ai_analyze.py path\to\file.py --prompt call_flow
```

Available prompts:

- `impact_analysis`
- `call_flow`
- `dependency`

The generated report path is printed after analysis completes.

