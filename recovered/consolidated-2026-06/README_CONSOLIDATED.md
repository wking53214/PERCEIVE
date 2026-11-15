# OBSERVE + PERCEIVE — Consolidated Clinical Governance System

A pediatric physiological early-warning system (**OBSERVE**) wired to an
AI-governance kernel (**PERCEIVE**), consolidated from a 19-file modular layout
into **7 self-contained files** with no third-party dependencies (Python 3.8+ stdlib only).

```
vitals
  → OBSERVE        7-engine fused risk assessment + per-patient escalation policy
  → escalation?    clinical-safety bypass for hard rules & dangerous syndromes
       → PERCEIVE  6-gate unanimous governance + optional multi-node consensus
  → ClinicalDecision  (links OBSERVE audit hash ⇄ PERCEIVE audit hash)
```

---

## The 7 files

| # | File | Role |
|---|------|------|
| 1 | `observe_consolidated.py` | Clinical engine: 7 risk adapters, calibrated fusion, per-patient policy, immutable audit, async scheduler |
| 2 | `perceive_consolidated.py` | Governance kernel: 6 policy gates, unanimous consensus, DGK multi-node, immutable audit |
| 3 | `clinical_governance_system.py` | **Integration surface** — the single object a hospital wires against |
| 4 | `compliance_exporters.py` | HIPAA / FDA 510(k) / SOX / GDPR exporters |
| 5 | `test_observe_consolidated.py` | OBSERVE unit + scenario tests |
| 6 | `test_perceive_consolidated.py` | PERCEIVE unit + integration tests |
| 7 | `test_integration_consolidated.py` | End-to-end OBSERVE→PERCEIVE tests |

Plus `test_clinical_governance_system.py` and `test_compliance_exporters.py` covering files 3 & 4.

**186 tests, all passing.** Run them with: `python3 -m pytest`

---

## Quick start

```python
from datetime import datetime, timezone
from observe_consolidated import VitalsSnapshot
from clinical_governance_system import build_single_hospital_system

system = build_single_hospital_system()

decision = system.process_vitals(VitalsSnapshot(
    patient_id="P001",
    timestamp=datetime.now(timezone.utc),
    heart_rate=168, oxygen_saturation=83.0,
    respiratory_rate=46, temperature=39.5,
    context={"age_months": 12},
))

print(decision.regime)              # "critical"
print(decision.action)              # "escalate_approved"
print(decision.observe_audit_hash)  # links to clinical assessment
print(decision.perceive_audit_hash) # links to governance decision
```

Multi-hospital deployment (emergency overrides require cross-site quorum):

```python
from clinical_governance_system import build_multi_hospital_system
system = build_multi_hospital_system(["site-a", "partner-a", "partner-b"])
```

---

## OBSERVE — the 7 risk engines

| Engine | What it assesses | Data needed |
|--------|------------------|-------------|
| `heuristic` | Age-adjusted PEWS thresholds (O2, HR, RR, temp) | Vitals only |
| `bayesian` | Age-banded z-score deviation, continuous likelihood | Vitals + age |
| `trajectory` | Per-minute momentum of vitals | Prior readings |
| `drift` | Baseline shift vs rolling history | History window |
| `behavioral` | Named syndromes (septic / respiratory / hypovolemic shock) | Vitals |
| `adversarial` | Sensor-fault detection (streaks, implausible rates) | Recent readings |
| `physiological_reserve` | 6-axis systems physiology (topology/capacity/resource/integrity/phase/instability) | Rich telemetry (gated) |

**The adapter contract (plug-and-play):** every engine returns a `RiskOutput`
with a `risk_score`, then pipes through the shared `regime_distribution()`
calibration. Engines swap; calibration is centralized. This is what lets you
drop in a new industry adapter without touching fusion.

### Key safety properties (each test-locked)

1. **Per-patient state isolation** — escalation cooldowns are keyed by `patient_id`.
   One patient's lock can never suppress another's critical alert.
2. **Clinical-safety bypass** — a `CRITICAL_O2` reading or a confirmed shock
   syndrome escalates *immediately*, skipping dwell/hysteresis confirmation.
3. **Abstention exclusion** — an engine with no data to assess is excluded from
   fusion entirely, so a chorus of "no data" can't dilute a real detection.
4. **Syndrome floor** — a confirmed dangerous pattern floors the fused risk at
   its detected severity; it cannot be averaged below it.
5. **Bounded memory** — per-patient state is LRU-capped (default 10k patients).

---

## PERCEIVE — the 6 governance gates

`boundary_gate` · `citadel` (intent) · `fortress` (content safety) ·
`invariant_validator` · `sentinel` (anomaly) · `micropatch` (emergency override)

- **Unanimous consensus:** every selected gate must approve.
- **Gate selection is deterministic** by request type.
- **DGK multi-node consensus** adds cross-site quorum (default 2/3) for
  `emergency_override` and critical rule changes in multi-hospital deployments.

---

## Audit & compliance

Both OBSERVE and PERCEIVE keep **independent SHA256-chained audit ledgers**.
Any post-hoc tampering breaks the chain and is caught by `verify_integrity()`.

`compliance_exporters.py` renders the ledgers into:
- **HIPAA** — de-identified event log (pseudonymized IDs, hour-coarsened timestamps)
- **FDA 510(k)** — validation report with determinism attestation + engine utilization
- **SOX** — governance decision log (who/what/when/result)
- **GDPR** — Article 30-style data-processing record

---

## Determinism

Every risk engine is a pure function of recorded telemetry + context. Identical
inputs produce identical outputs and the same audit hash. No randomness in the
decision path — a prerequisite for FDA validation and forensic replay.

---

## Notes

- `legacy/` holds the pre-consolidation 19-file modular implementation, archived
  for reference. It is excluded from test collection via `pytest.ini`.
- Clinical thresholds are evidence-informed defaults and require pediatrician
  validation before any clinical deployment.
