"""Measuring how much an agent varies from run to run.

Lab 1 pinned temperature=0.0 and a fixed seed so the MODEL CALL would be
deterministic. This file asks the next question: given a deterministic model
call, is the AGENT deterministic? They are not the same claim, and the answer
decides what a test on this system is even allowed to assert.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable

from agent import AgentResult, run


@dataclass(frozen=True)
class Trial:
    """One agent run, flattened to only what is worth comparing."""

    ok: bool                        # did it finish, or run out of budget
    answer: str                     # the final text, "" if there wasn't one
    reason: str                     # "final" or "budget"
    n_steps: int                    # how many loop turns it actually took
    trajectory: tuple[str, ...]     # tool names called, in order


def trial_of(result: AgentResult) -> Trial:
    """Flatten an AgentResult down to the fields worth comparing."""
    return Trial(
        ok=result.ok,
        answer=result.answer or "",
        reason=result.reason,
        n_steps=len(result.steps),
        trajectory=tuple(s.tool for s in result.steps if s.tool),
    )


def attempt(question: str, *,
            runner: Callable[..., AgentResult] = run,
            **kwargs: Any) -> Trial:
    """Run once, and turn a crash into a Trial instead of an exception.

    A harness that raises on the first failed run cannot measure reliability,
    because the failures ARE the data - and one timeout must not destroy the
    nineteen runs that already succeeded. Same ruling as ToolResult in Lab 3
    and AgentResult in Lab 4: report failure, do not raise it.
    """
    try:
        return trial_of(runner(question, **kwargs))
    except Exception as exc:
        return Trial(ok=False, answer="",
                     reason=f"error: {type(exc).__name__}",
                     n_steps=0, trajectory=())


def repeat(question: str, n: int = 5, *,
           runner: Callable[..., AgentResult] = run,
           **kwargs: Any) -> list[Trial]:
    """Ask the same question n times; return one Trial per run.

    `runner` is a SEAM. It defaults to the real agent, but a test can pass a
    stub and prove the counting without a model, a network, or a wait. Lab 7
    used the same trick for `reviewer=`.
    """
    return [attempt(question, runner=runner, **kwargs) for _ in range(n)]


def report(trials: list[Trial]) -> dict[str, Any]:
    """Reduce a set of trials to the numbers that decide what a test may assert.

    Agreement is measured against the MODE, not against trials[0]. The first
    run is an arbitrary choice of reference; if it was the odd one out, every
    number after it is wrong. The plurality has no such bias.
    """
    if not trials:
        raise ValueError("no trials to report on")

    n = len(trials)
    good = [t for t in trials if t.ok]

    out: dict[str, Any] = {
        "n": n,                          # runs attempted
        "n_scored": len(good),           # runs that produced an answer
        "ok_rate": len(good) / n,        # reliability, over ALL attempts
    }

    # Agreement is about variation in SUCCESSFUL behaviour. A run that
    # crashed took no path and said nothing; counting its empty trajectory
    # as "a different path" inflates the variation with infrastructure noise.
    # Reliability and variability are two questions and take two denominators.
    if not good:
        out.update(distinct_answers=0, answer_agreement=0.0,
                   distinct_paths=0, path_agreement=0.0,
                   steps_min=0, steps_max=0)
        return out

    answers = [t.answer for t in good]
    paths = [t.trajectory for t in good]
    steps = [t.n_steps for t in good]
    g = len(good)

    out.update(
        distinct_answers=len(set(answers)),
        answer_agreement=Counter(answers).most_common(1)[0][1] / g,
        distinct_paths=len(set(paths)),
        path_agreement=Counter(paths).most_common(1)[0][1] / g,
        steps_min=min(steps),
        steps_max=max(steps),
    )
    return out


def across(questions: list[str], *,
           runner: Callable[..., AgentResult] = run,
           **kwargs: Any) -> list[Trial]:
    """Run each question ONCE. Same intent, different wording.

    `repeat` holds the input fixed and asks whether the machine wobbles.
    `across` holds the MEANING fixed and varies the wording, which is the
    axis production actually moves along - nobody asks the same question
    twice with the same words. Both hand back Trials, so `report` scores
    either without knowing which it was given.
    """
    return [attempt(q, runner=runner, **kwargs) for q in questions]


if __name__ == "__main__":
    from agent import Step

    def canned(answer: str, tools: list[str]) -> AgentResult:
        """Build a finished run by hand, so the arithmetic can be checked by hand."""
        steps = [Step(n=i, action="tool", tool=t) for i, t in enumerate(tools, 1)]
        steps.append(Step(n=len(tools) + 1, action="final"))
        return AgentResult(ok=True, answer=answer, reason="final", steps=steps)

    PATH_A = ["list_services", "service_status"]
    PATH_B = ["service_status", "list_services", "service_status"]

    SCRIPT = iter([
        canned("nginx is running", PATH_A),
        canned("nginx is running", PATH_A),
        canned("nginx is running", PATH_B),
        canned("nginx appears to be active", PATH_A),
    ])

    def stub(_question: str, **_kwargs: Any) -> AgentResult:
        return next(SCRIPT)

    r = report(repeat("is nginx running?", 4, runner=stub))
    for key, value in r.items():
        print(f"  {key:20} {value}")

    assert r["n"] == 4, r
    assert r["ok_rate"] == 1.0, r
    assert r["distinct_answers"] == 2, r
    assert r["answer_agreement"] == 0.75, r
    assert r["distinct_paths"] == 2, r
    assert r["path_agreement"] == 0.75, r
    assert r["steps_min"] == 3 and r["steps_max"] == 4, r

    pair = across(["a?", "b?"], runner=lambda q, **kw: canned("same", PATH_A))
    assert [t.answer for t in pair] == ["same", "same"], pair
    assert report(pair)["n"] == 2, pair

    def boom(_question: str, **_kwargs: Any) -> AgentResult:
        raise RuntimeError("model unreachable")

    dead = repeat("anything?", 2, runner=boom)
    assert [t.ok for t in dead] == [False, False], dead
    assert dead[0].reason.startswith("error:"), dead
    assert report(dead)["ok_rate"] == 0.0, dead

    print("PASS  variance: 8 scripted runs measured, no model involved")
