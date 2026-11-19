# PERCEIVE

A governance kernel with a verifiable audit ledger, for event-driven state. A pediatric early-warning manifest is the worked example. It is not a medical device, not a certified system and not a compliance instrument. Reconstructed from conversation archives; see [PROVENANCE.md](PROVENANCE.md) for what was recovered and what was corrected.

Status: reference implementation, version 2.0.0 (`pyproject.toml`). Last checked 2026-11-19 at commit `9e71b6e`. This repo's history was rewritten after the 2026-11-15 review, which used commit `6de9d40` (its twin is `ccc8722`). License: Apache-2.0. Standard library only, Python 3.10 or newer.

## Role Within ≡TACK

- **What it is.** The policy-evaluation stage (`Admission → Observe/Keys → Locks → PERCEIVE → Decision → Conservation → Execution → Custody` is the order sentinel_os describes). It judges an `Event` against a `State` and a `Manifest` and records every decision.
- **What it owns.** Gate evaluation (a veto per gate), manifest-declared invariants checked before and after a transition, micropatch handling, a hash-linked ledger, and a distributed quorum model.
- **What it contributes.** Evaluation and evidence generation: a decision plus a ledger entry naming the rule that decided it.
- **What it does not own.** Enforcement. PERCEIVE returns `accepted` or not; nothing here stops a caller that ignores it. It does not own authentication of the caller (`metadata["authority"]` is a caller-supplied label), custody, admission, or execution.
- **Depends on.** Python standard library only. CNS is optional (`src/perceive/cns_connector.py`).
- **Depended on by.** UNKNOWN. No consumer was found or inspected. `observe-perceive` carries its own `perceive_consolidated.py`, which is a different design (see `recovered/` and PROVENANCE).
- **Verified versus declared.** Verified in this repo's tests: gate vetoes, the ledger check, patch re-governance. The CNS connector is optional and tested separately. Everything about the wider pipeline is declared by other repos.
- **Governance and control design.** Policy evaluation, not enforcement. A passing ledger shows the entries are consistent with each other. It does not show who wrote them.

## Gates

The default engine has six gates in `CANONICAL_ORDER`: `boundary`, `invariant_validator`, `fortress`, `citadel`, `sentinel`, `micropatch`. A transition proceeds only if all allow it. Running the clinical engine (`PERCEIVEEngine()`) loads eight: the six plus `vitals_plausible` and `monotonic_time` (EXECUTED 2026-11-15). After a refusal for a subject, an extra `_UnresolvedRefusalGate` is added for that subject until it is resolved (INSPECTED in `engine.py`). `perceive gates` prints only the six.

What the content gates really check: `boundary`, `fortress`, `citadel` and `sentinel` match 16 fixed English words (for example `harm`, `forbidden`, `unsafe`, `drift`) as literal text in the event. They do not analyze meaning, and the `sentinel` "drift" check is the word, not a measurement. A note such as "tissue damage noted", "restricted diet" or "no anomaly" is refused, and a misspelling such as "h4rm" passes (EXECUTED 2026-11-15).

Fail behavior: an exception in a gate or invariant is recorded as a fault and never read as permission. Two cases fail open. An engine or node built with an empty gate list (`gates=[]`) accepts every event, because no gate is left to veto (EXECUTED 2026-11-15). And a refusal is cleared by `resolve_refusal`, which accepts any non-empty name and reason from any caller and records the resolution in a plain list (`engine.resolutions`), not in the hash-linked ledger (EXECUTED 2026-11-15).

