# Structured counting contract

Use this contract for new analyses and user-requested corrections. Existing schema-v2 reports are historical inputs, not proof that these checks passed. Program checks establish file identity, coverage and arithmetic; visual review still establishes exercise semantics. Do not describe passing tests as verified movement recognition.

## Extraction and review

Event intervals must cover the working interval without unexplained gaps. Record a pause, setup or uncertain interval explicitly instead of jumping to the next recognized cycle. For left/right movements use `counting_mode: per_side` and `side: left|right` on every event; phase ordering is checked independently per side. The validator derives `reps_per_side`, and the undoubled `completed` convention is the smaller confirmed side count. Report the separate counts even when unequal. Default `bilateral` records one shared cycle; do not attach unchecked per-side counts to that mode.

Optional `context.excluded_context` is a list of `{frames: ["receipt/frame-id", ...], observation: "...", reason: "..."}` for preparation/reset outside the work interval. Those references must be on reviewed pages and outside every working segment. An excluded movement inside the work interval belongs in `events` instead. Page-pixel cost measures retained inputs; image tools may resize them, so it is not a token count or the final model image resolution.

1. Use the unchanged bounded `workout_video_decode.py` entrypoint to prepare proxies and source-local 8–12 fps windows. Decode original media only through that entrypoint with its existing permission prefix. Reuse matching cache markers. Pad a working set with preparation and reset; if a source ends mid-set, explicitly record incomplete coverage.
2. Use `extract_review_frames.py WINDOW OUTPUT_DIR --file SOURCE_LABEL --start ORIGINAL_SECONDS --end ORIGINAL_SECONDS --step 0.5`. This helper reads only completed, bounded source-local windows, checks their fingerprint/cadence/frame count, emits individually hashed frames, automatically paginates into at most 48 frames per sheet, and writes `receipt.json`. `--end` is the last requested sample, inclusive, not an FFmpeg duration. Output directories are immutable; choose a new one on a new extraction. Pillow is required: use the available bundled image Python runtime when the default Python lacks it. Do not install packages merely for this task.
3. For selected phase frames, use `--times times.json` (array of original-file seconds). Range endpoints are always added. The receipt records actual decoded frame PTS and corresponding source-window sample times, not invented millisecond accuracy. A receipt is complete only if every requested frame and pagination slot exists. Frame IDs, such as `f000320`, are window frame indices.
4. View every page you claim to review. Register inspected pages immediately: `analysis_manifest.py MANIFEST --reviewed-pages RECEIPT_PATH#p001,p002`. Every retained phase reference must be on a viewed page. For coarse discovery sheets use `--discovery-page IMAGE_PATH=FRAME_SLOTS`; this accounts for pixels and frame slots but provides no counting authority. Never register bare sheet names as proof.
5. Separate boundary selection from repetition counting. Motion/similarity methods output navigation candidates only. Verify persistent supports, load path and an excluding cue, then review complete boundary–opposite–boundary cycles inside the actual work interval. Inspect first two, middle, last two and irregular cycles. Disagreement after an ROI/parameter change triggers one targeted dense review, not selection of whichever numeric output seems plausible. Never force different sets to equal counts.

## Event evidence JSON

New analyses use evidence `schema_version: 3`. Versions 1 and 2 remain readable for archived evidence. Version 2 added a required context-first classification audit so phase triplets cannot be used by themselves to invent a working-set label.

All paths in evidence are relative to that evidence JSON unless absolute. Frame references are `receipt_alias/frame_id`. The example is a shape, not a ready-to-register assertion:

```json
{
  "schema_version": 3,
  "set_id": "row-1",
  "exercise_id": "row.backpack",
  "set": 1,
  "method": "visual_cycle_sequence",
  "receipts": {"w": "frames/receipt.json"},
  "segments": [{"file": "0045", "start_s": 132.5, "end_s": 136.5}],
  "coverage_complete": true,
  "confidence": "high",
  "classification_audit": {
    "full_context_reviewed": true,
    "intent": "working_set",
    "closest_lookalike": "equipment adjustment",
    "discriminator": "the same bag path repeats while both hands keep the same grip",
    "stable_configuration": true,
    "consistent_cycle_path": true,
    "setup_or_adjustment_observed": false,
    "novel_or_one_off": false
  },
  "review": {
    "reviewer": "model identity if known, otherwise assistant",
    "count_context": "source_review",
    "expected_totals_exposed": true,
    "boundary_pass": {"locked_before_counting": true, "segments": [{"file": "0045", "start_s": 132.5, "end_s": 136.5}], "observation": "Observed work interval; setup and release excluded"},
    "reverse_events": [{"event_id": "rep-1", "frames": ["w/f000625", "w/f000640", "w/f000665"], "observation": "Independently reviewed return, opposite phase and start, then matched source timestamps"}],
    "cycle_definition": "bag low - pull - bag low with stable foot support",
    "positive_cues": ["feet remain planted", "elbows flex while bag rises toward trunk"],
    "excluded_lookalike": "grip adjustment does not repeat the complete elbow/load path",
    "viewed_pages": {"w": ["p001"]}
  },
  "context": {"before": "w/f000610", "after": "w/f000680", "after_observation": "release and leave hinge posture"},
  "events": [{
    "id": "rep-1", "status": "complete",
    "frames": ["w/f000625", "w/f000640", "w/f000665"],
    "phase_labels": ["boundary", "opposite", "boundary"],
    "observation": "elbows pull the bag and extend back to the same low position"
  }]
}
```

