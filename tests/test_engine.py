"""End-to-end engine behaviour, compliance exports, and the CLI."""

import json

import pytest

from perceive.cli import EXIT_OK, EXIT_USAGE, main
from perceive.compliance import export_fda_510k, export_gdpr, export_hipaa, export_sox
from perceive.engine import PERCEIVEEngine, run_synthetic
from perceive.micropatch import MicroPatchEngine
from perceive.models import DecisionStatus, Event

PATIENT = {
    "patient_id": "PT_0001",
    "age_months": 12,
    "vitals": {
        "heart_rate": 130.0,
        "respiratory_rate": 40.0,
        "oxygen_saturation": 97.0,
        "temperature": 37.0,
    },
    "admission_timestamp": "2026-01-01T00:00:00",
}


class TestEngine:
    def test_healthy_patient_is_accepted_and_scored(self):
        response = PERCEIVEEngine().evaluate_patient(PATIENT)
        assert response.accepted
        assert response.risk is not None
        assert response.risk.outcome == "normal"
        assert response.chain_verified

    def test_every_accepted_decision_carries_its_proof(self):
        response = PERCEIVEEngine().evaluate_patient(PATIENT)
        assert response.audit_hash
        assert response.state_hash
        assert response.manifest_hash

    def test_a_rejected_subject_is_not_scored(self):
        """The archival engine scored risk regardless of the governance outcome.

        A refusal that still emits a clinical number is not a refusal.
        """
        engine = PERCEIVEEngine()
        hostile = dict(PATIENT)
        hostile["patient_id"] = "PT_BAD"
        event = Event(
            "admission",
            {"note": "this record is classified", "vitals": PATIENT["vitals"]},
            "PT_BAD",
            metadata={"authority": "attending"},
        )
        _, response = engine.submit(event)
        assert not response.accepted
        assert response.risk is None

    def test_empty_vitals_are_not_scored_normal(self):
        """PER-1: a patient with no vitals must not come back normal."""
        empty = {**PATIENT, "patient_id": "PT_EMPTY", "vitals": {}}
        response = PERCEIVEEngine().evaluate_patient(empty)
        assert response.risk is not None
        assert response.risk.outcome == "unassessable"
        body = response.to_dict()
        assert body["outcome"] == "unassessable"
        json.dumps(body, allow_nan=False)

    def test_a_refused_admission_is_not_cleared_by_the_next_observation(self):
        """PER-3: the observation after a refused admission must not pass.

        The admission is refused at the perimeter, so the subject has no
        governed state; the observation used to be judged against that empty
        state and accepted, and the patient came back accepted and scored.
        """
        engine = PERCEIVEEngine()
        patient = {**PATIENT, "patient_id": "PT_REF", "age_months": "classified"}
        _, (admission, observation) = engine.ingest_patient(patient)
        assert not admission.accepted
        assert not observation.accepted
        assert "unresolved refusal" in observation.reason

        final = PERCEIVEEngine().evaluate_patient(patient)
        assert not final.accepted
        assert final.risk is None
        assert final.decision == DecisionStatus.GATE_REJECTED.value
        assert "classified" in final.reason

    def test_a_refusal_persists_across_direct_submissions(self):
        engine = PERCEIVEEngine()
        bad = Event("admission", {"note": "classified"}, "PT_X", metadata={"authority": "attending"})
        good = Event("observation", {"vitals": PATIENT["vitals"]}, "PT_X",
                     metadata={"authority": "attending"})
        assert not engine.submit(bad)[1].accepted
        for _ in range(2):
            _, response = engine.submit(good)
            assert not response.accepted
        assert engine.state_of("PT_X") is None
        assert engine.verify()
        assert engine.unresolved_refusal("PT_X")

    def test_a_refusal_clears_only_by_explicit_resolution(self):
        engine = PERCEIVEEngine()
        bad = Event("admission", {"note": "classified"}, "PT_Y", metadata={"authority": "attending"})
        good = Event("observation", {"vitals": PATIENT["vitals"]}, "PT_Y",
                     metadata={"authority": "attending"})
        engine.submit(bad)
        with pytest.raises(ValueError):
            engine.resolve_refusal("PT_Y", resolved_by="", reason="reviewed")
        with pytest.raises(ValueError):
            engine.resolve_refusal("PT_Y", resolved_by="dr_a", reason="  ")
        assert engine.unresolved_refusal("PT_Y")
        assert engine.resolve_refusal("PT_Y", resolved_by="dr_a", reason="marker was a typo")
        assert engine.unresolved_refusal("PT_Y") is None
        assert engine.submit(good)[1].accepted
        assert engine.resolve_refusal("PT_Y", resolved_by="dr_a", reason="nothing pending") is False

    def test_a_refusal_for_one_subject_does_not_block_another(self):
        engine = PERCEIVEEngine()
        engine.submit(Event("admission", {"note": "classified"}, "PT_A",
                            metadata={"authority": "attending"}))
        assert engine.evaluate_patient(PATIENT).accepted

    def test_state_accumulates_across_submissions(self):
        engine = PERCEIVEEngine()
        engine.evaluate_patient(PATIENT)
        state = engine.state_of("PT_0001")
        assert state is not None
        assert len(state.history) == 2

    def test_cohort_runs_clean_and_the_chain_verifies(self):
        results, engine = run_synthetic(50, seed=42)
        assert len(results) == 50
        assert engine.verify()
        assert engine.metrics.total_events == 100

    def test_out_of_range_vitals_are_refused_by_governance(self):
        engine = PERCEIVEEngine()
        bad = dict(PATIENT)
        bad["patient_id"] = "PT_OOR"
        bad["vitals"] = {**PATIENT["vitals"], "oxygen_saturation": 40.0}
        response = engine.evaluate_patient(bad)
        assert not response.accepted
        assert response.decision == DecisionStatus.INVARIANT_VIOLATED.value

    def test_patches_are_named_in_the_response(self):
        def tag(event, state):
            event.metadata["patched"] = True
            return event

        engine = PERCEIVEEngine(patches=MicroPatchEngine().register("tag", tag))
        response = engine.evaluate_patient(PATIENT)
        assert response.patches_applied == ["tag"]