```
                        event
                          │
              ┌───────────▼────────────┐
              │  micropatch            │  emergency rules
              │  → re-govern output    │  patched output goes back
              └───────────┬────────────┘  through the gates
                          │
              ┌───────────▼────────────┐
              │  1. boundary           │  capability and scope
              │  2. invariant_validator│  manifest sealed? expired?
              │  3. fortress           │  forbidden content
              │  4. citadel            │  unsafe content, risk routing
              │  5. sentinel           │  drift, monotonic time
              │  6. micropatch         │  emergency rule budget
              └───────────┬────────────┘  unanimous: every gate vetoes
                          │
              ┌───────────▼────────────┐
              │  invariants (pre)      │
              │  apply to a copy       │  original untouched on refusal
              │  invariants (post)     │
              └───────────┬────────────┘
                          │
                 hash-linked audit ledger
```

## Install

```bash
pip install -e ".[dev]"
```

No runtime dependencies; the standard library only.

The optional CNS connector installs from a clone with `pip install -e ".[cns]"`. Without CNS, the connector's error message tells you to run `pip install 'perceive[cns]'`. Do not run that: this package is not published under that name, and an unrelated public package called `perceive` (a browser-automation helper, version 0.4.2 on 2026-11-19, with no CNS connector) would be installed in its place (EXECUTED: the extras install passed the test suite; the public package's own metadata was read).

## Use

```bash
perceive demo --patients 50
```

```
cohort           50 patients (seed 42)
chain verified   True
acceptance rate  100.00%

governance decisions:
  ACCEPTED                   50

clinical outcomes (accepted subjects only):
  normal                     36
  early_warning               8
  deterioration               3
  critical                    3
```

```python
from perceive import PERCEIVEEngine, Event

engine = PERCEIVEEngine()
state, response = engine.submit(
    Event("observation", {"vitals": {...}}, context_id="PT_0001",
          metadata={"authority": "attending"})
)

response.accepted        # bool
response.decision        # ACCEPTED | GATE_REJECTED | INVARIANT_VIOLATED |
                         # FAULT_DETECTED | PATCH_REJECTED
response.reason          # which rule decided, and why
response.audit_hash      # this decision's link in the chain
engine.verify()          # does the whole ledger still verify?
```

## What the ledger is for

Every decision is appended, refusals included. Each entry carries the state
before, the state after, the event, the manifest hash in force, every gate and
invariant outcome, and a hash over all of it linked to the entry before.

```bash
perceive verify --patients 50
```

`verify_chain` recomputes every hash and checks every link, raising
`ChainIntegrityError` naming the first bad entry. Altering a reason, changing
a decision, deleting an entry, or reordering two entries all break it -
there's a test for each.

This matters because the archival kernel built the chain and never checked it.
Its `audit_chain_valid` field was computed as `audit_chain_tip is not None`,
which is true as soon as any entry exists, including in a ledger that has been
rewritten. A hash chain nobody verifies is decoration.

## Unanimous, and why

Every gate is a veto. A transition proceeds only if every gate allows it, which
means **adding a gate can only ever make the system more conservative.** You
can extend the policy without auditing whether your new rule accidentally
unlocked something. `test_adding_a_permissive_gate_cannot_unlock_a_denial`
pins it.

The same asymmetry governs the distributed case:

```python
from perceive import DistributedGovernanceKernel, GovernanceNode

cluster = DistributedGovernanceKernel([node_a, node_b, node_c])
result = cluster.decide(event, state)
```

Allowing needs a quorum of allows. A single node's refusal denies, regardless
of quorum. Quorum exists to tolerate a *faulty* node, not to overrule a
correct one; a quorum that could vote away another node's veto would let a
compromised majority approve what an honest node refused. An unreachable node
registers as a fault vote rather than crashing the cluster.

`cluster.manifest_divergence()` reports nodes running different rulesets. Two
groups means a governance incident, whether or not a decision has failed yet.

## MicroPatch, inside the envelope

The archival pipeline ran the patch engine *after* every governance control,
with nothing between it and the model. The corpus says so plainly: "there is
no subsequent governance-control pass before model execution."

