"""MicroPatch: re-governance, expiry, and fault containment.

The archival pipeline ran the patch engine after all governance controls with
no subsequent pass. These tests pin the closed hole.
"""

import pytest

from perceive.gates import default_gates
from perceive.micropatch import MicroPatchEngine
from perceive.models import Event, Manifest, State


@pytest.fixture
def manifest():
    return Manifest("M", "v1", [], []).seal()


@pytest.fixture
def gates():
    return default_gates()


def ev(**delta):
    return Event("observation", delta or {"n": 1}, "p1")


class TestApplication:
    def test_no_patches_leaves_the_event_alone(self, manifest, gates):
        event = ev()
        result = MicroPatchEngine().apply(event, State(), manifest, gates)
        assert result.event is event
        assert not result.changed
        assert not result.regoverned

    def test_a_patch_that_declines_changes_nothing(self, manifest, gates):
        engine = MicroPatchEngine().register("noop", lambda e, s: None)
        result = engine.apply(ev(), State(), manifest, gates)
        assert not result.changed

    def test_an_applied_patch_rewrites_the_event(self, manifest, gates):
        def redact(event, state):
            event.delta["n"] = 0
            return event

        engine = MicroPatchEngine().register("redact", redact)
        result = engine.apply(ev(n=5), State(), manifest, gates)
        assert result.applied == ["redact"]
        assert result.event.delta["n"] == 0

    def test_duplicate_patch_name_rejected(self):
        engine = MicroPatchEngine().register("p", lambda e, s: None)
        with pytest.raises(ValueError):
            engine.register("p", lambda e, s: None)


class TestReGovernance:
    def test_patched_output_is_re_governed(self, manifest, gates):
        def touch(event, state):
            event.delta["note"] = "harmless"
            return event

        engine = MicroPatchEngine().register("touch", touch)
        result = engine.apply(ev(), State(), manifest, gates)
        assert result.regoverned, "a patched event must go back through the gates"

    def test_a_patch_cannot_smuggle_content_past_the_gates(self, manifest, gates):
        """The closed hole: the archival pipeline would have let this through."""

        def smuggle(event, state):
            event.delta["payload"] = "this content is forbidden"
            return event

        engine = MicroPatchEngine().register("smuggle", smuggle)
        original = ev()
        result = engine.apply(original, State(), manifest, gates)

        assert result.applied == [], "the patch must be discarded"
        assert result.rejected, "and the rejection recorded"
        assert result.event is original, "with the original event restored"
        assert "forbidden" in result.rejected[0] or "fortress" in result.rejected[0]

    def test_a_patch_may_make_the_system_more_conservative(self, manifest, gates):
        """Patches may tighten. They may only never loosen."""

        def tighten(event, state):
            event.delta["n"] = 0
            return event

        engine = MicroPatchEngine().register("tighten", tighten)
        result = engine.apply(ev(n=999), State(), manifest, gates)
        assert result.applied == ["tighten"]
        assert result.event.delta["n"] == 0


class TestFaultContainment:
    def test_a_raising_patch_is_recorded_not_propagated(self, manifest, gates):
        def broken(event, state):
            raise RuntimeError("patch is broken")

        engine = MicroPatchEngine().register("broken", broken)
        result = engine.apply(ev(), State(), manifest, gates)
        assert not result.changed
        assert any("broken" in r for r in result.rejected)

    def test_a_patch_returning_the_wrong_type_is_rejected(self, manifest, gates):
        engine = MicroPatchEngine().register("wrong", lambda e, s: "not an event")
        result = engine.apply(ev(), State(), manifest, gates)
        assert not result.changed
        assert any("not Event" in r for r in result.rejected)


class TestLifecycle:
    def test_a_patch_expires_after_its_allowance(self, manifest, gates):
        def touch(event, state):
            event.delta["seen"] = True
            return event

        engine = MicroPatchEngine().register("touch", touch, expires_after=2)
        for _ in range(2):
            engine.apply(ev(), State(), manifest, gates)
        assert engine.active == []
        result = engine.apply(ev(), State(), manifest, gates)
        assert not result.changed

    def test_pruning_reports_what_it_removed(self, manifest, gates):
        engine = MicroPatchEngine().register(
            "t", lambda e, s: e, expires_after=1
        )
        engine.apply(ev(), State(), manifest, gates)
        assert engine.prune_expired() == ["t"]
        assert engine.patches == []

    def test_retire_removes_a_named_patch(self):
        engine = MicroPatchEngine().register("p", lambda e, s: None)
        assert engine.retire("p") is True
        assert engine.retire("p") is False
