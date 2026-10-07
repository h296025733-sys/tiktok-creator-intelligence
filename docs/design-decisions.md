# Design decisions

## Current Codex is the default AI layer

The preferred Skill uses two phases:

1. deterministic preparation (`prepare-codex`) collects metadata, ranks,
   downloads selected public video URLs, extracts scenes/keyframes/contact
   sheets, and retrieves public captions;
2. the active Codex task inspects those local images/transcripts, writes strict
   JSON, and `finalize-codex` validates and renders the decision report.

This reuses the user's current Codex task and needs neither `OPENAI_API_KEY` nor
a TikTok API credential. The older OpenAI API path remains an explicit optional
CLI path for users who configure a key.

## A preparation is immutable

Every preparation uses a run directory and carries `job_id` plus a SHA-256
`job_digest` over the creator, product, selected videos, metrics, evidence paths,
and evidence hashes. A Review must copy both fields exactly. Finalization also
requires declared media/keyframe/contact-sheet/audio/transcript/product paths to
match their hash manifest and stay inside the run directory. This prevents an
older product review or replaced evidence from silently completing a newer job.

## Prepared commercial videos are the highest-view candidates

The deep-review set is not random and is not a blended-rank sample. After
commercial classification, preparation sorts all eligible commercial candidates
by `view_count` descending and takes the user-supplied `--top` hard cap. Videos
with missing view counts come after known counts. Raw and creator-relative
leaderboards are still reported separately, but relative performance does not
displace a higher-view commercial candidate from the prepared Top set. Context
videos are the highest-view non-commercial posts; `--context-videos 0` prepares
no context item.

## Cache reuse does not weaken run evidence

Public media is cached by creator and video ID. A later preparation can reuse
the cached bytes, but it copies them into the new run directory and hashes that
copy along with the generated keyframes and contact sheets. This avoids a repeat
download while keeping each product/job review bound to immutable run evidence.

## No-signal is unknown, not proven organic

Missing configured keywords or an explicit platform false value does not prove
that a video had no commercial relationship. The deterministic classifier uses
`unknown` when it has no positive evidence. The current Codex review can call a
video `organic` only when the reviewed evidence supports that broader judgment.

## Public captions precede paid transcription

TikTok-exposed public WebVTT tracks are downloaded and timestamp-parsed first.
If none exists, the no-key path stays visual/caption-only and records the
limitation; it does not claim that an extracted WAV was transcribed. Optional
API transcription remains in the legacy keyed pipeline.