Here, patched output is re-governed. A patch whose output the gates refuse is
discarded and the **original event restored, then judged on its own**. So a
patch cannot smuggle content past the gates, but a tightening patch (for
example a redaction) that fails re-governance leaves the original, unpatched
event to be accepted if the gates allow it. Do not rely on a patch to keep
data out of the ledger. The discard itself leaves no trace in the ledger: the
entry for that event lists no patch at all (EXECUTED 2026-11-15).

```python
engine.patches.register("redact_identifiers", redact, expires_after=100)
```

Patches expire by application count, because an emergency rule that never
retires becomes permanent policy that never went through review.

## Compliance exports

```bash
perceive export sox
perceive export gdpr --subject PT_0001
```

Four regimes: HIPAA, FDA 510(k), SOX, GDPR. Each renders the ledger into the
shape that regime expects, and each envelope carries its own integrity status
and its own caveats.

To be explicit, because the archives are not: **these exports do not establish
compliance.** They render decisions already in the ledger. What the ledger
genuinely supports is narrower and real; every decision recorded with its
inputs, the rule that decided it, the manifest in force, and a hash link to
the decision before. That is a defensible decision record. Whether the rules
themselves satisfy a regulation is a determination for someone with standing
to make it.

The HIPAA export deliberately carries no vitals: an export that reproduces the
protected data it accounts for defeats its own purpose.

## The clinical layer

The kernel is domain-neutral. `clinical.py` supplies one manifest for
pediatric early warning: four vital-sign invariants plus append-only history,
a plausibility gate rejecting jumps above 60% between readings, and a risk
model summing squared z-scores into an energy score across four outcome
classes.

Bounds, reference distributions, thresholds and cohort mix are all recovered
from the archive. Three corrections to the recovered model are documented in
[PROVENANCE.md](PROVENANCE.md); the sharpest is that a *missing* heart rate
used to default to zero, giving a z-score of −7 and making an absent sensor
the loudest alarm in the system.

## Tests

```bash
python -m pytest          # 182 passed, 1 skipped on 2026-11-19 at 9e71b6e (142 at review; two later commits added gate-position tests)
```

Properties worth knowing about: an exception in a gate or an invariant is
recorded as a fault and never read as permission, and a rejected event leaves
state untouched.

Limit, EXECUTED 2026-11-15: the state `submit` returns is the same object the
engine keeps (`engine._states`). A caller who mutates it changes what the
engine governs from next. The ledger still verifies (`engine.verify()` stayed
`True`), because the ledger keeps its own copies, so the ledger is not altered,
but it no longer matches the live state. In my run the `history_append_only`
invariant refused the next event. A change to a field no invariant covers would
not be caught. Treat the returned state as read-only.

## Recovered later lineage

`recovered/consolidated-2026-06/` holds a later, more complete state of this
system, recovered from the archive: seven OBSERVE risk engines, a request-based
PERCEIVE kernel, an orchestrator that wires them together, compliance exporters
and 186 tests. It is kept as a historical artifact and is not part of the
installed package. Run it with
`cd recovered/consolidated-2026-06 && python -m pytest`. Read its
[README](recovered/consolidated-2026-06/README.md) and the "A later lineage"
section of [PROVENANCE.md](PROVENANCE.md) before relying on either design.

## Scope

A governance kernel for event-driven state, with a clinical manifest as a
worked example. It is not a medical device, not a certified system, and not a
compliance instrument. The synthetic cohort is synthetic: it exists to
exercise the kernel, not to validate a clinical model against anything.

## Limits and trust

- The ledger is in memory and hash-linked with plain SHA-256. It detects inconsistency when recomputed. It has no key, so anyone who can rewrite the whole ledger can recompute every hash. That is integrity checking, not authentication or tamper-proof custody.
- `metadata["authority"]` is a label the caller supplies.
- A compliance export renders ledger entries. It does not establish compliance.
- Synthetic cohort only. No clinical validation.

## Documentation

[PROVENANCE.md](PROVENANCE.md), `NOTICE`, `recovered/consolidated-2026-06/README.md` (historical, not installed).

Maintainer: William N. King.
