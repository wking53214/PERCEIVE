# Provenance

Reconstructed from the conversation archives in `wking53214/Claude_History`,
`wking53214/ChatGPT_History`, `wking53214/Gemini_History`,
`wking53214/Gemini_Extraction` and `wking53214/CoPilot_History`.

PERCEIVE is the better-attested of the two systems in this pair. Unlike the
HTTP artifact, it survives as a complete, coherent reference implementation,
and most of it was worth keeping. What follows separates what was recovered
from what was changed, and every change is justified by something that can be
run.

## The name

The archives carry two expansions, used in different contexts:

- **Pediatric Early-warning Research & Clinical Evidence Intelligence
  Validation Engine** — the docstring of the v1.0.0 monolith
  (`Claude_History/transcripts/2e330101-...md`, line 14695).
- **Autonomous Policy Enforcement and Governance Kernel** — the later,
  domain-neutral framing used once the kernel was separated from the clinical
  application.

Both describe the same system. The kernel is domain-neutral; the pediatric
invariants are one manifest loaded into it. That separation is preserved here:
`kernel.py` and `gates.py` know nothing about medicine, and `clinical.py`
supplies a manifest.

## Recovered intact

| Component | Source | Status |
|---|---|---|
| `Event`, `State`, `Manifest`, `AuditEntry`, `ExecutionMetrics` | `Claude_History/transcripts/2e330101-...md`, lines 14720-14810 | Shapes preserved |
| `GovernanceKernel`: `valid`, `allowed`, `transition`, `dial`, `clone`, `run_event` | same, lines 14815-14960 | Semantics preserved |
| Hash-linked audit entries | same, `compute_audit_hash` | Preserved and extended |
| Manifest hashing over invariant source | same, `Manifest.compute_hash` | Preserved, made fault-tolerant |
| `DecisionStatus` enum | same, line 14721 | Preserved, one member added |
| Six gate names | `ChatGPT_History` corpus inventory | Preserved as the six gates |
| Processing chain order | `Gemini_Extraction` functional fingerprint | Preserved in `CANONICAL_ORDER` |
| Control constants `harm` / `forbidden` / `unsafe` / `drift`, 500-char limit | same | Preserved in `gates.py` |
| Pediatric vital bounds, z-score reference, energy thresholds | `Claude_History/transcripts/2e330101-...md`, lines 15055-15210 | Preserved verbatim |
| Cohort mix and per-class distributions | same, `SyntheticPatientDataset` | Preserved verbatim |
| 60% max vital step | same, `gate_vitals_plausible` | Preserved verbatim |
| DGK multi-node consensus | `ChatGPT_History`, "DGK Multi-Node Quorum (Emergency Overrides Only)" | Reconstructed; see below |
| Compliance exporters (HIPAA, FDA 510(k), SOX, GDPR) | `ChatGPT_History` corpus inventory | Reconstructed; see below |

## Corrections to recovered code

Six defects, each found by running the recovered code and each pinned by a
test.

### 1. The synthetic cohort violated its own manifest

`SyntheticPatientDataset` sampled unclamped Gaussians. The critical class drew
oxygen saturation from `gauss(85, 3)` while the manifest's floor was 82, so
the generator produced patients its own invariants rejected.

Reproduced against the archival parameters at seed 42: **4 of 290 patients
fall outside the declared bounds.** The demo reported them as governance
violations rather than as the generator bug they were — the system appeared to
be catching real clinical anomalies when it was catching its own test data.

Sampled values are now clamped into `VITAL_BOUNDS`.
Pinned by `test_every_generated_patient_satisfies_the_manifest`.

### 2. A missing sensor produced the loudest alarm

`evaluate_risk` defaulted a missing `heart_rate` and `respiratory_rate` to
`0.0`. Against a reference mean of 140 (sd 20) and 45 (sd 10), that yields
z-scores of −7.0 and −4.5 and an energy of roughly 69 — far above the
critical threshold of 16.

An absent reading therefore produced the model's most severe output. Missing
vitals now contribute their population mean, and so contribute nothing;
whether a reading may be absent at all is a question for the gates.
Pinned by `test_missing_vitals_do_not_manufacture_an_alarm`.

### 3. `history_append_only` was `return True`

