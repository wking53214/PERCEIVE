"""Command line interface for PERCEIVE."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Optional, Sequence

from .compliance import export_gdpr, export_hipaa, export_fda_510k, export_sox
from .engine import PERCEIVEEngine, run_synthetic

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_CHAIN_BROKEN = 3
EXIT_USAGE = 64


def _cmd_demo(args) -> int:
    results, engine = run_synthetic(args.patients, seed=args.seed)
    decisions = Counter(r.decision for r in results)
    outcomes = Counter(r.risk.outcome for r in results if r.risk)

    if args.json:
        print(
            json.dumps(
                {
                    "decisions": dict(decisions),
                    "outcomes": dict(outcomes),
                    "metrics": engine.metrics.to_dict(),
                    "chain_verified": engine.verify(),
                    "results": [r.to_dict() for r in results],
                },
                indent=2,
                default=str,
            )
        )
    else:
        print(f"cohort           {args.patients} patients (seed {args.seed})")
        print(f"chain verified   {engine.verify()}")
        print(f"acceptance rate  {engine.metrics.acceptance_rate:.2%}")
        print("\ngovernance decisions:")
        for name, count in decisions.most_common():
            print(f"  {name:<22}{count:>5}")
        print("\nclinical outcomes (accepted subjects only):")
        for name, count in outcomes.most_common():
            print(f"  {name:<22}{count:>5}")
    return EXIT_OK if engine.verify() else EXIT_CHAIN_BROKEN


def _cmd_verify(args) -> int:
    from .kernel import ChainIntegrityError

    results, engine = run_synthetic(args.patients, seed=args.seed)
    try:
        engine.kernel.verify_chain()
    except ChainIntegrityError as exc:
        print(f"CHAIN BROKEN: {exc}", file=sys.stderr)
        return EXIT_CHAIN_BROKEN
    print(f"chain verified: {len(engine.kernel.audit_log)} entries, tip "
          f"{engine.kernel.audit_chain_tip[:16]}")
    return EXIT_OK


def _cmd_export(args) -> int:
    results, engine = run_synthetic(args.patients, seed=args.seed)
    if args.regime == "gdpr":
        if not args.subject:
            print("error: gdpr export requires --subject", file=sys.stderr)
            return EXIT_USAGE
        envelope = export_gdpr(engine.kernel, engine.manifest, args.subject)
    else:
        exporter = {"hipaa": export_hipaa, "fda": export_fda_510k, "sox": export_sox}[
            args.regime
        ]
        envelope = exporter(engine.kernel, engine.manifest)
    print(envelope.to_json())
    return EXIT_OK if envelope.chain_verified else EXIT_CHAIN_BROKEN


def _cmd_gates(args) -> int:
    from .gates import CANONICAL_ORDER, default_gates

    print("Six governance gates, unanimous consensus, evaluated in order:\n")
    for i, gate in enumerate(default_gates(), 1):
        doc = (gate.__doc__ or "").strip().split("\n")[0]
        print(f"  {i}. {gate.name:<22}{doc}")
    print("\nEvery gate is a veto: a transition proceeds only if all six allow it.")
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="perceive",
        description="PERCEIVE - governance kernel with a verifiable audit ledger.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_demo = sub.add_parser("demo", help="govern a synthetic cohort")
    p_demo.add_argument("--patients", type=int, default=50)
    p_demo.add_argument("--seed", type=int, default=42)
    p_demo.add_argument("--json", action="store_true")
    p_demo.set_defaults(fn=_cmd_demo)

    p_verify = sub.add_parser("verify", help="verify the audit chain end to end")
    p_verify.add_argument("--patients", type=int, default=50)
    p_verify.add_argument("--seed", type=int, default=42)
    p_verify.set_defaults(fn=_cmd_verify)

    p_export = sub.add_parser("export", help="render the ledger for a regime")
    p_export.add_argument("regime", choices=["hipaa", "fda", "sox", "gdpr"])
    p_export.add_argument("--subject", help="required for gdpr")
    p_export.add_argument("--patients", type=int, default=20)
    p_export.add_argument("--seed", type=int, default=42)
    p_export.set_defaults(fn=_cmd_export)

    p_gates = sub.add_parser("gates", help="describe the six gates")
    p_gates.set_defaults(fn=_cmd_gates)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