- `segments` is the ordered actual work interval, translated through the decoder's authoritative segment map. Cross-file sets use multiple receipts and ordered segments; frame references may span files. Compare the support configuration on each side of the boundary.
- Review wide chronological context before choosing cycle frames. Set `classification_audit.intent` to `working_set`, `setup_adjustment`, `transition`, or `ambiguous`. A v2 working set requires stable configuration, a consistent cycle path, no setup or adjustment within the declared interval, and at least two complete cycles. For a novel or one-off exercise, set `novel_or_one_off: true` and add `second_pass` with `wide_context_reviewed`, `agrees_with_exercise_id`, and `setup_adjustment_ruled_out`, all true. If clothing, straps, gloves, weights, or body areas are being adjusted, split out a later clean interval or classify the candidate as non-set.
- Events have unique IDs, three distinct increasing phase references, and non-overlapping time ranges. The previous return may be the next start. First and last events must reach the declared working boundaries. All event frames must fall in the working interval. Preparation/reset outside it remain context, not repetitions.
- Status is `complete`, `partial`, `uncertain`, or `excluded`. All require a visual observation; non-complete events additionally require `reason`. Excluded equipment adjustments count zero. A whole non-set candidate has `disposition: "non_set"`, only excluded events, and matching context evidence; it is not silently omitted.
- `complete` requires phase labels `boundary, opposite, boundary`. `completed`, `partial` and `uncertain` totals are derived from the events. Optional `claimed_count` is an assertion and must match the derived complete count.
- `coverage_complete: false` requires `unresolved_reason`, pre-context and medium/low confidence; post-reset evidence may be absent. Confirmed cycles can be retained, but their sum is only a lower bound, not an exact session total. Use a pending-cycle count only when the candidate ambiguity is bounded; do not automatically invent ±1.
- Retain receipts, frames, pages, evidence JSON, source fingerprints and segment maps together. Window videos may return to the bounded cache; validated frame evidence remains independently readable. Do not rewrite hashed frames/receipts after review.

## Finalization and budgets

```text
analysis_manifest.py MANIFEST --candidate-set row-1
analysis_manifest.py MANIFEST --evidence row-1=EVIDENCE_JSON --assert-final-ready --summary
```

The manifest is schema 4. It validates structured evidence, derives counts automatically, and rechecks registered hashes on subsequent calls. `--final-count row-1=12` is only an optional equality assertion. Old `--source-verified-set` and textual `--count-evidence` declarations no longer grant authority. Keep old manifests as history; start a new run for a correction.

Budget defaults are 3,000 actual displayed frame slots and 300 million page pixels per run (not measured model tokens). The tool deduplicates the same retained page asset across sets. Record all viewed discovery and verification pages; keep generated and actually viewed artifacts distinct. Reserve roughly one quarter of the chosen budget for boundaries and disputed events before coarse discovery. Contact sheets have enforced readable geometry; do not squeeze multiple sets into oversized sheets. Stop additional viewing at the chosen budget and retain explicit unresolved status. Do not silently start another manifest to reset a budget; a new review run requires a new user request or approved scope.

## Report handoff

New reports use schema 5 with v3 event evidence, `evidence_manifest` relative to the report JSON, and one unique `evidence_set_id` on each timeline row. Every working-set evidence entry must appear exactly once. Report source path/fingerprint, segments, counts, partial/uncertain counts, exercise/set identities and confidence must match. Every issue needs a `problem_gate`; see `report-schema.md`. Keep ordinary summary/timeline arithmetic checks too. The renderer reopens all evidence; a copied summary or removed manifest does not bypass the gate. Schemas 3 and 4 remain readable for existing reports.

Historical rendering is explicit: `render_html_report.py OLD.json OUT.html --allow-legacy`. This only accepts missing-manifest schema ≤2 and adds a visible history/unverified notice. New reports always use the strict default. The report does not certify exercise semantics solely because hashes and arithmetic pass.

## v3 boundary, exposure and reverse contract

For each working set, require `review.count_context: source_review|blind_source_review` and boolean `expected_totals_exposed`. Blind mode requires false plus a concrete `isolation_basis`; the validator checks the declaration, not the model's mental independence. Same-chat prescription followed by video normally uses source_review.

`review.boundary_pass` must record `locked_before_counting: true`, an exact copy of the working `segments`, and an observation. In `reverse_events`, write one independently inspected triplet per complete event, in end-to-start order per side; triplet frames themselves remain chronological. After review, match each to `event_id`; include an observation and `side` for per-side counts. Each corresponding phase must be within 0.5 seconds of the forward phase. Missing, duplicate, mismatched or wrongly ordered cycles fail validation. Do not generate this list by reversing forward JSON. Partial/uncertain/excluded events remain explicit in the forward coverage; a reverse match cannot resolve unseen coverage.

Schema-5 reports require v3 working-set evidence. Historical reports retain their existing versions and hashes; never rewrite old evidence to claim a review that did not happen. Structural success proves evidence binding and arithmetic, not perception or independent cognition.
