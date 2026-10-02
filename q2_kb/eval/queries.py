"""Retrieval evaluation set.

The brief asks for at least five queries spanning product, policy,
qualification, FAQ and objection intents. This set has 18, because five is not
enough to expose the two failure modes that actually matter for a voice agent:

* **Paraphrase vs jargon.** The same fact is asked twice -- once in insurance
  jargon ("PED waiting period") and once in a caller's own words ("I already
  have diabetes, will that be covered?"). If only one retrieves correctly, the
  hybrid fusion is not earning its complexity.

* **Refusal.** Four queries are deliberately unanswerable from this KB. A
  retrieval set with no negatives measures only recall, and a system tuned on
  recall alone will happily answer everything -- which is the specific failure
  the brief calls out.

`expect` is the intended system behaviour, not a gold document id. Scoring a
voice KB on exact-chunk match is misleading, because several chunks can be
legitimately correct; what matters is whether the right *fact* surfaced and
whether the gate fired when it should have.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Intent = Literal[
    "product", "policy", "qualification", "faq", "objection",
    "claims", "out_of_scope",
]


@dataclass(frozen=True)
class EvalQuery:
    qid: str
    question: str
    intent: Intent
    expect: Literal["answer", "refuse"]
    # What a correct retrieval must contain for a human grader to mark it
    # correct. Kept as keywords rather than a gold id -- see module docstring.
    must_mention: tuple[str, ...] = ()
    note: str = ""


QUERIES: list[EvalQuery] = [
    # ---------------- product ----------------
    EvalQuery(
        qid="Q01",
        question="What is covered under the ReAssure plan?",
        intent="product",
        expect="answer",
        must_mention=("reassure",),
        note="Named-product lookup; tests that product pages indexed cleanly.",
    ),
    EvalQuery(
        qid="Q02",
        question="Do you cover AYUSH or ayurvedic treatment?",
        intent="product",
        expect="answer",
        must_mention=("ayush",),
        note="Benefit block extracted from a Mantine accordion, not prose.",
    ),
    EvalQuery(
        qid="Q03",
        question="Is maternity covered and what is the waiting period for it?",
        intent="product",
        expect="answer",
        must_mention=("maternity",),
        note="Two-part question; chunk must carry benefit AND its condition.",
    ),

    # ---------------- policy / coverage ----------------
    EvalQuery(
        qid="Q04",
        question="What is the waiting period for pre-existing diseases?",
        intent="policy",
        expect="answer",
        must_mention=("pre-existing", "waiting"),
        note="Core policy rule; the single most common qualification question.",
    ),
    EvalQuery(
        qid="Q05",
        question="PED waiting period",
        intent="policy",
        expect="answer",
        must_mention=("pre-existing",),
        note="JARGON form of Q04. Tests BM25 + canonical-term expansion. If "
             "this fails while Q04 passes, terminology normalisation is not "
             "working.",
    ),
    EvalQuery(
        qid="Q06",
        question="I already have diabetes, will my treatment be covered?",
        intent="policy",
        expect="answer",
        must_mention=("pre-existing",),
        note="PARAPHRASE form of Q04 with no shared vocabulary. Tests dense "
             "retrieval. Q04/Q05/Q06 together isolate which retriever works.",
    ),
    EvalQuery(
        qid="Q07",
        question="What is a co-payment and when does it apply?",
        intent="policy",
        expect="answer",
        must_mention=("co-pay",),
    ),
    EvalQuery(
        qid="Q08",
        question="What is the room rent limit on my policy?",
        intent="policy",
        expect="answer",
        must_mention=("room rent",),
    ),
    EvalQuery(
        qid="Q09",
        question="What is the free look period?",
        intent="policy",
        expect="answer",
        must_mention=("free look",),
    ),

    # ---------------- qualification / eligibility ----------------
    EvalQuery(
        qid="Q10",
        question="What is the maximum age to buy a health insurance policy?",
        intent="qualification",
        expect="answer",
        must_mention=("age",),
        note="Drives the qualification branch of the Q1 call flow.",
    ),
    EvalQuery(
        qid="Q11",
        question="Can I include my parents in a family floater policy?",
        intent="qualification",
        expect="answer",
        must_mention=("parent", "family"),
    ),

    # ---------------- FAQ ----------------
    EvalQuery(
        qid="Q12",
        question="What documents do I need to buy health insurance?",
        intent="faq",
        expect="answer",
        must_mention=("document",),
    ),
    EvalQuery(
        qid="Q13",
        question="How do I make a cashless claim?",
        intent="claims",
        expect="answer",
        must_mention=("cashless",),
    ),
    EvalQuery(
        qid="Q14",
        question="Why do health insurance claims get rejected?",
        intent="claims",
        expect="answer",
        must_mention=("claim",),
    ),

    # ---------------- objection ----------------
    EvalQuery(
        qid="Q15",
        question="Health insurance is too expensive, why should I bother?",
        intent="objection",
        expect="answer",
        must_mention=(),
        note="Objection handling must be grounded in real cost/benefit "
             "content, not improvised by the model.",
    ),
    EvalQuery(
        qid="Q16",
        question="I already have insurance from my employer, why do I need my own?",
        intent="objection",
        expect="answer",
        must_mention=(),
        note="Classic bancassurance/corporate-cover objection.",
    ),

    # ---------------- out of scope: MUST refuse ----------------
    EvalQuery(
        qid="Q17",
        question="What is my current policy balance and when is my next premium due?",
        intent="out_of_scope",
        expect="refuse",
        note="Account-specific. No KB can answer this; must escalate to a human "
             "rather than guess.",
    ),
    EvalQuery(
        qid="Q18",
        question="What is the capital of France?",
        intent="out_of_scope",
        expect="refuse",
        note="Pure off-domain. If the gate lets this through, the threshold is "
             "too low.",
    ),
    EvalQuery(
        qid="Q19",
        question="Can you sell me car insurance for my new Honda?",
        intent="out_of_scope",
        expect="refuse",
        note="Adjacent domain -- harder than Q18, because 'insurance' matches "
             "lexically across the whole corpus.",
    ),
    EvalQuery(
        qid="Q20",
        question="What was Niva Bupa's net profit last quarter?",
        intent="out_of_scope",
        expect="refuse",
        note="On-brand but financial; the corpus has no financial reporting.",
    ),
]


def by_intent() -> dict[str, list[EvalQuery]]:
    out: dict[str, list[EvalQuery]] = {}
    for q in QUERIES:
        out.setdefault(q.intent, []).append(q)
    return out
