"""The confidence score: computed from the evidence, never taken from the model.

A model's own "I'm 92% sure" means little (Nano was 92% sure of a wrong cause in our first
test). This score instead asks checkable questions about the evidence, each 0..1:

    coverage       how many independent kinds of source support the cause (of 4)
    strength       how much supporting evidence there is (5 or more facts = full)
    verifier       the verifier's verdict: supported 1, weak 0.4, refuted 0
    history        1 if a past incident supports it
    fix            1 once a patch is verified in the sandbox (Phase 6)
    contradiction  share of the evidence weight that argues against it (subtracted)

Each component has a fixed weight; the score is the weighted sum, clamped to [0.05, 0.97]:
never certain, never zero.
"""

from app.models import EvidenceSourceType, Stance, Verdict
from app.schemas.events import ConfidenceComponent

WEIGHTS = {
    "coverage": 0.25,
    "strength": 0.20,
    "verifier": 0.25,
    "history": 0.10,
    "fix": 0.20,
    "contradiction": -0.30,
}
VERDICT_VALUE = {Verdict.SUPPORTED: 1.0, Verdict.WEAK: 0.4, Verdict.REFUTED: 0.0}

# Coverage counts independent kinds of evidence; a doc and a past incident are both "what we
# already knew", while logs and production data are both "what we observed".
SOURCE_GROUP = {
    EvidenceSourceType.CODE: "code",
    EvidenceSourceType.COMMIT: "commit",
    EvidenceSourceType.LOG: "observed",
    EvidenceSourceType.DATABASE: "observed",
    EvidenceSourceType.DOC: "known",
    EvidenceSourceType.INCIDENT: "known",
    EvidenceSourceType.ISSUE: "known",
    EvidenceSourceType.MEMORY: "known",
}
FLOOR, CEILING = 0.05, 0.97


def components(facts: list[dict], verdict: Verdict | None, fix_verified: bool = False):
    """Each component's value, from facts shaped like {source_type, stance, weight}."""
    supporting = [f for f in facts if f["stance"] == Stance.SUPPORTS]
    against = [f for f in facts if f["stance"] == Stance.CONTRADICTS]
    total_weight = sum(f["weight"] for f in facts) or 1.0
    groups = {SOURCE_GROUP[EvidenceSourceType(f["source_type"])] for f in supporting}
    values = {
        "coverage": len(groups) / 4,
        "strength": min(1.0, sum(f["weight"] for f in supporting) / 5),
        "verifier": VERDICT_VALUE.get(verdict, 0.0),
        "history": float(any(f["source_type"] == EvidenceSourceType.INCIDENT for f in supporting)),
        "fix": float(fix_verified),
        "contradiction": sum(f["weight"] for f in against) / total_weight,
    }
    return [ConfidenceComponent(name=n, value=v, weight=WEIGHTS[n]) for n, v in values.items()]


def score(parts: list[ConfidenceComponent]) -> float:
    """The weighted sum of the components, clamped to [FLOOR, CEILING]."""
    raw = sum(p.value * p.weight for p in parts)
    return round(min(CEILING, max(FLOOR, raw)), 3)
