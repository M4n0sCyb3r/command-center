"""Scoring an agent run, and measuring the thing that does the scoring.

Lab 8 measured AGREEMENT: do repeated runs say the same thing? Five runs
agreeing on a wrong answer scored 1.0, because agreement never looks at the
truth. Lab 9 built the truth - 21 cases carrying `expected_facts` as a DICT
and `expected_tools` as the minimum honest trajectory. Neither lab ever said
PASS or FAIL.

This file does. It scores the OUTCOME and the TRAJECTORY separately, and then
turns the instrument on itself, because a scorer nobody has measured is an
opinion with a percent sign after it.

Run:  python src/score.py
"""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from structured import ask_json
from suite import parse_suite

# Resolved from THIS FILE, not from the working directory. `python src/score.py`
# and `python score.py` put different things in cwd; the suite does not move.
SUITE = Path(__file__).resolve().parent.parent / "evals" / "suites" / "agent.jsonl"

# BUILD STEP 3 PART 2 SPLIT THE SUITE, and this path is the visible half of it.
# agent.jsonl used to hold 21 lines: 13 runnable cases AND 8 hand-labelled
# calibration fixtures, which are not cases - they carry `candidate_facts` and a
# human `label`, have no `expected_tools`, and exist to measure the comparator
# rather than the agent.
#
# The collision was invisible for as long as nothing had to name a scorer for
# the file. Once a suite declares one (see suite.py), handing those 8 lines to
# the agent scorer would produce 8 rows whose `passed` means nothing - which
# inflates the pass rate while looking like a measurement, the same
# vacuous-field defect this project refused when it declined to write
# passed=True on unanswerable retrieval cases.
#
# So they live here instead, in a file declaring `"scorer": null` - not a suite,
# and the harness must refuse it. A file is not a suite, and the file could not
# say so until it had a header.
FIXTURES = (Path(__file__).resolve().parent.parent
            / "evals" / "fixtures" / "judge-calibration.jsonl")


def load_suite(path: Path = SUITE) -> list[dict[str, Any]]:
    """Read the golden set. One header object, then one case per line.

    JSONL and not JSON because a suite only ever grows: appending a case is one
    line at the end, and a diff shows exactly the case that was added rather
    than a reindented array.

    BUILD STEP 3 PART 2: the parsing moved to suite.parse_suite(), shared with
    retrieval.py so the two cannot drift on what a header, a tier or a duplicate
    id is. Return type is unchanged - a list of plain dicts - so nothing
    downstream of here noticed.
    """
    return parse_suite(path).cases


def load_calibration(path: Path = FIXTURES) -> list[dict[str, Any]]:
    """Read the hand-labelled judge fixtures. NOT cases - see FIXTURES above.

    Parsed by the same function as a suite, which still checks the things that
    matter for a fixture: one header, unique ids, valid JSON naming its line.
    What it deliberately does NOT check is a tier, because these entries are
    never scored by the harness and a tier only means something for something
    that gates. parse_suite() enforces that in both directions.
    """
    suite = parse_suite(path)
    if suite.header.runnable:
        # A fixtures file that has grown a scorer is a fixtures file somebody is
        # about to run as a suite. Caught here rather than discovered in a
        # report whose pass rate includes eight meaningless rows.
        raise ValueError(
            f"{suite.path.name} declares scorer={suite.header.scorer!r} - "
            "calibration fixtures must declare scorer=null"
        )
    # `candidate_text` (P04 scorer defect 1 part 2) is an agent's answer
    # VERBATIM. When present, calibrate() hands it to the judge instead of
    # rendering candidate_facts. A present-but-empty one would silently measure
    # the judge on "" - so it is refused here, by name, before any measurement.
    for c in suite.cases:
        if "candidate_text" in c and not (isinstance(c["candidate_text"], str)
                                          and c["candidate_text"].strip()):
            raise ValueError(f"{suite.path.name}: {c.get('id')!r} has a "
                             "candidate_text that is not a non-empty string")
    return suite.cases


def normalize(value: Any) -> Any:
    """Put a fact value into the one form the comparator compares.

    Three decisions live here, and they are decisions, not obvious truths:

      - strings are stripped and casefolded, so "Stopped" == "stopped"
      - lists are SORTED, so ["Spooler","wuauserv"] == ["wuauserv","Spooler"]
      - everything else is left alone

    Sorting is the interesting one. "Which services are down" has no natural
    order, so order is noise and scoring it would fail correct answers. If a
    future case ever asks for a SEQUENCE - restart order, say - that case needs
    its own comparator, because this one would call a wrong order right.
    """
    if isinstance(value, str):
        return value.strip().casefold()
    if isinstance(value, list):
        return sorted(normalize(v) for v in value)
    return value


