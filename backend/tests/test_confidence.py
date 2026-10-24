import pytest

from app.agent.confidence import CEILING, FLOOR, components, score
from app.models import Verdict


def fact(source_type, stance="supports", weight=1.0):
    return {"source_type": source_type, "stance": stance, "weight": weight}


HERO = [fact("log"), fact("commit"), fact("code"), fact("doc"), fact("incident")]


def test_hero_evidence_scores_each_component():
    parts = {c.name: c.value for c in components(HERO, Verdict.SUPPORTED)}
    assert parts == {
        "coverage": 1.0,
        "strength": 1.0,
        "verifier": 1.0,
        "history": 1.0,
        "fix": 0.0,
        "contradiction": 0.0,
    }


def test_score_is_the_weighted_sum():
    # 0.25 + 0.20 + 0.25 + 0.10 = 0.80 before the fix is verified
    assert score(components(HERO, Verdict.SUPPORTED)) == pytest.approx(0.80)
    assert score(components(HERO, Verdict.SUPPORTED, fix_verified=True)) == CEILING


def test_contradictions_pull_the_score_down():
    with_doubt = HERO + [fact("log", "contradicts", 2.0)]
    assert score(components(with_doubt, Verdict.SUPPORTED)) < score(
        components(HERO, Verdict.SUPPORTED)
    )


def test_no_evidence_never_scores_zero():
    assert score(components([], Verdict.REFUTED)) == FLOOR
