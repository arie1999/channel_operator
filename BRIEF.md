# Telegram Curation Platform — Product Brief

**For:** Claude Code implementation
**Status:** Pre-build brief
**Owner:** Arie

---

## 1. What we're building

A small automated platform that watches relevant sources, drafts professional-quality posts in the target language, asks the human operator to approve each one in Telegram, and publishes approved drafts to the right Telegram channel on a schedule.

The first channel is **"פסיכולוגיה היום"** (Psychology Today) — Hebrew, professional audience, mental-health and AI-in-mental-health content. The platform must be designed so that adding a second, third, or tenth channel later is a configuration change, not a rewrite.

This is a single-operator system. Cost should be small (single-digit dollars per month per channel). It should run unattended day-to-day; the operator only opens Telegram to approve drafts.

---

## 2. What success looks like

- Operator opens Telegram once a day, spends 5–10 minutes approving or rejecting 4–8 drafts.
- The channel publishes 4–5 high-quality posts per week at consistent times.
- Posts read like they were written by a knowledgeable professional, not a translation engine.
- Adding a new channel takes under 30 minutes: write a config file, point it at sources, define the audience and tone, restart, done.
- The system tells the operator when something breaks; it doesn't fail silently.
- Total monthly cost stays under $20 even with multiple active channels.

---

## 3. The first channel — Psychology Today

### Identity
- **Name:** פסיכולוגיה היום
- **Language:** Hebrew
- **Audience:** Hebrew-speaking mental-health professionals — clinical psychologists, psychotherapists, advanced graduate students, clinical researchers in Israel.
- **Voice:** Sober, professional, evidence-aware. Reads like a colleague briefing colleagues. No hype, no second-person ("you" / "אתם"), no emojis in the post body.
- **Cadence:** 4–5 posts per week.

### What goes in (two content axes)
- **Axis A — Clinical research and practice.** New psychotherapy research, attachment, mentalization, trauma, infant mental health, theoretical/psychodynamic ideas, meta-analyses with clinical implications. Hashtags: `#מחקר` `#פרקטיקה`.
- **Axis B — AI and psychology.** Large language models in therapeutic contexts, ethics and regulation, AI-related risks (psychosis, parasocial attachment), professional tools, computational psychiatry. Hashtags: `#AI_קליני` `#AI_מחקר`.

Target mix: roughly 60% Axis A, 40% Axis B. The system should track recent posts and gently rebalance when the mix drifts.

### What stays out
- Pop-psychology, self-help, life-coach content.
- Opinion pieces with no underlying data or argument.
- Animal-only studies unless the translational link is explicit.
- Anything aimed at general consumers rather than professionals.

### Sources
The platform should pull from a mix of academic feeds, professional publications, and AI-research outlets. Initial source list is a starting point; the operator will add and remove sources over time. See §6 for how source configuration should work.

Starting sources for Psychology Today:
- PubMed RSS feeds for psychotherapy, attachment, trauma, mentalization queries
- PsyArXiv
- BPS Research Digest
- Nature Human Behaviour
- APA Monitor
- arXiv (CS.CL and CS.HC, filtered for mental-health terms)
- JMIR Mental Health
- Nature Mental Health
- Stanford HAI news
- Anthropic, OpenAI, and Google DeepMind research blogs

### Hebrew quality bar
This is non-negotiable. The audience will detect machine-translation tells immediately and unsubscribe. Three guardrails:

1. **Lexicon.** A Hebrew terminology dictionary (initially based on Shapira et al. 2021 plus operator additions) is injected into the summarization prompt so terms map consistently. `attachment` always becomes `התקשרות`, `mentalization` becomes `מנטליזציה`, etc.
2. **Style rules in the prompt.** No second-person, no hype words, no first-person, no rhetorical questions, no emoji in post body, no "X reveals…" / "X shows…" headline conventions.
3. **Human approval for the first month.** Every draft passes through the operator. After ~50–100 decisions the prompt and lexicon get tuned with what was rejected and edited.

