"""
Stage 3 prototype: Hebrew lexicon-disciplined summarization, via OpenRouter.

Purpose: validate the riskiest assumption in the brief — that a strong
model with a lexicon and a strict prompt can produce Hebrew that passes
the clinical-professional sniff test. If the output here is not
acceptable to a native Hebrew clinical reader, the project's quality bar
is in question and we revisit before building the rest.

Why OpenRouter: one API key, many models. Lets us A/B Stage 3 candidates
(claude-opus-4-7, gpt-5, gemini-2.5-pro, etc.) by changing one env var,
not by swapping SDKs.

Run:
    pip install openai pyyaml
    export OPENROUTER_API_KEY=...                       (bash)
    $env:OPENROUTER_API_KEY = "..."                     (PowerShell)
    python prototype/stage3_test.py

A/B by model:
    MODEL=anthropic/claude-opus-4-7    python prototype/stage3_test.py   (default)
    MODEL=openai/gpt-5                 python prototype/stage3_test.py
    MODEL=google/gemini-2.5-pro        python prototype/stage3_test.py

Output:
    prototype/stage3_output.md          (review with a native Hebrew reader)
"""

import json
import os
import sys
from pathlib import Path

import yaml
from openai import OpenAI

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-opus-4-7"
MAX_TOKENS = 1500

PROTOTYPE_DIR = Path(__file__).parent


def load_lexicon() -> str:
    data = yaml.safe_load((PROTOTYPE_DIR / "lexicon_seed.yaml").read_text(encoding="utf-8"))
    lines = []
    for t in data["terms"]:
        line = f"- {t['en']} → {t['he']}"
        if t.get("forbid"):
            line += f"  (NOT {t['forbid']})"
        if t.get("note"):
            line += f"  [{t['note']}]"
        lines.append(line)
    return "\n".join(lines)


def load_prompt() -> str:
    template = (PROTOTYPE_DIR / "prompt.txt").read_text(encoding="utf-8")
    return template.replace("{lexicon}", load_lexicon())


def load_items() -> list[dict]:
    return yaml.safe_load((PROTOTYPE_DIR / "items.yaml").read_text(encoding="utf-8"))["items"]


def parse_json_response(text: str) -> dict:
    """Tolerate accidental code fences. Fail loudly on malformed JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    return json.loads(text.strip())


def summarize(client: OpenAI, model: str, system_prompt: str, item: dict) -> tuple[dict, object]:
    user_message = (
        "ITEM TO SUMMARIZE\n"
        f"Title: {item['title']}\n"
        f"Source: {item['source']}\n"
        f"Axis: {item['axis']} (A = clinical practice; B = AI and psychology)\n"
        "Abstract:\n"
        f"{item['abstract'].strip()}\n"
    )
    # Reasoning is disabled by default for speed and consistency. Set
    # REASONING=on to allow the model to use its reasoning budget — required
    # for some endpoints (gpt-5, gpt-5-mini, gemini-2.5-pro) that enforce it.
    extra_body = {}
    if os.environ.get("REASONING", "off").lower() != "on":
        extra_body["reasoning"] = {"enabled": False}

    response = client.chat.completions.create(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        response_format={"type": "json_object"},
        extra_headers={
            "HTTP-Referer": "https://github.com/channels-operator-prototype",
            "X-Title": "Channels Operator - Stage 3 prototype",
        },
        extra_body=extra_body,
    )
    text = response.choices[0].message.content or ""
    return parse_json_response(text), response.usage


def render_item_block(idx: int, item: dict, result: dict, usage) -> list[str]:
    return [
        f"## Item {idx}: {item['title']}",
        f"**Axis:** {item['axis']}  ·  **Source:** {item['source']}",
        "",
        "### English abstract",
        item["abstract"].strip(),
        "",
        "### Hebrew draft",
        "",
        f"**Headline finding:** {result.get('headline_finding', '(missing)')}",
        "",
        "**Body:**",
        "",
        result.get("post_body") or "_(model returned empty body — see caveat)_",
        "",
        f"**Hashtags:** {' '.join(result.get('hashtags', []))}",
        "",
        f"**Methodological caveat:** {result.get('methodological_caveat') or '_(none)_'}",
        "",
        f"**Tokens:** in={usage.prompt_tokens}, out={usage.completion_tokens}",
        "",
        "---",
        "",
    ]


def main() -> int:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("OPENROUTER_API_KEY is not set.", file=sys.stderr)
        return 1

    model = os.environ.get("MODEL", DEFAULT_MODEL)
    system_prompt = load_prompt()
    items = load_items()
    client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)

    output: list[str] = [
        "# Stage 3 prototype output",
        "",
        f"Model: `{model}`  ·  Items: {len(items)}  ·  Gateway: OpenRouter",
        "",
        "Read each Hebrew block as if it were going to publish today. Mark",
        "each as: ACCEPT / EDIT / REJECT, and note why. After all five,",
        "decide whether the prompt + lexicon mechanism is viable as-is,",
        "viable with prompt tuning, or not viable.",
        "",
        "---",
        "",
    ]

    total_in = 0
    total_out = 0

    for i, item in enumerate(items, 1):
        print(f"[{i}/{len(items)}] {item['title']}")
        try:
            result, usage = summarize(client, model, system_prompt, item)
            total_in += usage.prompt_tokens
            total_out += usage.completion_tokens
            output.extend(render_item_block(i, item, result, usage))
        except Exception as exc:
            output.extend([
                f"## Item {i}: {item['title']}",
                f"**FAILED:** `{type(exc).__name__}: {exc}`",
                "",
                "---",
                "",
            ])

    output.extend([
        "## Token usage",
        f"- Input tokens:  {total_in:,}",
        f"- Output tokens: {total_out:,}",
        "",
        "Cost estimate: multiply by current OpenRouter pricing for the chosen",
        "model. OpenRouter exposes per-call cost in the API response under",
        "`usage.cost` for some providers — not surfaced here to keep the",
        "prototype minimal.",
        "",
    ])

    slug = model.split("/")[-1]
    out_path = PROTOTYPE_DIR / f"stage3_output_{slug}.md"
    out_path.write_text("\n".join(output), encoding="utf-8")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
