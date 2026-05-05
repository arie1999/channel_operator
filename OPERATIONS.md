# Operations

Day-to-day running of the Channels Operator. Reference doc for the
operator (currently a single person). Pair with [BRIEF.md](BRIEF.md)
(product intent) and [README.md](README.md) (architecture).

---

## What's running

The system is a single Python process that:

- Polls Telegram for operator interactions (editor bot)
- Fires scheduled jobs via APScheduler
- Calls OpenRouter for Stage 2 / Stage 3 LLM work
- Persists everything to SQLite

While the process is alive, the channels run themselves. If the process
dies, fetches and publishes stop until you restart it. Operator review
in Telegram still works against pending state when restarted.

---

## Daily operation (zero touch)

Per channel, on its configured schedule (Asia/Jerusalem):

| Time | What happens |
|---|---|
| 03:00 | DB snapshot to `data/backups/` (last 30 kept) |
| 06:00 | Fetch + Stage 1 + Stage 2 + Stage 3 → drafts queued in DB |
| 07:00 | Up to `cadence.drafts_per_day_max` pending drafts DM'd to operator |
| each `publish_times_local` slot | Oldest approved draft published to that channel's Telegram destination |
| 21:00 | Daily summary DM'd to operator |

Your only role: open Telegram once a day, review what arrives, click
✅ Approve / ✏️ Edit / ❌ Reject / ⏭️ Skip.

If anything breaks, the 21:00 summary will show:
- `silent ≥7d` source list (broken feeds)
- Cost spike (unexpected high tokens)
- Error counts

---

## Starting the bot

### Foreground (testing, manual)

```powershell
cd "C:\Development\Channels operator"
py -m channels_operator.main
```

Pydantic-settings auto-loads `.env` — no manual `source .env`.

The bot stays running in that terminal. **Don't close the window.**
Stop with Ctrl+C.

### Background (production)

**Recommended: Linux host with Docker.** Your brief assumes one
([BRIEF.md §8](BRIEF.md)).

```bash
git clone https://github.com/arie1999/channel_operator.git
cd channel_operator
cp .env.example .env
# Fill in OPENROUTER_API_KEY, EDITOR_BOT_TOKEN, PUBLISHER_BOT_TOKEN,
# OPERATOR_TELEGRAM_USER_ID. Save.

docker compose up -d        # start in background
docker compose logs -f      # follow logs (Ctrl+C to detach)
docker compose down         # stop
docker compose restart      # after channel.yaml or .env changes
```

Container has `restart: unless-stopped`, so it survives reboots.

**Windows Task Scheduler alternative** if you must run on Windows:
- Action: start program
  - Program: `C:\Users\Arie\AppData\Local\Programs\Python\Python313\python.exe`
  - Arguments: `-m channels_operator.main`
  - Start in: `C:\Development\Channels operator`
- Trigger: at log on
- Settings: restart on failure, restart up to 3 times

---

## Stopping

- **Foreground:** Ctrl+C in the terminal.
- **Docker:** `docker compose down`.
- **Task Scheduler:** end task in Task Scheduler GUI.

---

## What requires a bot restart vs hot-reloads

### Hot-reload (no restart needed)

These are re-read at the start of every pipeline run (next 06:00 fetch,
or next manual `pipeline run` invocation):

- `channels/<id>/sources.yaml`
- `channels/<id>/keywords.yaml`
- `channels/<id>/lexicon.yaml`
- `channels/<id>/prompts/relevance.txt`
- `channels/<id>/prompts/summarize.txt`

Edit, save, wait. To force immediate effect: run the pipeline manually
(see "Manual pipeline runs" below).

### Restart required

- `channels/<id>/channel.yaml` (id, name, telegram_channel_id, cadence, axes, model selection, emoji, bot tokens)
- Adding or removing a channel folder under `channels/`
- Anything under `src/`
- `.env`

After changing these: `docker compose restart` (or stop and start the
foreground process).

---

## Telegram editor bot commands