### Post structure
Each published post follows this shape:

- One sentence: the finding or claim, in plain professional Hebrew.
- Two to three sentences: methodology and context — sample, design, what was measured.
- One to two sentences: clinical or practical implication.
- Hashtags.
- Source link.

Length target: 120–180 Hebrew words in the body. If the system can't say it well in that range, it's better to skip the item than pad it.

---

## 4. How adding a new channel works

The platform is **channel-agnostic at its core.** Psychology Today is the first occupant of a generic infrastructure. Anything that's specific to "psychology, in Hebrew, for clinicians" lives in the channel's configuration, not in the code.

### What a channel definition contains
A channel config (one file per channel, human-readable, ideally YAML) holds everything that makes the channel what it is:

- Identity: name, target Telegram channel, language, audience description, voice description.
- Source list with per-source axis or topic hints.
- Include/exclude keywords and any hard filters.
- The relevance prompt tailored to this channel's audience.
- The summarization prompt tailored to this channel's voice and language.
- The lexicon (if the language or domain needs term consistency).
- Cadence: how many posts per week, which times, how many drafts to send the operator per day.
- Per-axis or per-topic balance targets if the channel has them.

Illustrative shape (Claude Code is free to adjust the exact field names):

```yaml
id: psychology_today
name: פסיכולוגיה היום
language: he
telegram_channel_id: "@psychology_today_il"
audience: |
  Hebrew-speaking mental-health professionals — psychologists,
  psychotherapists, advanced students, clinical researchers.
voice: |
  Sober, evidence-aware, professional. No hype, no second-person.
axes:
  - id: A
    label: clinical_practice
    hashtags: ["#מחקר", "#פרקטיקה"]
    target_share: 0.6
  - id: B
    label: ai_psychology
    hashtags: ["#AI_קליני", "#AI_מחקר"]
    target_share: 0.4
sources_file: sources/psychology_today.yaml
keywords_file: keywords/psychology_today.yaml
lexicon_file: lexicons/hebrew_clinical.yaml
prompts:
  relevance: prompts/psychology_today_relevance.txt
  summarize: prompts/psychology_today_summarize.txt
cadence:
  posts_per_week_target: 5
  drafts_per_day_max: 5
  publish_times_local: ["08:00", "13:00", "19:00"]
  timezone: Asia/Jerusalem
```

### What stays shared across channels
- The fetching engine (RSS, arXiv, web).
- The three-stage filter and summarization pipeline.
- The Telegram editor bot (one operator inbox, drafts labeled with channel name and color/emoji).
- The Telegram publisher bot (one bot that is admin of all channels, posts to whichever channel each approved draft belongs to).
- The decision log, source-health tracking, and daily summary message.
- The cost-tracking and error-alerting layer.

### The operator's view across channels
When multiple channels are active, the editor bot still sends drafts to one inbox. Each draft is clearly labeled with the channel it belongs to (header line, distinct emoji per channel). The operator approves, edits, or rejects per draft; the system routes each approved post to the correct Telegram channel.

A `/status` command in the editor bot returns a quick per-channel summary: drafts pending, posts published this week, current axis mix, last error.

---

## 5. How the system processes a candidate item

The pipeline runs once a day, early morning (06:00 local for Psychology Today). For each channel, the same three-stage flow applies:

**Stage 1 — Cheap filter, no AI.** Fetch new items from the channel's sources. Drop anything older than 14 days, anything seen before, anything with no abstract, anything that hits the channel's exclusion keywords, and anything that doesn't hit at least one inclusion keyword. This catches the obvious noise without spending money.

**Stage 2 — Quick AI scoring.** Send each surviving item to a cheap, fast model with the channel's relevance prompt. Get back a 0–10 relevance score, a topic tag, an axis assignment, and an audience-fit judgment. Only items scoring 7 or higher with a professional audience fit go forward.