def compare_facts(expected: dict[str, Any] | None,
                  actual: dict[str, Any] | None) -> bool:
    """Exact match on the fact dict, after normalization.

    None means "there is no answer to give" - the negative case. Lab 9's
    `negative-unknown-service` sets `expected_facts: null` for a service that
    does not exist, so a correct agent REFUSES. Both None is therefore a pass,
    and exactly one None is a fail: an answer where none was warranted is the
    fabrication we are hunting, and a refusal where an answer existed is a
    different failure with the same score.

    Key sets must match EXACTLY. A candidate that reports three of four
    services has not given a partially right answer to an aggregate question;
    it has given a wrong one, and Lab 8 watched an agent do exactly that.
    """
    # P04 SCORER DEFECT 1, FIXED 2026-09-23 (option B, Jose's call). On a
    # negative case `{}` now PASSES, alongside None. That is only safe because
    # facts_schema() gives a negative case an OPEN form: a fabrication such as
    # "Fax is running" now has somewhere to land ({"Fax": "running"}) and fails
    # here. Before, the form had zero fields, so `{}` meant EITHER "asserted
    # nothing" OR "fabricated something it had no box for" - opposite verdicts,
    # one value. Reason: "an empty form can't tell a correct refusal from a
    # fabrication it had no box for, and treating {} as a refusal on the old
    # form would let a made-up answer pass." The two changes are one fix; either
    # alone is wrong (this line alone = a false-pass path; the form alone = the
    # refusal still fails). Guarded by calibration's zero-false-pass gate.
    if expected is None:
        return actual is None or actual == {}
    if actual is None:
        return False
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return False

    exp = {normalize(k): normalize(v) for k, v in expected.items()}
    act = {normalize(k): normalize(v) for k, v in actual.items()}
    return exp == act



@dataclass(frozen=True)
class Score:
    """One case, scored. Frozen for the same reason Lab 8's Trial was: a result
    that can be mutated after the fact is not evidence."""

    case_id: str
    outcome: bool                   # did the answer match expected_facts
    trajectory: bool                # were the required calls made
    earned: bool                    # BOTH - see score_case()
    missing: tuple[str, ...] = ()   # required calls that never happened
    extra: int = 0                  # calls beyond the required minimum
    note: str = ""
    # WHAT THE JUDGE READ OUT OF THE ANSWER. Added 2026-09-21, build step 3
    # part 2c, after the harness's FIRST REAL RUN of this suite: 5 of 13 cases
    # failed on `outcome` while their answer text looked correct, and the row
    # in the run record could not distinguish "the agent got the facts wrong"
    # from "the judge failed to read a correct answer".
    #
    # Those are different defects with different fixes - one is the agent, one
    # is Lab 10's false-fail rate - and `outcome: False` alone cannot tell them
    # apart. ADR 0007 chose the self-contained record because Jose's stated
    # reason was that it would be CHEAPER TO INVESTIGATE; without this field it
    # was not cheap enough.
    #
    # None means the judge returned no facts (a declines / not_found stance) OR
    # raised before returning anything. `judged` below tells those apart.
    actual: Any = None
    # JUDGE-ERROR GATING (2026-09-27). False = the judge RAISED, so `outcome`
    # was never measured and its False is a placeholder, not a finding. Kept a
    # separate field rather than read off `note` ("judge error: ..."): a
    # verdict must not hang on a string prefix a later edit can reword.
    # scorers.agent_row turns this into the spine's "unscored" - but only when
    # the verdict actually depended on the judge (Jose's sub-call i).
    judged: bool = True


def calls_made(result: Any) -> list[tuple[str, dict[str, Any]]]:
    """Every tool call in a run's trace, as (tool, arguments).

    Read off Step, not off the answer text. The trace is the only record of
    what the agent DID; the answer is a record of what it SAID. Lab 8 proved
    those come apart.
    """
    made: list[tuple[str, dict[str, Any]]] = []
    for step in getattr(result, "steps", []):
        if step.tool:
            made.append((step.tool, step.arguments or {}))
    return made


def _satisfies(required: dict[str, Any],
               made: list[tuple[str, dict[str, Any]]]) -> bool:
    """Was this one required call made anywhere in the run?

    Order-insensitive, and the arguments are a SUBSET check, not equality: the
    case names what must appear in the call, and a tool is free to take options
    the case does not care about.
    """
    want_tool = normalize(required["tool"])
    want_args = {normalize(k): normalize(v)
                 for k, v in (required.get("arguments") or {}).items()}
    for tool, args in made:
        if normalize(tool) != want_tool:
            continue
        got = {normalize(k): normalize(v) for k, v in args.items()}
        if all(got.get(k) == v for k, v in want_args.items()):
            return True
    return False


