"""Signal extraction from a live call, in two tiers.

The brief asks for intent shifts, compliance/risk, sentiment, buying signals,
missed opportunities and callback needs -- and separately warns against
"excessive low-value alerts". Those two requirements pull against each other,
so the design question is not "how do we detect more" but "what are we willing
to fire on".

**Tier 1 - deterministic (~0.1ms, no model).**
Compliance checklists and explicit phrases are rules, not judgement calls. A
required disclosure either was or was not said. Running an LLM over that is
slower, costs tokens, and is *less* reliable than a keyword check, because the
model will occasionally decide a paraphrase counts. Tier 1 also carries the
hard-negative guards: phrases that look like signals but are not.

**Tier 2 - LLM (~150ms).**
Reserved for things that genuinely need reading comprehension: is the customer
frustrated or just terse; was that a buying signal or a deflection; did the
agent miss an opening. Only runs on *finalised* utterances, and only when Tier
1 did not already explain the turn, because paying 150ms to confirm a keyword
match is waste.

Confidence is attached at detection time, not at nudge time, so the suppression
layer downstream can reason about it uniformly.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum


class SignalType(str, Enum):
    COMPLIANCE_GAP = "compliance_gap"
    MISSED_CROSS_SELL = "missed_cross_sell"
    RISING_FRUSTRATION = "rising_frustration"
    PAYMENT_DIFFICULTY = "payment_difficulty"
    BUYING_SIGNAL = "buying_signal"
    CALLBACK_NEEDED = "callback_needed"
    RISKY_STATEMENT = "risky_statement"
    TOPIC_SHIFT = "topic_shift"
    COMPETITOR_MENTION = "competitor_mention"


class Priority(int, Enum):
    CRITICAL = 1   # compliance / regulatory - agent must act before continuing
    HIGH = 2       # revenue or retention at risk right now
    MEDIUM = 3     # useful coaching
    LOW = 4        # informational


PRIORITY_BY_TYPE: dict[SignalType, Priority] = {
    SignalType.COMPLIANCE_GAP: Priority.CRITICAL,
    SignalType.RISKY_STATEMENT: Priority.CRITICAL,
    SignalType.RISING_FRUSTRATION: Priority.HIGH,
    SignalType.PAYMENT_DIFFICULTY: Priority.HIGH,
    SignalType.MISSED_CROSS_SELL: Priority.MEDIUM,
    SignalType.BUYING_SIGNAL: Priority.MEDIUM,
    SignalType.CALLBACK_NEEDED: Priority.MEDIUM,
    SignalType.COMPETITOR_MENTION: Priority.MEDIUM,
    SignalType.TOPIC_SHIFT: Priority.LOW,
}


@dataclass
class Signal:
    type: SignalType
    confidence: float
    evidence: str                 # the words that triggered it, for the audit trail
    speaker: str                  # agent | customer
    detected_at: float = field(default_factory=time.time)
    tier: int = 1
    detail: str = ""

    @property
    def priority(self) -> Priority:
        return PRIORITY_BY_TYPE.get(self.type, Priority.LOW)


# ---------------------------------------------------------------------------
# Tier 1: deterministic patterns
# ---------------------------------------------------------------------------

# Hard negatives come first and veto a match. Without these, "I'm not
# frustrated" and "no second car" both fire, which is exactly the
# low-value-alert failure the brief warns about.
NEGATION = re.compile(
    r"\b(not|no|never|don'?t|doesn'?t|didn'?t|won'?t|isn'?t|aren'?t|wasn'?t)\b"
    r"[^.!?]{0,28}$",
    re.I,
)

CUSTOMER_PATTERNS: dict[SignalType, list[re.Pattern[str]]] = {
    SignalType.MISSED_CROSS_SELL: [
        re.compile(r"\b(second|another|other|2nd)\s+(car|vehicle|bike|scooter|property|house|flat)\b", re.I),
        re.compile(r"\bmy (wife|husband|spouse|son|daughter|mother|father|parents?)\s+(also|too|as well)\b", re.I),
        re.compile(r"\b(also|as well)\s+(need|want|looking for)\b.{0,30}\b(cover|insurance|policy|plan)\b", re.I),
        re.compile(r"\bwe (just|recently)\s+(bought|got|purchased)\b", re.I),
    ],
    SignalType.RISING_FRUSTRATION: [
        re.compile(r"\b(this is )?(ridiculous|unacceptable|absurd|nonsense)\b", re.I),
        re.compile(r"\b(third|3rd|fourth|4th|fifth) time (i|we)('ve| have)? (called|rung|contacted)\b", re.I),
        re.compile(r"\b(fed up|sick of|tired of|had enough)\b", re.I),
        re.compile(r"\b(nobody|no one) (has |ever )?(called|responded|got back|helped)\b", re.I),
        re.compile(r"\b(waste of|wasting) (my )?time\b", re.I),
        re.compile(r"\b(speak|talk) to (your )?(manager|supervisor|someone senior)\b", re.I),
    ],
    SignalType.PAYMENT_DIFFICULTY: [
        re.compile(r"\b(can'?t|cannot|unable to) afford\b", re.I),
        re.compile(r"\b(lost|losing) (my |the )?job\b", re.I),
        re.compile(r"\b(too|very) expensive\b", re.I),
        re.compile(r"\b(tight|difficult|hard) (financially|month|right now)\b", re.I),
        re.compile(r"\b(don'?t|do not) have the money\b", re.I),
        re.compile(r"\b(instal?ment|emi|cicilan|angsuran)\b.{0,24}\b(late|miss|delay|can'?t)\b", re.I),
    ],
    SignalType.BUYING_SIGNAL: [
        re.compile(r"\b(how (do|can) i|what('s| is) the process to) (sign up|apply|buy|start|enrol)\b", re.I),
        re.compile(r"\bwhen (can|could) (it|the (policy|cover)) start\b", re.I),
        re.compile(r"\b(send|email) me (the )?(details|quote|form|proposal)\b", re.I),
        re.compile(r"\b(sounds|that'?s) (good|great|fine|reasonable)\b.{0,20}\b(go ahead|proceed|next)\b", re.I),
        re.compile(r"\bwhat (documents|papers|proof) (do i|would i) need\b", re.I),
    ],
    SignalType.CALLBACK_NEEDED: [
        re.compile(r"\b(call|ring|phone) me (back|later|tomorrow|after)\b", re.I),
        re.compile(r"\b(not|bad) a? ?good time\b", re.I),
        re.compile(r"\bi'?m (driving|in a meeting|at work|busy right now)\b", re.I),
    ],
    SignalType.COMPETITOR_MENTION: [
        re.compile(r"\b(star health|hdfc ergo|icici lombard|care health|aditya birla|manipal ?cigna|tata aig|bajaj allianz)\b", re.I),
        re.compile(r"\b(cheaper|better|lower) (quote|price|rate|premium) (from|with|at)\b", re.I),
        re.compile(r"\b(another|other) (company|insurer|provider) (offered|quoted|said)\b", re.I),
    ],
}

# Risky things the AGENT may say. These are compliance exposures, and they are
# the highest-value detections in the whole system because the cost of missing
# one is regulatory rather than commercial.
AGENT_RISK_PATTERNS: dict[SignalType, list[re.Pattern[str]]] = {
    SignalType.RISKY_STATEMENT: [
        re.compile(r"\b(guarantee|guaranteed|definitely|100% sure|i promise|certainly will)\b.{0,36}\b(approv|cover|accept|pay|claim)\w*", re.I),
        re.compile(r"\byou'?ll? definitely (get|be) (covered|approved|paid)\b", re.I),
        re.compile(r"\bno (waiting period|exclusions?|conditions?) at all\b", re.I),
        re.compile(r"\b(everything|all of it) (is|will be) covered\b", re.I),
        re.compile(r"\byour (claim|policy) (will|is going to) (be )?(approved|accepted)\b", re.I),
        # Collections conduct (Q3 Indonesia): threats are regulated.
        re.compile(r"\b(tarik unit|repossess|blacklist|legal action|court|police)\b", re.I),
    ],
}

# Disclosures the agent is REQUIRED to make. Tracked as a checklist over the
# whole call rather than per-utterance: the signal is the absence of a phrase
# by the time a trigger topic comes up, which is why this cannot be a simple
# per-turn regex.
REQUIRED_DISCLOSURES: dict[str, dict] = {
    "waiting_period": {
        "satisfied_by": re.compile(r"\bwaiting period\b", re.I),
        # Note the \w* suffixes: a trailing \b after a stem like "diabet" can
        # never match "diabetes", because there is no word boundary between
        # "t" and "e". Stems need an explicit suffix wildcard.
        "required_when": re.compile(
            r"\b(pre[- ]existing|ped|diabet\w*|hypertens\w*|bp|blood pressure|"
            r"existing condition|heart condition|asthma|thyroid)\b",
            re.I,
        ),
        "nudge": "Disclose the waiting period before moving on.",
    },
    "premium_not_final": {
        "satisfied_by": re.compile(r"\b(subject to|depends on|indicative|approximate|underwriting|may vary)\b", re.I),
        "required_when": re.compile(r"\b(premium|cost|price|pay)\b.{0,24}\b(\d[\d,]{2,}|lakh|thousand|rupees|rs\.?|inr)\b", re.I),
        "nudge": "Say the premium is indicative and subject to underwriting.",
    },
    "identity": {
        "satisfied_by": re.compile(r"\b(this is|my name is|i'?m)\b.{0,28}\b(from|calling (on behalf of|for))\b", re.I),
        "required_when": None,          # required at call start, unconditionally
        "nudge": "Identify yourself and the company.",
        # 20s, not 45s. Identification is expected in the opening
        # exchange, and a 45s window let this fire in the middle of the
        # call -- where it both arrived too late to be useful and
        # collided with other CRITICAL nudges for panel space.
        "deadline_seconds": 20.0,
    },
    "recording_or_bot": {
        "satisfied_by": re.compile(r"\b(automated|recorded|this call is being recorded|virtual assistant|ai assistant)\b", re.I),
        "required_when": re.compile(r"\b(are you (a )?(bot|robot|human|real)|am i (talking|speaking) to a (bot|machine|person))\b", re.I),
        "nudge": "Confirm plainly that this is an automated assistant.",
    },
}


@dataclass
class Utterance:
    speaker: str          # agent | customer
    text: str
    start_s: float        # seconds into the call
    end_s: float
    is_final: bool = True


class Tier1Detector:
    """Deterministic detection plus the running disclosure checklist."""

    def __init__(self) -> None:
        self.disclosures_made: set[str] = set()
        self.disclosures_pending: dict[str, float] = {}  # key -> first required at
        self.call_start: float | None = None

    def detect(self, utt: Utterance) -> list[Signal]:
        if self.call_start is None:
            self.call_start = utt.start_s

        signals: list[Signal] = []
        text = utt.text.strip()
        if not text:
            return signals

        if utt.speaker == "customer":
            signals.extend(self._match(text, CUSTOMER_PATTERNS, utt))
        else:
            signals.extend(
                self._match(text, AGENT_RISK_PATTERNS, utt,
                            apply_negation_veto=False)
            )

        signals.extend(self._check_disclosures(utt))
        return signals

    def _match(
        self,
        text: str,
        table: dict[SignalType, list[re.Pattern[str]]],
        utt: Utterance,
        apply_negation_veto: bool = True,
    ) -> list[Signal]:
        out: list[Signal] = []
        for sig_type, patterns in table.items():
            for pattern in patterns:
                m = pattern.search(text)
                if not m:
                    continue
                # Veto on local negation: "no second vehicle" is not a lead.
                #
                # Never applied to agent risk patterns. "Don't worry, your claim
                # will definitely be approved" contains a negation and is MORE
                # dangerous for it, not less -- the reassurance is the problem.
                # Applying the veto there suppressed exactly the detections
                # that matter most.
                if apply_negation_veto:
                    window = text[max(0, m.start() - 34): m.start()]
                    if NEGATION.search(window):
                        continue
                out.append(Signal(
                    type=sig_type,
                    # Deterministic matches are high-confidence by construction;
                    # the uncertainty lives in whether the rule was worth having,
                    # not in whether it fired.
                    confidence=0.88,
                    evidence=m.group(0)[:80],
                    speaker=utt.speaker,
                    tier=1,
                ))
                break  # one hit per type per utterance
        return out

    def _check_disclosures(self, utt: Utterance) -> list[Signal]:
        """Two kinds of required disclosure, with different firing rules.

        * **Deadline-based** (identity): required unconditionally within N
          seconds of the call opening. Fires as soon as the deadline passes
          without it being said -- there is nothing to wait for.

        * **Trigger-based** (waiting period, premium caveat): armed when the
          conversation raises the topic, then fires on the agent's *next* turn
          if they still have not said it. The one-turn grace matters: nudging
          an agent who was already about to say it is how a coaching panel
          becomes noise.

        An earlier version armed and fired in the same pass, so the `>`
        comparison was always false and no gap ever fired. The grace period is
        now expressed as "a later agent utterance", not as a timestamp
        comparison against the arming utterance itself.
        """
        signals: list[Signal] = []
        text = utt.text
        elapsed = utt.end_s - (self.call_start or 0.0)

        # 1. Mark anything the agent has now satisfied.
        if utt.speaker == "agent":
            for key, rule in REQUIRED_DISCLOSURES.items():
                if key in self.disclosures_made:
                    continue
                if rule["satisfied_by"].search(text):
                    self.disclosures_made.add(key)
                    self.disclosures_pending.pop(key, None)

        def fire(key: str) -> None:
            signals.append(Signal(
                type=SignalType.COMPLIANCE_GAP,
                confidence=0.92,
                evidence=f"missing disclosure: {key}",
                speaker="agent",
                tier=1,
                detail=REQUIRED_DISCLOSURES[key]["nudge"],
            ))
            self.disclosures_pending.pop(key, None)
            # Record it so the same gap does not re-fire every subsequent turn;
            # the nudge layer also dedupes, but not re-detecting is cheaper.
            self.disclosures_made.add(key)

        # 2. Deadline-based: fire the moment the deadline passes.
        for key, rule in REQUIRED_DISCLOSURES.items():
            if rule["required_when"] is not None or key in self.disclosures_made:
                continue
            if elapsed > rule.get("deadline_seconds", 45.0):
                fire(key)

        # 3. Trigger-based: fire on a LATER agent turn than the one that armed it.
        if utt.speaker == "agent":
            for key, armed_at in list(self.disclosures_pending.items()):
                if key in self.disclosures_made:
                    continue
                if utt.start_s > armed_at:
                    fire(key)

        # 4. Arm anything the current utterance has just made required.
        for key, rule in REQUIRED_DISCLOSURES.items():
            trigger = rule["required_when"]
            if trigger is None:
                continue
            if key in self.disclosures_made or key in self.disclosures_pending:
                continue
            if trigger.search(text):
                self.disclosures_pending[key] = utt.end_s

        return signals
