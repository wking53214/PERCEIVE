"""The content gates are a word-list tripwire, not a safety control.

These tests record what the screen gets wrong today, so the README's claim is checked and a later
improvement shows up as a test that has to be edited on purpose. They are not a statement that the
behavior is right. See "What the content gates really check" in the README.
"""

import pytest

from perceive.gates import default_gates, evaluate_gates
from perceive.models import Event, Manifest, State


@pytest.fixture
def manifest():
    return Manifest("M", "v1", [], []).seal()


def allowed(text, manifest):
    event = Event("observation", {"note": text}, "p1")
    ok, _outcomes = evaluate_gates(default_gates(), event, State(), manifest)
    return ok


@pytest.mark.parametrize("text", ["tissue damage noted", "restricted diet", "no anomaly"])
def test_harmless_wording_that_contains_a_listed_word_is_refused(text, manifest):
    assert allowed(text, manifest) is False


@pytest.mark.parametrize("text", ["h4rm", "hurt the patient", "es peligroso"])
def test_harmful_wording_that_avoids_the_listed_words_passes(text, manifest):
    assert allowed(text, manifest) is True


def test_an_ordinary_note_passes(manifest):
    assert allowed("routine observation", manifest) is True