def score_trajectory(result: Any,
                     expected_tools: list[dict[str, Any]] | None
                     ) -> tuple[bool, tuple[str, ...], int]:
    """Did the run make every call the case requires? Returns (ok, missing, extra).

    THE RULING THAT COST SOMETHING TO FIND: a required call counts as made when
    it was ATTEMPTED, not when it succeeded.

    The obvious rule - "it only counts if ok is True" - fails Lab 9's negative
    case. `Fax` is not in SERVICES, so get_service_status("Fax") raises and
    dispatch returns ok=False. That failed lookup IS the honest work: it is the
    evidence the refusal rests on. Requiring success would score the one agent
    that checked properly the same as one that refused off the top of its head,
    which inverts the exact behaviour this suite exists to reward.

    Extra calls are COUNTED, never penalised. expected_tools is the minimum
    honest trajectory, so an agent that also called list_services() first did
    more work than required, not less. Charging for that would be an efficiency
    metric wearing a correctness metric's clothes, and Lab 8 already showed
    those two diverge.
    """
    required = expected_tools or []
    made = calls_made(result)
    missing = tuple(
        f"{r['tool']}({json.dumps(r.get('arguments') or {}, sort_keys=True)})"
        for r in required if not _satisfies(r, made)
    )
    return (not missing), missing, max(0, len(made) - len(required))



# The judge is told to be an EXTRACTOR, twice: once in the role, once in "do
# not use your own knowledge". The second is not redundant. A model that knows
# Windows will happily fill in what a Spooler status USUALLY is when the answer
# text does not say - and a judge that supplies facts the agent never stated is
# scoring its own opinion.
#
# MEASURED DEFECT, and the fix (Lab 10, qwen3:8b):
#   v1 asked for a boolean `answered`. On the refusal case - where the form has
#   ZERO fields, so the boolean is the whole output - the judge read "there is
#   no such service" as an answer, returned answered=true with facts {}, and
#   compare_facts(None, {}) scored a correct refusal WRONG.
#   Calibration: agreement 0.88, false-pass 0.00, false-fail 0.25.
#   v2 replaces the boolean with a three-way STANCE. The point is structural,
#   not lexical: a boolean called "answered" has no stable meaning on an empty
#   form, and no amount of prompt wording fixes a badly shaped output. Naming
#   the three non-answers separately also makes the verdict auditable - you can
#   see WHICH kind of non-answer the judge saw, not just that it saw one.
JUDGE_SYSTEM = (
    "You are an extraction tool, not a grader. You will be shown a question "
    "that was put to a Windows operations assistant, and that assistant's "
    "answer. Report ONLY what the answer explicitly states. Do not use your "
    "own knowledge of Windows services. Do not infer, complete, or correct "
    "anything.\n\n"
    "Set stance to exactly one of the three below. Decide them IN THIS ORDER "
    "and use the FIRST that fits - existence is decided before value:\n"
    "  not_found  - the answer says the thing asked about does not exist or "
    "cannot be found on the host. This wins even when the answer also "
    "phrases that as a value.\n"
    "  declines   - the answer refuses, hedges, or says it cannot determine\n"
    "  states     - the answer asserts a concrete value for something it "
    "treats as existing\n\n"
    "Only 'states' carries facts. For 'declines' and 'not_found', omit facts."
)
# P04 SCORER DEFECT 1 PART 2b (option A, Jose's call, 2026-09-24). The three
# stances used to be listed with no precedence, and they OVERLAPPED: the agent's
# real refusal names a "value" that is itself a statement of non-existence, so
# it fit both `states` and `not_found` truthfully and the judge took the first
# one listed. Now they are a partition, decided in a fixed order. Reason:
# "existence is decided before value, so one sentence can only ever get one
# label." STANCES below is deliberately NOT reordered - one change per measure.
# The rule is stated as a PRINCIPLE with no example: quoting the calibration
# sentence here would teach the judge its own test (tests/test_stance_order.py
# checks that no calibration prose leaks into the prompt).

JUDGE_PROMPT = """QUESTION PUT TO THE ASSISTANT:
{question}

THE ASSISTANT'S ANSWER:
{answer}

Report what the answer states."""

STANCES = ["states", "declines", "not_found"]

# P04 SCORER DEFECT 2 (option A, Jose's call, 2026-09-24): a CLOSED VOCABULARY.
# Every slot used to accept any string, so the judge wrote a service's NAME, its
# start type, or a whole sentence into a status field. The form is enforced
# WHILE the judge writes (Ollama masks tokens that violate the schema), so an
# enum makes those values impossible to produce - not discouraged, impossible.
# Jose's reason: "it makes the wrong answers impossible to write."
#
# UNLISTED is the escape hatch, and it is a SAFETY rule, not a convenience: a
# claim outside the vocabulary ("Fax is down") must still be recordable, or the
# judge is forced to drop it and a list can come out matching - a false pass.
# UNLISTED is forbidden inside the vocabulary and so can never equal an
# expected value: an out-of-vocabulary claim always FAILS.
UNLISTED = "unlisted"
VOCABULARY_KEYS = ("status", "service")


