---
name: tiktok-creator-intelligence
description: Analyze a public TikTok creator from one profile URL, identify and rank explicit ads, affiliate/product-anchor content, and broader commercial candidates, inspect top videos with the current Codex session, assess fit with a supplied product image or description, find shared filming/script patterns, and produce a creator negotiation brief. Use for TikTok 达人分析、广告/带货能力、达人和产品是否匹配、热门广告共同拍法、脚本沟通建议, or English requests about creator commercial intelligence and sponsored content.
---

# TikTok Creator Intelligence

Run the bundled deterministic CLI for collection, classification, metrics, downloads, scene detection, evidence validation, and reports. Use the active Codex session itself for semantic and visual judgment. Do not ask for an OpenAI API key for this workflow.

## Execute the full workflow

1. Extract one public TikTok profile URL. Ask only when no profile URL is present. Product material is optional; accept any combination of product images, description, category, price, selling points, and target audience.
2. Resolve the repository that contains this `SKILL.md`. Use `.venv/Scripts/creator-intel.exe` on Windows or `.venv/bin/creator-intel` on macOS/Linux. If absent, use an installed `creator-intel` executable.
3. Run `prepare-codex PROFILE_URL --limit 100 --top 10 --context-videos 5`. Add each supplied image as `--product-image PATH`, the text as `--product-description TEXT`, and any other supplied product options. Preserve explicit user limits. Commercial candidates must be prepared in descending `view_count` order; never randomly sample or let relative-performance rank displace a higher-view commercial candidate. Context videos must be the highest-view non-commercial videos. This command must not require `OPENAI_API_KEY`.
4. Read the emitted `codex_job.json`, `codex_job.md`, and `codex_review.schema.json` completely.
5. Inspect every product image and every contact sheet listed for each `ready` asset with the local image-viewing tool. If a contact sheet is unclear, inspect the corresponding individual keyframes listed in the job. Read every available `transcript_path`; a WAV file by itself is not a transcript.
6. Write exactly one `CodexVideoReview` for every ready asset to the job's `review_output_path`. Copy `job_id` and `job_digest` exactly from the job. Follow the JSON schema exactly, including explicit empty arrays and nulls.
7. Reassess broad commercial status from frames and public captions for every asset. A positive commercial read needs localizable evidence; use minimum confidence 0.75 for `confirmed`, 0.50 for `likely`, and 0.20 for `possible`. A context video may enter a shared pattern only when the Codex review itself classifies it `confirmed`, `likely`, or `possible` with evidence. A shared pattern requires at least two different reviewed-commercial video IDs and non-empty `evidence_by_video` for each one.
8. Unless the user explicitly requests another language, write all narrative fields in plain Simplified Chinese. Assume the user is already considering collaboration: make the first and most detailed business section explain exactly what video the creator should make, including the concept, opening hook, shot order, required real-product proof, creator freedom, things to avoid, deliverables, compensation structure, and smallest useful performance test. Do not lead with technical coverage, model terminology, or a generic yes/no verdict.
9. Run `finalize-codex JOB_JSON`. Fix any schema, coverage, cross-reference, or evidence-hash error. Read `creator_decision_report.md`, `script_negotiation_brief.md`, `product_fit.json`, and `completion_status.json` before answering.

## Evidence rules

- Keep three meanings separate: `platform_is_ad` is TikTok's explicit three-state marker; `has_product_anchor` means product/Shop evidence; `commercial_status` is the broader explainable system judgment.
- Do not call a product anchor proof of a paid relationship. Explicit platform ad evidence may support `confirmed`; text, product, and visual signals support probabilistic commercial judgments.
- Describe what frames or public captions show before making an inference. Put unreadable overlays, missing speech, sparse frames, and ambiguous product identity in limitations.
- Use raw views and local outperformance together. High raw views alone do not establish creator-relative outperformance.
- Say a creative pattern “co-occurred with” or was “associated with” performance, never that it caused performance.
- If fewer than two commercial candidates were reviewed, return no shared commercial pattern and state the sample limitation.
- If no product was supplied, set product fit to `not_assessed`; still return creator/ad analysis and a brief explaining what product evidence is needed.

## Collection and fallback order

Use these layers in order:

1. yt-dlp profile/video URL collection and download;
2. direct public video-page hydration for explicit ad/product fields and TikTok WebVTT captions;
3. saved/cached evidence from an earlier run;
4. browser automation only for specific failed public items, after the URL-only paths fail.

Never bypass login, CAPTCHA, privacy controls, or platform security. Never invent results for a failed video. A preparation-only run is not a completed Codex analysis.

## Present the result

Use plain merchant-facing language and this fixed priority order:

1. **合作的话，应该让对方发什么视频**: give the recommended concept first, then the hook, shot-by-shot order, mandatory real-product footage/proof, creator freedom, claims to avoid, deliverables, compensation structure, and the smallest useful performance test. Make this the most actionable section.
2. **这个达人的合作画像**: explain what kind of selling account this is, what it repeatedly does well, the strongest ad patterns by raw views, and which parts can transfer to the supplied product.
3. **主要问题和打分**: give the plain-language verdict, 0-100 fit score, confidence, mismatches, risks, and unknowns. Distinguish “适合低成本测试” from “适合高价或长期背书”.

Put technical evidence coverage after those three sections: discovered/classified and ready/reviewed/failed counts; explicit ads, product anchors, and broader commercial candidates as separate counts; rankings by raw views and creator-local performance; collection failures, missing captions, TikTok restrictions, and other uncertainty. Never let the technical appendix displace the filming recommendation at the top.

For follow-ups about the same creator, reuse the saved structured artifacts and cite exact video IDs/URLs. Refresh only when requested or when the requested scope is outside the saved evidence.