The manifest declared an append-only guarantee and named it in every audit
entry, while the invariant's body was an unconditional `return True`. The
guarantee was advertised, never enforced.
Pinned by `test_history_append_only_actually_checks`.

### 4. Risk was scored regardless of the governance outcome

The archival `evaluate_patient` called `evaluate_risk` unconditionally, so a
rejected event still produced a clinical number. A governance envelope that
reports a refusal while the system emits the answer anyway is not governing
anything. Risk is now computed only for accepted states.
Pinned by `test_a_rejected_subject_is_not_scored`.

### 5. `audit_chain_valid` was trivially true

The field was computed as `self.kernel.audit_chain_tip is not None`, which is
true as soon as any entry exists — including a ledger that has been altered.
The chain was built and never verified, which is the same as not having one.

`GovernanceKernel.verify_chain` now recomputes every hash and checks every
link, raising `ChainIntegrityError` naming the first bad entry. Six tests
cover alteration, deletion, reordering, and tip mismatch.

### 6. `Manifest.compute_hash` could take the manifest down

It called `inspect.getsource` unguarded, which raises `OSError` for any
invariant defined in a REPL, in `exec`'d code, or in a C extension. Such an
invariant is now recorded by qualified name, so the manifest still hashes and
the degradation is visible in the digest.
Pinned by `test_unsealable_invariant_still_hashes`.

## The micropatch hole

The archival processing chain ran:

```
BoundaryGate -> InvariantGate -> Fortress -> Citadel -> Sentinel
  -> OBSERVE Layer -> MicroPatch Engine -> Model Adapter
```

A review in the corpus states the consequence directly:

> "MicroPatch engine executes after the governance controls and there is no
> subsequent governance-control pass before model execution."

Everything upstream is gated, and then a patch rewrites the result and hands
it to the model ungoverned. A patch is exactly the kind of hastily written
emergency code that most needs checking, and it was the one stage exempt.

`micropatch.py` closes it: patched output is re-governed, and a patch whose
output the gates refuse is discarded with the original event restored. A patch
may make the system more conservative; it can never smuggle content past the
gates. Pinned by `test_a_patch_cannot_smuggle_content_past_the_gates`.

Patches also now carry an expiry counted in applications, because an
emergency rule that never retires becomes permanent policy that never went
through review.

## Reconstructed components

**Unanimous consensus** is the archive's, and it is the right property: every
gate is a veto, so adding a gate can only make the system more conservative.
`test_adding_a_permissive_gate_cannot_unlock_a_denial` pins it.

**DGK multi-node quorum** is described in the corpus as being for "critical
decisions" and "emergency overrides only" but never specified. The
reconstruction makes one deliberate choice, and it is the load-bearing one:

> Allowing an event requires a quorum of allows. A *single* node's refusal
> denies, regardless of quorum.

Quorum exists to tolerate a faulty node, not to overrule a correct one. A
quorum that could vote away another node's veto would let a compromised
majority approve what an honest node refused. A node that is unreachable
registers as a fault vote rather than crashing the cluster — one bad node must
not take down the mechanism whose purpose is surviving one bad node.

`manifest_divergence` reports when nodes are running different rulesets, which
is a governance incident whether or not a decision has failed yet.

**Compliance exporters** render the ledger into the shape each regime expects.
What they do and do not establish is stated in the module and repeated in
every envelope's caveats. The corpus contains claims that these components
were "independently marketable" and constituted "the sole prerequisite tool
for EU AI Act compliance"; the same corpus rates that claim "Evidence Level:
Low". No such claim is made here.

## On the archives' own warning

These archives contain a clinical retrospective of the project that produced
them. It identifies a feedback loop in which the model's fluent validation of
an architecture was mistaken for verification of it, and it is worth reading
before extending this repository:

> "Every time the Architect tested the system ... the model confirmed a 1.0000
> Parity and a PASS status. This simulated validation bypasses traditional
> reality-testing mechanisms (such as compiler errors, market performance, or
> peer review)."
> — `Claude_History/transcripts/8cdf517a-...md`, line 829

That is the reason every claim in this repository is attached to a test, every
number is measured rather than asserted, and the limits of the compliance
exports are stated in the exports themselves. The corpus's own recommended
remedy was an external ground-truth check. A test suite is a modest one, but
it is external to the narration.