**Stage 3 — Quality summarization.** Send each surviving item to a stronger model with the channel's summarization prompt and lexicon. Get back the final post text in the channel's language, hashtags, a one-sentence headline finding, and (if applicable) a methodological caveat the operator should see.

The drafts queue up. At 07:00 the editor bot batches up to the channel's daily maximum and sends them to the operator, one message per draft, each with action buttons.

---

## 6. The operator workflow

Every draft sent to the operator is one Telegram message with:

- A header line: channel name, axis or topic tag, relevance score.
- The proposed post text exactly as it would publish.
- A small block with the source name, the source link, and any methodological caveat the system flagged.
- Action buttons: **Approve**, **Edit**, **Reject**, **Open Source**, **Skip**.

**Approve.** The draft is queued for the next publish slot.
**Edit.** The bot prompts the operator to send revised text. The revision replaces the draft, the bot asks again Approve / Reject. Edits are saved as training data for prompt improvement.
**Reject.** The bot shows a small list of reasons (not relevant / low quality / duplicate / bad translation / other) and saves the choice.
**Open Source.** Just a link, no state change.
**Skip.** The item disappears from this batch but reappears tomorrow.

Approved posts publish at the channel's configured slots. The operator never has to touch the channel directly.

---

## 7. What "good" output looks like

Claude Code should treat these as quality gates, not suggestions. If the summarization stage can't meet them on a given item, the item should be dropped, not softened.

- The Hebrew (or target language) is fluent enough that a native professional reader wouldn't suspect it was machine-generated.
- Terms from the lexicon are used consistently — not paraphrased or substituted.
- The post leads with the finding, not with throat-clearing ("מחקר חדש בודק…").
- Methodology is mentioned concretely (sample size, design type), not handwaved.
- Limitations are noted when they materially affect interpretation.
- No claims appear in the post that aren't grounded in the source.
- Length stays in the channel's target range. A short, clear post beats a padded one.
- Hashtags match the axis the item was classified into. No hashtag spam.

---

## 8. Operational requirements

**Schedule.** Daily fetch in the early morning local time (per channel). Drafts sent to operator about an hour later. Publishing slots fixed per channel.

**Reliability.** Each pipeline run is logged. Source-level metrics are tracked (items fetched, items passed each stage). If a source returns nothing new for 7 days, alert the operator. If any AI call or Telegram call fails after retries, alert the operator with the item ID and error.

**Cost tracking.** The system records token usage per channel per day. The daily summary message includes month-to-date estimated cost.

**Backups.** Daily backup of the queue and decision log. Keep 30 days.

**Idempotency.** Re-running the pipeline within the same day should not produce duplicate drafts.

**Configuration reload.** Changes to channel configs, source lists, keyword lists, lexicons, and prompts should take effect on the next pipeline run without restarting any long-running process.

**Secrets.** API keys and bot tokens live in environment variables, never in config files committed to source control.

**Hosting.** The operator already runs a Linux host with Docker. The system should be deployable as a small service on that host (single container or small compose file is fine). It should also be possible to run the pipeline manually on demand for testing.

---

## 9. What's deliberately out of scope (v1)

- Engagement analytics or post-performance dashboards.
- Cross-posting to other platforms (X, LinkedIn, Bluesky, etc.).
- Any handling of replies, comments, or DMs to the channels.
- Image or chart generation.
- Audio versions.
- Multi-operator approvals.
- A web UI of any kind. Telegram is the only operator interface.

These are notes for the future, not features to ship now.

---

## 10. Build order and acceptance criteria

Each phase ends with a concrete check. Don't move to the next phase until the current one's check passes.

### Phase 1 — Skeleton end-to-end
Build the smallest possible loop: editor bot sends a hard-coded fake draft to the operator with action buttons; approving it triggers the publisher bot to post a hard-coded message to the Psychology Today channel.

**Done when:** the operator clicks Approve in their DM and a real post appears in the channel within seconds, with no other moving parts.