Indices match the alphabetical order of channel ids; `/start` shows the
mapping.

| Command | What it does |
|---|---|
| `/start` | List channels with index, emoji, pending/approved counts |
| `/help` | Same as `/start` |
| `/draft [N\|id]` | Send pending drafts (up to per-channel max). No arg = all channels |
| `/status [N\|id]` | Daily summary (pipeline, queue, cost, source health). No arg = all channels |
| `/publish_now [N\|id]` | Publish the oldest approved draft now (ops helper for testing) |
| `/cancel` | Abort an in-progress edit |

Example: `/draft 1` sends only AI-news drafts; `/status 2` shows only
the psychology channel summary.

### Buttons on each draft DM

- ✅ **Approve** → `state=approved`, queued for the next publish slot.
- ✏️ **Edit** → bot prompts for revised text; your next text message replaces the body. Re-displays the draft for re-approval.
- ❌ **Reject** → shows reason picker (Not relevant / Low quality / Duplicate / Bad translation / Other). Logged to `decisions` table.
- 🔗 **Open Source** → just opens the URL.
- ⏭️ **Skip** → leaves the draft pending; reappears in the next batch.

---

## Manual CLI commands

Run from the project root:

```powershell
# Full pipeline run for a channel (Stage 1 + 2 + 3)
py -m channels_operator.cli pipeline run --channel psychology_today

# Just Stage 1 (recency + dedup + keywords). No AI cost.
py -m channels_operator.cli pipeline run --channel dev_test --stages stage1

# Cap items processed by Stages 2 + 3 (cheap test runs)
py -m channels_operator.cli pipeline run --channel dev_test --limit 5

# Pipeline + write a markdown review of pending drafts
py -m channels_operator.cli pipeline run --channel dev_test \
    --stages stage3 --review-out data/review.md
```

`--stages` accepts a comma-separated subset of `stage1,stage2,stage3`.
`--limit` caps how many Stage-1-passed items get scored, and how many
Stage-2-passed items get summarized, in this run.

---

## Adding a new channel

