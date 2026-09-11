# PERCEIVE

A governance kernel with a verifiable audit ledger.

Six gates under unanimous consensus, manifest-declared invariants checked
before *and* after every transition, and a hash-linked ledger that records
every decision — accepted or refused — with the rule that decided it.

Reconstructed from conversation archives. See [PROVENANCE.md](PROVENANCE.md)
for what was recovered, what was corrected, and why.

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
a decision, deleting an entry, or reordering two entries all break it —
there's a test for each.

This matters because the archival kernel built the chain and never checked it.
Its `audit_chain_valid` field was computed as `audit_chain_tip is not None`,
which is true as soon as any entry exists, including in a ledger that has been
rewritten. A hash chain nobody verifies is decoration.

## Unanimous, and why

Every gate is a veto. A transition proceeds only if all six allow it, which
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
correct one — a quorum that could vote away another node's veto would let a
compromised majority approve what an honest node refused. An unreachable node
registers as a fault vote rather than crashing the cluster.

`cluster.manifest_divergence()` reports nodes running different rulesets. Two
groups means a governance incident, whether or not a decision has failed yet.

## MicroPatch, inside the envelope

The archival pipeline ran the patch engine *after* every governance control,
with nothing between it and the model. The corpus says so plainly: "there is
no subsequent governance-control pass before model execution."

Here, patched output is re-governed. A patch whose output the gates refuse is
discarded and the original event restored. A patch may tighten; it can never
smuggle content through.

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
genuinely supports is narrower and real — every decision recorded with its
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
python -m pytest          # 131 tests
```

Properties worth knowing about: an exception in a gate or an invariant is
recorded as a fault and never read as permission; a rejected event leaves
state untouched; and a caller mutating the state it got back cannot alter the
ledger.

## Scope

A governance kernel for event-driven state, with a clinical manifest as a
worked example. It is not a medical device, not a certified system, and not a
compliance instrument. The synthetic cohort is synthetic: it exists to
exercise the kernel, not to validate a clinical model against anything.