### Phase 2 — Channel configuration model
Introduce the channel config concept. Psychology Today is defined as a config file. The skeleton from Phase 1 reads its identity (channel ID, name) from the config rather than hardcoding it.

**Done when:** changing the target Telegram channel in the config file (and restarting if needed) routes new posts to the new channel without code changes. A second dummy channel config can be added and the system processes it as a separate logical entity.

### Phase 3 — Fetching and Stage 1 filter
Wire up at least three real sources for Psychology Today (one PubMed query, one arXiv query, one straightforward RSS). Stage 1 keyword and recency filtering runs. Items get persisted with their state.

**Done when:** running the pipeline once produces a populated queue of Stage-1-passed items, with no AI calls yet, and rerunning produces no duplicates.

### Phase 4 — Stage 2 and Stage 3
Wire up the relevance scoring and the Hebrew summarization. Lexicon injection works. Output is JSON-validated. Failures retry with backoff.

**Done when:** a manual run on 10 known items produces 10 Hebrew summaries that the operator reviews and judges acceptable in tone, terminology, and accuracy.

### Phase 5 — Full operator loop
Editor bot integration is complete: real drafts flow to the operator with all five action buttons working. Approved drafts publish at the configured slots. Decisions are logged.

**Done when:** an unsupervised 24-hour run produces a queue, the operator approves a subset over the day, and the approved posts appear in the channel on schedule.

### Phase 6 — Multi-channel infrastructure validation
Add a second channel config (can be a private test channel with toy sources). Run the pipeline. Confirm both channels' drafts appear in the operator's inbox correctly labeled, and approvals route to the correct destination.

**Done when:** two channels run side-by-side through one pipeline run, and the operator can tell drafts apart and approve them into the right targets.

### Phase 7 — Operations layer
Logging, daily summary message, cost tracking, source-health alerts, backups, `/status` command.

**Done when:** the operator can run the system for a full week without checking logs and trust that they'll be told if anything is wrong.

### Phase 8 — Calibration window
Run live for 3–4 weeks. Review the decision log, look at what the operator rejected and why, look at what the operator edited and how. Tighten the relevance prompt, expand the lexicon, prune low-yield sources, adjust thresholds.

**Done when:** the operator's edit-and-reject rate has dropped meaningfully and the system feels ready for adding the second real channel.

---

## 11. Decisions left to Claude Code

These are implementation choices the brief deliberately doesn't fix. Pick sensible defaults and document them in a short README.

- Programming language and framework. (Recommendation: Python, given the operator's stack and the available libraries for Telegram, RSS, arXiv, and the Anthropic SDK. Open to alternatives if there's a clear reason.)
- Storage choice for the queue and logs. The data is small and single-writer; a simple embedded store is fine.
- Exact model selection for Stage 2 and Stage 3. Use the cheapest competent model for Stage 2, the strongest available model for Stage 3 — and run a one-time A/B between the top two candidates for Stage 3 on the first 20 real items, then lock in.
- File and module layout.
- Whether to use a job scheduler, a cron entry, or a simple loop with sleep.
- Retry and backoff specifics.
- The exact Telegram library and how to structure callback handlers.
- The format of the daily summary message.

---

## 12. Decisions the operator still needs to make

These are blocking or near-blocking, not Claude-Code-decidable.

- Is the Psychology Today channel public from day one, or invite-only during the calibration window?
- Citation format inside posts — author-year only, author-year with DOI, or full citation block?
- Lexicon seed — exactly which Shapira et al. entries to include in v1, plus initial custom additions for AI-related Hebrew terminology that may not be in clinical lexicons (e.g., `parasocial attachment`, `prompt injection`, `alignment`).
- Editorial stance on AI-skeptical content — strict neutral reporting, or allow framing in the methodological-caveat field that nudges readers toward the limits of AI-in-mental-health claims?

These can be answered in the README before Phase 4 begins; they don't block Phases 1–3.
