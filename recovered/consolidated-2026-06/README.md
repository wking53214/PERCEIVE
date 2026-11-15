# Recovered: consolidated OBSERVE + PERCEIVE (June 2026)

This is the final state of a clinical governance system, recovered from a Claude
conversation held between 2026-06-10 and 2026-06-15. It is the later, more
complete lineage of the system this repository reconstructs. It is kept here as
a historical artifact beside the live package. It is not imported by
`src/perceive`, it is not installed, and the root test suite does not collect it.

Read the "A later lineage" section of [`../../PROVENANCE.md`](../../PROVENANCE.md)
first. It says how the files were recovered, what was redacted, and how this
design differs from the live package.

## What is here

| File | Role |
|---|---|
| `observe_consolidated.py` | Seven risk engines, calibrated fusion, per-patient escalation policy, hash-chained audit, async scheduler |
| `perceive_consolidated.py` | Six policy gates selected by request type, consensus, optional multi-node DGK, hash-chained audit |
| `clinical_governance_system.py` | The orchestrator that wires OBSERVE's output into PERCEIVE's gates |
| `compliance_exporters.py` | HIPAA, FDA 510(k), SOX and GDPR renderings of the two audit ledgers |
| `test_*.py` (five files) | 186 tests |
| `pytest.ini` | Scopes collection to those five files |
| `README_CONSOLIDATED.md` | The system's own README, as it was written then |

Everything uses the standard library only.

## Run it

```bash
cd recovered/consolidated-2026-06
python -m pytest        # 186 passed
```

Run from inside this folder. The files import each other as top-level modules,
and `pytest.ini` here takes precedence over the repository's own configuration.

## Things to know before relying on it

- **Not validated.** The clinical thresholds are evidence-informed defaults that
  no pediatrician has checked. This is not a medical device (see `NOTICE`).
- **Passing tests show the code matches its own tests.** They are not an
  independent validation, and `README_CONSOLIDATED.md` states its properties in
  the original author's words.
- **The multi-node consensus is unanimous by construction.** `DGKGateway.reach_consensus`
  builds every node's proposal from the same decision data, using keys that one
  gateway object holds. It shows message signing, clustering and quorum
  arithmetic. It does not show independent evaluation by separate nodes.
- **Different design from the live package.** This kernel evaluates a
  `PolicyRequest`; `src/perceive` judges an `Event` against a `State`. Features
  of one should not be assumed to exist in the other.
- **Five lines were redacted** (a real organisation's name). Nothing else differs
  from the recovered text. The table is in `PROVENANCE.md`.