class TestComplianceExports:
    @pytest.fixture
    def engine(self):
        _, engine = run_synthetic(10, seed=42)
        return engine

    @pytest.mark.parametrize("exporter", [export_hipaa, export_fda_510k, export_sox])
    def test_exports_carry_verified_integrity(self, engine, exporter):
        envelope = exporter(engine.kernel, engine.manifest)
        assert envelope.chain_verified
        assert envelope.chain_error is None
        assert envelope.record_count == len(engine.kernel.audit_log)

    @pytest.mark.parametrize("exporter", [export_hipaa, export_fda_510k, export_sox])
    def test_every_export_states_its_limits(self, engine, exporter):
        envelope = exporter(engine.kernel, engine.manifest)
        assert envelope.caveats
        joined = " ".join(envelope.caveats).lower()
        assert "does not establish compliance" in joined

    def test_a_broken_chain_is_reported_in_the_export(self, engine):
        engine.kernel.audit_log[2].reason = "tampered"
        envelope = export_sox(engine.kernel, engine.manifest)
        assert not envelope.chain_verified
        assert envelope.chain_error

    def test_hipaa_export_excludes_protected_data(self, engine):
        envelope = export_hipaa(engine.kernel, engine.manifest)
        blob = envelope.to_json()
        assert "heart_rate" not in blob
        assert all(r["phi_included"] is False for r in envelope.records)

    def test_gdpr_export_is_scoped_to_one_subject(self, engine):
        envelope = export_gdpr(engine.kernel, engine.manifest, "PT_0001")
        assert envelope.record_count > 0
        assert all(r["subject"] == "PT_0001" for r in envelope.records)

    def test_gdpr_export_for_unknown_subject_is_empty(self, engine):
        assert export_gdpr(engine.kernel, engine.manifest, "NOBODY").record_count == 0

    def test_exports_are_valid_json(self, engine):
        for exporter in (export_hipaa, export_fda_510k, export_sox):
            json.loads(exporter(engine.kernel, engine.manifest).to_json())


class TestCLI:
    def test_demo_runs(self, capsys):
        assert main(["demo", "--patients", "10"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "chain verified   True" in out

    def test_demo_json_parses(self, capsys):
        main(["demo", "--patients", "5", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["chain_verified"] is True
        assert len(payload["results"]) == 5

    def test_verify_reports_the_tip(self, capsys):
        assert main(["verify", "--patients", "5"]) == EXIT_OK
        assert "chain verified" in capsys.readouterr().out

    def test_gates_lists_all_six(self, capsys):
        assert main(["gates"]) == EXIT_OK
        out = capsys.readouterr().out
        for name in ("boundary", "fortress", "citadel", "sentinel", "micropatch"):
            assert name in out

    @pytest.mark.parametrize("regime", ["hipaa", "fda", "sox"])
    def test_export_emits_valid_json(self, capsys, regime):
        assert main(["export", regime, "--patients", "5"]) == EXIT_OK
        json.loads(capsys.readouterr().out)

    def test_gdpr_export_requires_a_subject(self):
        assert main(["export", "gdpr", "--patients", "5"]) == EXIT_USAGE