def check_vocabulary(vocabulary: Any, cases: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Validate a suite's declared answer space against its own cases.

    Raises ValueError (a tooling error, exit 2 at the harness) - a suite whose
    vocabulary cannot express its own answer key would fail every case for a
    reason that has nothing to do with the agent.

    THE RULE THAT MATTERS MOST: the vocabulary is the HOST's answer space, never
    derived from expected_facts. An enum of ["stopped"] would force every answer
    to pass. So each list must offer at least two values - a form must always be
    able to express a WRONG answer.
    """
    if vocabulary is None:
        raise ValueError("the agent suite's header must declare a 'vocabulary' "
                         f"with keys {list(VOCABULARY_KEYS)} (P04 scorer defect 2)")
    if not isinstance(vocabulary, dict) or set(vocabulary) != set(VOCABULARY_KEYS):
        raise ValueError(f"'vocabulary' must have exactly the keys {list(VOCABULARY_KEYS)}")
    for key in VOCABULARY_KEYS:
        values = vocabulary[key]
        if (not isinstance(values, list) or len(values) < 2
                or not all(isinstance(v, str) and v.strip() for v in values)):
            raise ValueError(f"vocabulary[{key!r}] must list at least two non-empty "
                             "strings - a one-value form can only say the right answer")
        folded = [v.casefold() for v in values]
        if len(set(folded)) != len(folded):
            raise ValueError(f"vocabulary[{key!r}] repeats a value")
        for reserved in (UNLISTED, NOT_STATED):   # defect 3 adds NOT_STATED
            if reserved in folded:
                why = ("it is the escape hatch" if reserved == UNLISTED
                       else "it means the answer said nothing")
                raise ValueError(f"vocabulary[{key!r}] may not contain {reserved!r} - "
                                 f"{why} and must never match")
    status = {v.casefold() for v in vocabulary["status"]}
    service = {v.casefold() for v in vocabulary["service"]}
    for case in cases:
        for key, value in (case.get("expected_facts") or {}).items():
            items, space, name = ((value, service, "service") if isinstance(value, list)
                                  else ([value], status, "status"))
            missing = [v for v in items if str(v).casefold() not in space]
            if missing:
                raise ValueError(f"case {case.get('id')!r}: expected {key}={missing} is "
                                 f"not in vocabulary[{name!r}] - the case could never pass")
    return vocabulary


# ---------------------------------------------------------------------------
# P04 SCORER DEFECT 3 (option A, Jose's call, 2026-09-24): THE JUDGE EXTRACTS,
# CODE INTERPRETS.
#
# All three scorer defects had the judge asked to INTERPRET, not extract: is
# "not installed" a value or a refusal (1), what belongs in a slot called
# `Spooler` (2), does "stopped" count as an "outage" (3). The judge was told
# "do not infer" and then handed a form that required inference.
#
# So the judge will report ONE READING PER SERVICE - its stated status and
# start type, in the closed vocabulary - and everything a case asks about is
# DERIVED from those readings here, by rules that are plain code with tests.
# Jose's reason: "the 4 single-service cases already use this shape" - the
# aggregate cases move onto the only form the judge is MEASURED reliable on.
#
# Build step 1 of 3 adds the rules only. Nothing calls derive_facts() until
# step 2 changes the judge's form.
# ---------------------------------------------------------------------------

# The answer does not mention this service (or this field of it). A THIRD
# reserved value beside UNLISTED, with a different meaning:
#   NOT_STATED - the answer made no claim  -> contributes nothing to a list
#   UNLISTED   - the answer made a claim the vocabulary cannot express
#                -> poisons any list it would feed, so that list never matches
# The difference is the safety argument. A concise correct answer ("Spooler
# and wuauserv are stopped") says nothing about W32Time, and must still pass;
# so silence cannot fail a list. But a claim we cannot read might be the one
# that makes the list wrong, so an unreadable claim must never pass one.
# Silence rule confirmed by Jose, 2026-09-25: "it needs a pass rate that
# measures the agent" - failing concise correct answers would measure
# the agent's wordiness instead. Rejected: every service must be named.
NOT_STATED = "not_stated"
RUNNING = "running"
RESERVED = (UNLISTED, NOT_STATED)


def _is_down(reading: dict[str, str]) -> bool | None:
    """Sub-call (i), Jose's, 2026-09-24: DOWN MEANS ANYTHING BUT `running`.

    Stopped, paused and every *_pending state are down - "is anything down?"
    should surface a service stuck in stop_pending. Rejected (ii): stopped only.

    Returns None when the reading cannot say (NOT_STATED) and raises nothing;
    UNLISTED is handled by the caller, which poisons the list.
    """
    status = reading.get("status", NOT_STATED)
    if status == NOT_STATED:
        return None
    return status != RUNNING


def _is_automatic(reading: dict[str, str]) -> bool | None:
    start_type = reading.get("start_type", NOT_STATED)
    if start_type == NOT_STATED:
        return None
    return start_type == "automatic"


# Aggregate key -> (the reading field it depends on, the rule). A new aggregate
# is one row here and one test; the judge's form does not change.
DERIVATIONS: dict[str, tuple[str, Any]] = {
    "down": ("status", _is_down),
    "automatic": ("start_type", _is_automatic),
}


def derive_facts(keys: list[str] | tuple[str, ...],
                 readings: dict[str, dict[str, str]],
                 services: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Turn per-service readings into the facts a case asks for.

    `keys` are the case's expected_facts keys (never its values - the same
    defence as facts_schema). `services` is the vocabulary's service list.

      - a key that IS a service name reads that service's status directly
        (the single-service cases). NOT_STATED / UNLISTED come through as
        themselves and can never equal a vocabulary status, so they fail.
      - a key in DERIVATIONS applies its rule to every service's reading.
        Built from a dict, so a service can appear at most once - the
        ['Spooler', 'Spooler'] extraction from attempt A cannot recur.
      - any other key is a suite that asks something no rule answers: a
        tooling error (ValueError -> exit 2), never a silent fail.
    """
    folded = {s.casefold(): s for s in services}
    facts: dict[str, Any] = {}
    for key in keys:
        if key.casefold() in folded:
            reading = readings.get(folded[key.casefold()], {})
            facts[key] = reading.get("status", NOT_STATED)
            continue
        if key not in DERIVATIONS:
            raise ValueError(f"no rule derives {key!r} - add a row to DERIVATIONS "
                             "or name a service from the vocabulary")
        field, rule = DERIVATIONS[key]
        items: list[str] = []
        for service in services:
            reading = readings.get(service, {})
            if reading.get(field) == UNLISTED:
                items.append(UNLISTED)          # poison: this list can never match
                continue
            if rule(reading):
                items.append(service)
        facts[key] = sorted(items)
    return facts


def load_vocabulary(path: Path = SUITE) -> dict[str, list[str]]:
    """The agent suite's vocabulary, validated against its own cases."""
    suite = parse_suite(path)
    return check_vocabulary(suite.header.vocabulary, suite.cases)


def facts_schema(expected: dict[str, Any] | None,
                 vocabulary: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Build the extraction form from the CASE, never from the answer.

    The judge does not choose the shape of its own output. The case says which
    fields exist and whether each is a single value or a list; the judge only
    fills them in. That is what keeps extraction from drifting into summary.

    The field keys are NOT marked required. Completeness is compare_facts()'s
    job - it demands an exact key-set match - and a rule enforced in two places
    is a rule that will eventually disagree with itself.

    RESIDUAL LEAK, stated rather than hidden: the field NAMES reach the judge.
    For {"down": [...]} it learns the answer is about things being down. It
    never learns WHICH, and the values are the whole of what is scored - but it
    is a real hole, and a suite with leading key names would widen it.
    """
    # With a VOCABULARY (defect 2), a status slot is an enum of the declared
    # states and a list slot an array of declared service names, each plus
    # UNLISTED. Without one (scripted fixtures only - the real agent scorer
    # refuses to run without it) the old free-string form is kept.
    def slot(value: Any) -> dict[str, Any]:
        if isinstance(value, list):
            item = ({"type": "string"} if vocabulary is None else
                    {"type": "string", "enum": [*vocabulary["service"], UNLISTED]})
            return {"type": "array", "items": item}
        return ({"type": "string"} if vocabulary is None else
                {"type": "string", "enum": [*vocabulary["status"], UNLISTED]})

    props: dict[str, Any] = {key: slot(value) for key, value in (expected or {}).items()}
    # A NEGATIVE case (expected None) gets an OPEN form - any key, string
    # values - so a fabricated claim has somewhere to be recorded and can fail.
    # A closed form with zero fields made `{}` ambiguous (P04 scorer defect 1;
    # see compare_facts). Positive cases stay CLOSED: the case, not the judge,
    # chooses their shape, and that is still the whole defence against drift
    # into summary. The field-name leak noted above does not widen here - an
    # open form names no fields at all.
    facts = ({"type": "object", "properties": props, "additionalProperties": False}
             if expected is not None else
             {"type": "object", "additionalProperties": {"type": "string"}})
    return {
        "type": "object",
        "properties": {
            "stance": {"type": "string", "enum": STANCES},
            "facts": facts,
        },
        "required": ["stance"],
        "additionalProperties": False,
    }


def extract_facts(question: str, answer: str,
                  expected: dict[str, Any] | None, *,
                  vocabulary: dict[str, list[str]] | None = None,
                  **kwargs: Any) -> dict[str, Any] | None:
    """Read prose, return the facts it asserts - or None if it asserts none.

    `expected` is used ONLY to build the schema. Its VALUES never enter the
    prompt. That is the whole defence: a judge shown the right answer agrees
    with the right answer, and then the scoreboard measures agreeableness.

    Any stance but `states` returns None, which compare_facts() already treats
    as a correct refusal - so the refusal path needs no special case anywhere
    downstream.
    """
    data = ask_json(
        JUDGE_PROMPT.format(question=question, answer=answer),
        facts_schema(expected, vocabulary),
        system=JUDGE_SYSTEM,
        **kwargs,
    )
    if data.get("stance") != "states":
        return None
    return data.get("facts") or {}


def score_case(case: dict[str, Any], result: Any, *,
               judge: Any = extract_facts,
               vocabulary: dict[str, list[str]] | None = None,
               **kwargs: Any) -> Score:
    """Score one run against one case: outcome, trajectory, and both.

    `judge=` is the third SEAM in this course, after Lab 7's `reviewer=` and
    Lab 8's `runner=`. Same payoff: the smoke test drives this whole function
    with a scripted judge and no model at all.

    A judge that CRASHES is recorded - `judged=False` plus a note - not
    raised. (Until 2026-09-27 it was recorded as a failed OUTCOME, which gated
    a broken instrument as a failed agent; see Score.judged.) Fourth time this ruling has applied - ToolResult (L3), AgentResult
    (L4), attempt() (L8), and now here. A judge timing out on case 14 must not
    destroy the thirteen cases already scored.

    `earned` is outcome AND trajectory, and the word is chosen. A right answer
    reached without doing the work is not earned; Lab 8 watched one happen.
    """
    traj_ok, missing, extra = score_trajectory(result, case.get("expected_tools"))
    note = ""
    actual = None                                 # set below; see Score.actual
    judged = True
    try:
        actual = judge(case["question"], result.answer or "",
                       case.get("expected_facts"), vocabulary=vocabulary, **kwargs)
    except Exception as exc:                      # noqa: BLE001 - see docstring
        outcome, judged = False, False
        note = f"judge error: {type(exc).__name__}: {exc}"
    else:
        # OUTSIDE the try, on purpose (2026-09-27). compare_facts() is OUR
        # code, not the judge: a bug in it used to be caught above and filed
        # as "judge error", which would now make it look like a flaky judge
        # (exit 2, re-run it) instead of a bug. Only the judge call is the
        # instrument whose failure means "unmeasured".
        outcome = compare_facts(case.get("expected_facts"), actual)

    return Score(case_id=case["id"], outcome=outcome, trajectory=traj_ok,
                 earned=outcome and traj_ok, missing=missing, extra=extra,
                 note=note, actual=actual, judged=judged)



def render_candidate(facts: dict[str, Any] | None) -> str:
    """Turn a hand-labelled fact dict back into the prose a judge must read.

    The calibration pairs store DICTS, and a dict comparison is a rule - which
    is why the comparator scored 8/8 on them and proved nothing about a judge.
    To calibrate the judge we need the pairs as TEXT, so we render them.

    THE CAVEAT, and it is load-bearing: this renderer writes cleaner prose than
    a real agent does. No hedging, no "looks like", no three paragraphs of
    reasoning around the fact. So every number calibrate() produces is an
    UPPER BOUND on the judge's real-world accuracy. Quote it that way or do not
    quote it.

    A pair carrying `candidate_text` never reaches this function - calibrate()
    hands the judge that text verbatim. That is how the upper bound gets a
    real sentence under it (P04 scorer defect 1 part 2): the judge passed
    "there is no such service" and failed the agent's own words.
    """
    if facts is None:
        return ("I checked and there is no such service on this host, so I "
                "cannot report a status for it.")
    if not facts:
        return "I was unable to determine anything."

    parts: list[str] = []
    for key, value in facts.items():
        if isinstance(value, list):
            listed = ", ".join(value) if value else "none"
            parts.append(f"The services that are {key}: {listed}.")
        else:
            parts.append(f"The {key} service is {value}.")
    return " ".join(parts)


def calibrate(cases: list[dict[str, Any]], pairs: list[dict[str, Any]], *,
              judge: Any = extract_facts,
              vocabulary: dict[str, list[str]] | None = None,
              **kwargs: Any) -> dict[str, Any]:
    """Measure the JUDGE against the human labels. Two error rates, never one.

    TWO DENOMINATORS, the same ruling as Lab 8's report(): a false-pass rate is
    a fraction of the pairs a human labelled INCORRECT, and a false-fail rate a
    fraction of those labelled CORRECT. Dividing both by the total buries the
    rare-but-fatal error inside the common one.

    false_pass - judge said the answer was right, the human said it was wrong.
                 This is the dangerous one. A judge with a false-pass rate
                 ships broken agents while the dashboard stays green.
    false_fail - judge said wrong, the human said right. Expensive, not fatal:
                 it sends you hunting a bug that is not there.
    """
    # THE SIGNATURE WAS LYING, and splitting the file is what exposed it. This
    # function always needed two things - the labelled pairs, and the cases they
    # are labelled AGAINST - and took one argument, because both happened to
    # live in the same file and it could filter one out of the other. The filter
    # it used was `category == "judge-calibration"`, a scorer-specific string,
    # which is exactly the leak the spine/detail split removed from the record
    # one step earlier. Now they are two arguments, and `cases` is used for
    # exactly what its name says.
    by_id = {c["id"]: c for c in cases}

    agree = fp = fn = 0
    n_correct = n_incorrect = 0
    misses: list[dict[str, Any]] = []

    for pair in pairs:
        target = by_id[pair["calibration_of"]]
        expected = target["expected_facts"]
        # VERBATIM beats RENDERED. A pair with `candidate_text` is a real agent
        # sentence; its `candidate_facts` is still the human's reading of it,
        # which is what the comparator section checks. Either way `source` is
        # recorded, so a miss says which kind of prose the judge got wrong.
        if "candidate_text" in pair:
            prose, source = pair["candidate_text"], "verbatim"
        else:
            prose, source = render_candidate(pair["candidate_facts"]), "rendered"
        truth = (pair["label"] == "correct")
        n_correct += truth
        n_incorrect += not truth

        try:
            extracted = judge(pair["question"], prose, expected,
                              vocabulary=vocabulary, **kwargs)
            verdict = compare_facts(expected, extracted)
            err = ""
        except Exception as exc:                  # noqa: BLE001
            # A crashed judge is NOT a verdict. Counted as a miss and named.
            extracted, verdict, err = None, False, f"{type(exc).__name__}: {exc}"

        if verdict == truth:
            agree += 1
        else:
            kind = "FALSE PASS" if verdict else "FALSE FAIL"
            fp += verdict
            fn += not verdict
            misses.append({"id": pair["id"], "kind": kind, "source": source,
                           "prose": prose,
                           "extracted": extracted, "expected": expected,
                           "error": err})

    return {
        "n": len(pairs),
        "agreement": agree / len(pairs) if pairs else 0.0,
        "false_pass": fp,
        "false_pass_rate": fp / n_incorrect if n_incorrect else 0.0,
        "false_fail": fn,
        "false_fail_rate": fn / n_correct if n_correct else 0.0,
        "misses": misses,
    }


# --- smoke test -----------------------------------------------------------
# A test, not a demo. Everything below either asserts or gates, and the exit
# code is the contract - Lab 0 set that rule and it has held for ten labs.
#
# The whole file must pass with OLLAMA DOWN. That is what `judge=` buys: the
# scorer is exercised end to end against scripted judges, so a broken bench
# can never be mistaken for broken code. `JUDGE=1` adds the live-model
# sections on top.

if __name__ == "__main__":
    import os
    import sys

    from agent import AgentResult, Step

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    cases = load_suite()
    by_id = {c["id"]: c for c in cases}
    pairs = load_calibration()
    VOCAB = load_vocabulary()
    print(f"suite: {SUITE.name}  cases: {len(cases)}  "
          f"calibration pairs: {len(pairs)} ({FIXTURES.name})")

    # The integrity check the split makes necessary: every pair names a case id
    # that still exists. While they shared a file, moving a case and forgetting
    # its pairs was impossible. Now it is possible, so it is checked - and it is
    # checked HERE, before any measurement, because a KeyError three sections
    # later would arrive disguised as a judge failure.
    orphans = sorted(p["id"] for p in pairs if p["calibration_of"] not in by_id)
    check("every calibration pair names a live case id", orphans == [],
          f"orphans: {orphans}" if orphans else "")

    # ---- 1. the comparator, against the hand-labelled pairs -----------------
    # Exact, not a threshold. This layer has no model in it, so anything less
    # than every case is a logic bug, not variance.
    print("\n[1] fact comparator vs human labels")
    agree = 0
    for pair in pairs:
        target = by_id[pair["calibration_of"]]
        verdict = compare_facts(target["expected_facts"], pair["candidate_facts"])
        agree += (verdict == (pair["label"] == "correct"))
    check(f"comparator {agree}/{len(pairs)}", agree == len(pairs))

    # ---- 2. trajectory, against SCRIPTED runs -------------------------------
    # Lab 8's `runner=` seam in miniature: we are testing the SCORER, so the
    # agent is a fixture. A scorer that needs a live model to be tested is a
    # scorer nobody runs.
    def fake(calls: list[tuple[str, dict[str, Any]]], answer: str,
             ok_flags: list[bool] | None = None) -> AgentResult:
        flags = ok_flags or [True] * len(calls)
        steps = [Step(n=i + 1, action="call", tool=t, arguments=a, ok=f)
                 for i, ((t, a), f) in enumerate(zip(calls, flags))]
        steps.append(Step(n=len(calls) + 1, action="final"))
        return AgentResult(ok=True, answer=answer, reason="final", steps=steps)

    four = [("get_service_status", {"name": n})
            for n in ("Spooler", "W32Time", "WinRM", "wuauserv")]
    honest = fake(four, "Spooler and wuauserv are down.")
    shortcut = fake([("list_services", {})], "Spooler and wuauserv are down.")
    refused = fake([("get_service_status", {"name": "Fax"})],
                   "There is no Fax service on this host.", ok_flags=[False])

    print("\n[2] trajectory scoring")
    ok, missing, _ = score_trajectory(honest, by_id["aggregate-down"]["expected_tools"])
    check("honest run scores PASS", ok and not missing)

    ok, missing, _ = score_trajectory(shortcut, by_id["aggregate-down"]["expected_tools"])
    check("the Lab 8 shortcut scores FAIL", (not ok) and len(missing) == 4,
          f"missing={len(missing)}")

    # THE RULING WORTH A TEST OF ITS OWN: the required call FAILED, and that
    # failure is the honest work the refusal rests on. If this ever goes red,
    # someone has "fixed" score_trajectory to require ok=True.
    ok, _, _ = score_trajectory(refused, by_id["negative-unknown-service"]["expected_tools"])
    check("an ATTEMPTED call that errored still counts", ok)

    # ---- 3. score_case end to end, with no model ---------------------------
    print("\n[3] score_case with scripted judges (no model)")

    def judge_ok(_q: str, _a: str, expected: dict[str, Any] | None,
                 **_k: Any) -> dict[str, Any] | None:
        return expected                      # a perfect extractor

    def judge_partial(_q: str, _a: str, _e: dict[str, Any] | None,
                      **_k: Any) -> dict[str, Any] | None:
        return {"down": ["Spooler"]}         # the incomplete aggregate

    def judge_boom(*_a: Any, **_k: Any) -> dict[str, Any] | None:
        raise TimeoutError("judge took too long")

    sc = score_case(by_id["aggregate-down"], honest, judge=judge_ok)
    check("perfect judge + honest run -> earned", sc.earned)

    sc = score_case(by_id["aggregate-down"], honest, judge=judge_partial)
    check("incomplete answer -> outcome False, trajectory True",
          (not sc.outcome) and sc.trajectory)

    sc = score_case(by_id["aggregate-down"], shortcut, judge=judge_ok)
    check("right answer, work not done -> NOT earned",
          sc.outcome and (not sc.trajectory) and (not sc.earned))

    # Failure as data, fourth application: ToolResult (L3), AgentResult (L4),
    # attempt() (L8), and now a judge that dies mid-suite.
    sc = score_case(by_id["aggregate-down"], honest, judge=judge_boom)
    check("a crashing judge is recorded, not raised",
          (not sc.outcome) and sc.note.startswith("judge error"), sc.note)

    # ---- 4. the live judge -------------------------------------------------
    #     JUDGE=1 python src/score.py
    if os.environ.get("JUDGE"):
        print("\n[4] judge: prose -> facts (model in the loop)")
        prose = [
            ("aggregate-down", four,
             "I checked all four services. Spooler and wuauserv are both "
             "stopped; W32Time and WinRM are running.", True),
            ("aggregate-down", four, "Looks like the print spooler is down.", False),
            ("negative-unknown-service",
             [("get_service_status", {"name": "Fax"})],
             "There is no service called Fax on this host, so I cannot report "
             "a status for it.", True),
            ("status-spooler", [("get_service_status", {"name": "Spooler"})],
             "The print spooler is currently stopped.", True),
        ]
        for case_id, calls, answer, want in prose:
            sc = score_case(by_id[case_id], fake(calls, answer), vocabulary=VOCAB)
            check(f"{case_id}: {answer[:44]}...", sc.earned == want,
                  f"earned={sc.earned} expected={want}"
                  + (f" {sc.note}" if sc.note else ""))

        # ---- 5. judging the judge, and the gate ----------------------------
        print("\n[5] judge calibration")
        cal = calibrate(cases, pairs, vocabulary=VOCAB)
        print(f"  pairs      : {cal['n']}")
        print(f"  agreement  : {cal['agreement']:.2f}")
        print(f"  false PASS : {cal['false_pass']} "
              f"({cal['false_pass_rate']:.2f} of answers a human called wrong)")
        print(f"  false FAIL : {cal['false_fail']} "
              f"({cal['false_fail_rate']:.2f} of answers a human called right)")
        for m in cal["misses"]:
            print(f"    {m['kind']}: {m['id']}")
            print(f"      prose    : {m['prose']}")
            print(f"      expected : {m['expected']}")
            print(f"      extracted: {m['extracted']}")
            if m["error"]:
                print(f"      error    : {m['error']}")

        # TWO GATES, DELIBERATELY ASYMMETRIC.
        #
        # A false PASS is a judge blessing a wrong answer: the scoreboard goes
        # green while the agent is broken, which is the failure this whole file
        # exists to prevent. It gets a HARD ZERO - no budget, no rate.
        #
        # A false FAIL costs an engineer an afternoon chasing a bug that is not
        # there. Expensive, survivable, and it gets a threshold.
        #
        # AGREEMENT alone is NOT a gate. A judge that called everything correct
        # would score 0.50 here and be worthless; one calibrated on a set of
        # all-correct answers would score 1.00 and be worthless. The rates are
        # the measurement; agreement is the headline.
        check("no false passes", cal["false_pass"] == 0,
              f"false_pass={cal['false_pass']}")
        check("agreement >= 0.90", cal["agreement"] >= 0.90,
              f"agreement={cal['agreement']:.2f}")
    else:
        print("\n[4-5] live-judge sections SKIPPED  (JUDGE=1 to run them)")

    print()
    if failures:
        print(f"SMOKE TEST FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("SMOKE TEST PASSED")
    sys.exit(0)
