# Channels Operator

Automated content curation platform. Watches sources, drafts professional-quality posts in the channel's target language, asks the operator to approve each one in Telegram, and publishes approved drafts on schedule.

First channel: **פסיכולוגיה היום** — Hebrew, mental-health professionals, AI-and-psychology research.

The product brief lives in [BRIEF.md](BRIEF.md). This README documents implementation choices. Day-to-day running and recovery procedures are in [OPERATIONS.md](OPERATIONS.md).

## Tech decisions

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.13 | Best ecosystem for Telegram, RSS, arXiv, OpenAI/Anthropic SDKs |
| LLM gateway | OpenRouter (OpenAI-compatible API) | One API key, multiple models. Default Stage 3 = `qwen/qwen3.6-plus`, A/B-validated 2026-05-04 |
| Storage | SQLite via SQLAlchemy | Single-writer, zero ops. Schema is Postgres-portable for SaaS migration |
| Scheduler | APScheduler in-process | Hot config reload without restarting; one process per host |
| Telegram | python-telegram-bot v21+ | Two bots (editor + publisher) share one async runtime |
| Container | Single Dockerfile + compose | Operator already runs Docker on Linux |
| Config | YAML hot-reloaded | Channel files re-read at start of each pipeline run |

## Architecture overview

```
sources --> fetcher --> Stage 1 (no AI) --> Stage 2 (cheap AI) --> Stage 3 (strong AI)
                                                                          |
                                          SQLite (drafts, decisions, posts, costs)
                                                          |
operator <-> Editor bot                                   |
                                                          v
                                                   Publisher bot --> Telegram channel
```

See [BRIEF.md §5–6](BRIEF.md) for pipeline detail.

## SaaS-readiness disciplines

The implementation is private-use first, but five design choices keep a future SaaS pivot cheap:

1. **Storage via DAL.** SQLAlchemy with `Engine` URL from config — swap to Postgres later by changing `DATABASE_URL`.
2. **Config loader as an interface.** `FileConfigLoader` reads YAML from disk today; a future `DatabaseConfigLoader` can read from a Postgres row with no caller changes.
3. **Bot tokens as per-channel config.** `editor_bot_token` and `publisher_bot_token` live in `channel.yaml` (with env var fallback for v1). SaaS adds tenant-owned bot tokens by editing config rows.
4. **Cost tracking at per-call grain.** Every LLM call logs (timestamp, channel, stage, model, tokens, USD). Aggregate at query time. Per-tenant billing later requires no schema migration.
5. **Publisher as an interface.** `Publisher` protocol with a `TelegramPublisher` implementation today; `WhatsAppPublisher` etc. slot in as new files later.

## Setup (development)

Requires Python 3.13+ and an OpenRouter API key.

```bash
# Install deps (editable, with dev tools)
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env: fill in OPENROUTER_API_KEY and bot tokens

# Run pipeline once (no scheduler, no bots — useful for testing)
python -m channels_operator.cli pipeline run --channel psychology_today

# Run the full service (scheduler + bots)
python -m channels_operator.main
```

## Setup (production)

```bash
cp .env.example .env
# Edit .env

docker compose up -d
docker compose logs -f
```

## Adding a new channel

1. Copy `channels/psychology_today/` to `channels/<your_channel>/`.
2. Edit `channel.yaml` — set the Telegram channel ID, audience, voice, axes.
3. Edit `sources.yaml` — RSS / PubMed / arXiv queries for your domain.
4. Edit `keywords.yaml` — include / exclude keywords for Stage 1.
5. Edit `lexicon.yaml` if your audience needs term consistency.
6. Edit `prompts/summarize.txt` and `prompts/relevance.txt` to match your channel's voice.
7. Restart, or wait — config is re-read at the next 06:00 fetch.

No code changes. No schema migrations. The pipeline iterates over all channel folders.

## Project layout