1. **Create the destination Telegram channel.** Add `@channelsoperatorpublisher_bot` as an administrator with **Post Messages** permission. Grab its chat ID (forward any message to [@RawDataBot](https://t.me/RawDataBot) and copy `forward_from_chat.id`).

2. **Copy a working channel folder as the starting point:**

   ```bash
   cp -r channels/psychology_today channels/<new_channel_id>
   ```

3. **Edit `channels/<new_channel_id>/channel.yaml`** — at minimum:
   - `id` — must match the folder name
   - `name` — shown in DM headers
   - `language` — `he`, `en`, etc.
   - `emoji` — short emoji shown in headers (helps you scan)
   - `telegram_channel_id` — the chat ID from step 1, or `@username`
   - `audience` — guides the prompts
   - `voice` — guides the prompts
   - `axes` — at least one
   - `cadence` — fetch time, publish slots, drafts per day, timezone

4. **Tune `sources.yaml` / `keywords.yaml` / `lexicon.yaml` / `prompts/`** for the new channel's domain. The prototype's psychology configs are a good starting reference.

5. **Restart the bot.** New channel appears in `/start` with its own index. Daily fetch starts populating it on the next 06:00 IL.

No code changes. No DB migrations. The pipeline iterates over all
channel folders.

---

## File layout

```
.env                     credentials. NEVER commit (gitignored).
.env.example             template. Safe to commit.
BRIEF.md                 product intent (don't edit lightly)
README.md                architecture decisions
OPERATIONS.md            this file

channels/<id>/           channel data, no code
  channel.yaml             identity, cadence, model selection
  sources.yaml             RSS / PubMed / arXiv feeds
  keywords.yaml            include / exclude lists
  lexicon.yaml             domain terminology (Hebrew clinical, etc.)
  prompts/relevance.txt    Stage 2 system prompt
  prompts/summarize.txt    Stage 3 system prompt with {lexicon} slot

src/channels_operator/   Python source
  main.py                  entrypoint
  cli.py                   manual pipeline / admin commands
  scheduler.py             APScheduler jobs
  config/                  YAML loader, pydantic models
  sources/                 RSS, PubMed, arXiv fetchers
  pipeline/                Stage 1 / 2 / 3 + orchestrator
  llm/                     OpenRouter client + provider abstraction
  publishing/              TelegramPublisher
  telegram/                editor bot, message formatting
  storage/                 SQLAlchemy models + repository
  ops/                     daily summary, cost, source health, backup

prototype/               Phase 0 artifacts (model A/B for Stage 3)
data/                    GITIGNORED runtime data
  channels.db              SQLite — items, drafts, decisions, cost log
  backups/                 daily snapshots, last 30 days
```

---

## Recovery scenarios

### Bot won't start

Check the error in the terminal output:
- `OPENROUTER_API_KEY not set` → fill in `.env`
- `EDITOR_BOT_TOKEN missing` → likewise
- `Chat not found` → publisher bot was removed from a channel; re-add it
  as admin (channel info → Administrators → Add)
- `Network Retry Loop ... TimedOut` → Telegram unreachable; wait or
  check connectivity. PTB retries 3 times then aborts.

### A source keeps erroring

Check the daily summary's source health line. If a source has been
silent ≥7 days, edit its URL in `channels/<id>/sources.yaml` (no
restart needed; takes effect at the next fetch). If you can't find a
working RSS, comment the source out — it's not needed.

### A draft was approved by mistake

If it hasn't been published yet (state = `approved`), you can:
- Wait for the publish slot to fire — the post goes out.
- Or clear it manually: connect to `data/channels.db` with any SQLite
  client and `UPDATE drafts SET state='rejected' WHERE id=N`.

If it's already been published, delete the message in the Telegram
channel manually. The DB state stays `published` either way.

### DB is corrupt or you need to roll back

```bash
# Stop the bot first
docker compose down

# Pick a backup
ls data/backups/

# Restore (overwrites current DB)
cp data/backups/channels_20260505_030000.db data/channels.db

# Restart
docker compose up -d
```

You'll lose any decisions made between the backup time and now.

### You want a clean slate (queue too noisy)

Mark all pending drafts as skipped:

```sql
UPDATE drafts SET state='skipped' WHERE state='pending';
```

Run via any SQLite client against `data/channels.db`. Tomorrow's
fetch repopulates fresh drafts.

---

## Cost monitoring

Per-call usage is logged in `cost_log` (table). The daily summary
includes today + month-to-date USD estimates from a hardcoded rate
table in `src/channels_operator/ops/cost.py`. Update that table when
OpenRouter pricing changes.

To inspect cost manually:

```python
# In a Python REPL with project loaded
from channels_operator.storage.repository import cost_summary
cost_summary("psychology_today")
# {stage: {model: {calls, prompt_tokens, completion_tokens}}}
```

Operating cost target per [BRIEF.md §2](BRIEF.md): under $20/month
across all channels. Current Stage 3 model (Qwen 3.6 Plus) is
~$0.36/month for one channel at 5 drafts/day, so there's substantial
headroom for adding more channels.

---

## Updating the deployment

```bash
cd channel_operator
git pull
docker compose up -d --build    # rebuild image and restart
```

If `pyproject.toml` changed, the image rebuild picks up new deps.

---

## When to escalate (back to me)

Send a message with the symptom plus a copy of relevant log lines:

- After ~50–100 operator decisions accumulate (~end of week 2 of live
  use): time to look at the decision log, prune low-yield sources,
  tighten prompts where you keep editing the same way, expand the
  lexicon based on terms you keep correcting.
- A source has been silent ≥7d and you can't find a working URL.
- The pipeline produces a clear regression — drafts that used to be
  acceptable are now consistently bad.
- You want to add a real third channel and want a sanity check on the
  config before going live.
- Anything breaks in a way that doesn't match the recovery scenarios
  above.

For day-to-day operation: it's yours.