```
src/channels_operator/
  config/          channel YAML loader and pydantic models
  sources/         RSS, PubMed, arXiv fetchers
  pipeline/        Stage 1 (filter), Stage 2 (relevance), Stage 3 (summarize), orchestrator
  llm/             LLM client abstraction (OpenRouter default)
  publishing/      Publisher protocol + TelegramPublisher
  telegram/        Editor bot (Telegram-specific operator UX)
  storage/         SQLAlchemy engine, models, repository
  ops/             Daily summary, cost tracking, source health, backups

channels/<channel>/   one folder per channel — pure data, no code
prototype/            standalone scripts for validating risky assumptions
data/                 SQLite + backups (gitignored)
tests/                pytest tests
```

## Build status

The brief defines an 8-phase build order with concrete acceptance criteria — see [BRIEF.md §10](BRIEF.md).

- Phase 0 — Stage 3 prototype validated. Hebrew quality on Qwen 3.6 Plus operator-approved 2026-05-04 against synthetic 5-item test set. Done.
- Phase 1 — End-to-end skeleton (editor bot ↔ publisher bot, hardcoded draft). Operator-validated 2026-05-05 against the test channel. Done.
- Phase 2 — Channel configuration model. Operator-validated 2026-05-05 (loader discovers `psychology_today` and `dev_test`; runtime reads channel name and target ID from YAML). Done.
- Phase 3 — Fetching and Stage 1 filter. Operator-validated 2026-05-05 (511 items fetched across 24 sources, 161 stage-1-passed, dedup confirmed via second run with `stored_new=0`). Done.
- Phase 4 — Stage 2 and Stage 3 implementation. Operator-validated 2026-05-05 (36 Hebrew drafts reviewed in `data/phase4_review.md`, "looks good enough"). Total cost ~$0.20 for the full run. Calibration tweaks deferred to Phase 8: 5% forbidden-lexicon-term leak rate, 30% hashtag-axis mismatch on Axis B items, Haiku conservative on PsyPost framing. Done.
- Phase 5 — Full operator loop. Operator-validated 2026-05-05 (real drafts in DM; all five buttons functional including the edit text-back flow and reject-reason picker; APScheduler running daily fetch / batch DM / publish slots; `/publish_now` admin helper for testing). Done.
- Phase 6 — Multi-channel validation. Operator-validated 2026-05-05. Two channels run side-by-side: 🧠 psychology_today (Hebrew clinical) + 🤖 dev_test (Hebrew AI news, "חדשות AI בשפה פשוטה"). Drafts route to correct destinations; `/draft N`, `/status N`, `/publish_now N` accept either index or channel id. Done.
- Phase 7 — Operations layer. Operator-validated 2026-05-05. Daily summary (cost, queue, source health), `/status` command, daily DB backup, all wired into the scheduler. Done.
- Phase 8 — 3–4 week calibration. Started 2026-05-05. Both channels live: 🧠 psychology_today publishing to public `@psychology_today_il` (4 bootstrap posts seeded); 🤖 dev_test publishing to operator's existing `חדשות AI בשפה פשוטה` channel. Run unattended; review the decision log after ~50–100 operator decisions and tune prompts/sources/lexicon. **In progress.**

## Decisions still open (per brief §12)

These need operator input before Phase 4 begins:

- **Channel privacy** — public from day one, or invite-only during calibration?
- **Citation format** — author-year only, or full citation block?
- **Lexicon AI terms** — Hebrew renderings for `parasocial attachment`, `prompt injection`, `alignment`. The Phase 0 prototype produced transliterations (`פאראסוציאלית` and `פרסוציאלית`); operator decision pending.
- **AI-skeptical framing** — strict neutral reporting, or critical framing allowed in the methodological caveat?

## Prototype artifacts

`prototype/` contains the Phase 0 validation work used to lock in the Stage 3 model choice. The validated Hebrew lexicon (`lexicon_seed.yaml`) and summarization prompt (`prompt.txt`) were copied to `channels/psychology_today/lexicon.yaml` and `channels/psychology_today/prompts/summarize.txt` respectively. The prototype script (`stage3_test.py`) is kept for future regression checks against new model candidates.
