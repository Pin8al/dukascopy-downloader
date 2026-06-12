---
name: the-conviction
description: Deterministic bug-finding and bug-killing protocol. Replaces assumption-patching ("this is probably it" → edit → hope) with an evidence-gated kill chain — reproduce → observe → falsifiable hypotheses → discriminating experiments → conviction → minimal fix → verified kill. No edit until the root cause is OBSERVED, not inferred; a patch requires a conviction. AUTO-FIRES on any defect turn: bug, error, exception, crash, stack trace, traceback, regression, flaky test, hang, wrong output, "doesn't work", "broken", "why is it doing X", "fix this". Lane-routed — mechanical errors whose cause is on the face of the trace take the fast lane (repro → fix → verify); unclear causes take the full kill chain; non-reproducible / prod-only bugs take forensics mode. PARALLEL-COMPATIBLE — when birdseye or the-inverse co-fire, they run first (birdseye classifies the turn, this skill owns the kill; the-inverse's framing is inherited, never re-derived). Do NOT fire on feature requests, refactors, style changes, or how-does-this-work questions with no defect in play.
---

# the-conviction

> **No conviction, no cut. The bug dies when the evidence says it's dead — not when the patch looks plausible.**

The default model behaviour on a bug is assumption-patching: skim the code, form the *first plausible story*, edit where the story points, declare victory. Sometimes the story is right. Whether it's right is a coin flip — and a coin flip is exactly what "non-deterministic debugging" means. This skill removes the coin. Determinism doesn't come from being smarter; it comes from a procedure whose gates can't be passed by assertion — only by artifacts: a failing reproduction, a written prediction, an observed value, a toggle proof.

**The core inversion: the unit of progress is an observation, not an idea.** An idea ("it's probably the cache") costs nothing and proves nothing. An observation ("the cache returns `None` at line 142 on the second call — here's the log line") is a fact the bug cannot argue with. Every gate below exists to convert ideas into observations before any edit happens.

---

## The governing law

**No edit to non-test code until the root cause has been observed — not inferred, not pattern-matched, not "almost certainly". A patch written before evidence is a guess wearing a fix's costume.**

Corollary: if you notice you are about to type an edit and you cannot point at the observation that convicts the cause, stop. You are at the wrong gate. Go back.

The law has exactly two relaxations, both explicit and labeled:
- **Fast lane** (below) — when the cause is on the face of the error, the hypothesis machinery collapses, but reproduce-first and verify-after never do.
- **Probe-patch** (forensics mode) — when reproduction is impossible, a best-evidence patch may ship, but only labeled as a probe with a named signal that will confirm or refute it.

---

## This is a procedure, not a vibe

Re-read this file each time the skill fires on a full-lane bug. Do not work from memory of it. Field truth from the sibling skills applies here doubly: models working from impression skip gates, and the gate most often skipped is exactly the one that would have caught the wrong assumption. Three drifts to catch mid-flight:
1. **Reading code instead of running code** — twenty minutes of staring at source, zero observations collected. Code-reading generates hypotheses; only execution kills them.
2. **The single-story lock-in** — one hypothesis formed early, all subsequent evidence interpreted to fit it. The two-hypothesis minimum (Gate 2) exists to break this.
3. **Ledger theatre** — filling in the evidence ledger *after* choosing the patch, to justify it. Same fraud as a birdseye block written after the decision. The ledger is written forward, gate by gate, or it is fake.

---

## The unfakeability core — the evidence ledger

Every full-lane hunt produces a visible ledger, built **forward in time** as the gates are passed. This is the artifact that makes the protocol deterministic: a model can ignore prose rules, but a required artifact that's missing is visible to everyone.

```
SYMPTOM    : <verbatim error / observed wrong behaviour — quoted, not paraphrased>
REPRO      : <exact command> → <exact failure output>
H1         : <candidate cause> | test: <experiment> | predicts: <specific outcome>
H2         : <candidate cause> | test: <experiment> | predicts: <specific outcome>
EXP 1      : changed <one thing> | predicted <X> | observed <Y> | verdict: <H1 dead / H2 alive / new H3>
EXP 2      : ...
ROOT CAUSE : <causal chain from trigger to symptom — every link observed, none assumed>
TOGGLE     : cause present → fails with <output> | cause removed → passes with <output>
FIX        : <file:line, one sentence, at the root-cause layer>
VERIFY     : repro rerun → pass | tests → <result> | siblings → <locations checked, result>
```

**Ledger rules:**
- Predictions are written **before** the experiment runs. A prediction added after seeing the result is not a prediction; it's a caption.
- A wrong prediction is *information, never embarrassment* — it just killed a hypothesis for free. Record it and update. Silently dropping a falsified hypothesis and pivoting without noting it is how lock-in survives.
- For hunts longer than ~3 experiments or spanning sessions, persist the ledger to `<project-root>/.cursor/debug-ledgers/<bug-slug>.md` so compaction can't eat the kill history. Resume from the ledger, not from memory of the hunt.
- Short ledgers are fine. A two-line ledger on a simple bug is honest; a ten-line ledger padded for ceremony is theatre. The compression law from birdseye governs here too: the ledger must be smaller than the confusion it removes.

---

## Lane selection (10 seconds, before anything)

| Lane | When | Protocol |
|---|---|---|
| **Fast** | The error names the exact cause and the fix is mechanical: typo, missing import, undefined name, wrong path, obvious off-by-one named in the trace | Reproduce → fix → re-run repro. One-line ledger. |
| **Full** | Cause not on the face of the error; behaviour wrong with no error; error far from its origin; "it worked yesterday" | The full kill chain, Gates 0–6. |
| **Forensics** | Cannot reproduce locally: prod-only, race, heisenbug, "happens sometimes" | Forensics mode (below). |

**Lane honesty check:** the fast lane is for when the cause is *observed in the trace*, not for when you have a *strong feeling*. "I'm pretty sure it's X" is full-lane by definition — the feeling is H1, and H1 needs a test. If a fast-lane fix doesn't make the repro pass on the first try, you mis-laned: stop, escalate to full lane, start the ledger. A second blind patch is forbidden.

---

## The kill chain (full lane)

### Gate 0 — Reproduce before you read code

Produce a command that fails deterministically, as minimal as practical: a failing test, a script, a curl, a CLI invocation. Capture the exact command and verbatim output into the ledger.

- No reproduction → you cannot know your fix works. Everything downstream of this gate depends on it.
- Shrink it: smallest input, fewest moving parts, fastest cycle time. Every second of repro latency multiplies across every experiment you'll run.
- Genuinely can't reproduce → branch to forensics mode. Never "skip ahead" to patching.

### Gate 1 — Observe the actual failure

Read the **entire** error — full stack trace, not the last line. Open the actual file at the actual failing line at the actual revision. Never debug from memory of what the code says; memory of code is an assumption with good posture.

Then collect the first real observations: log or print the actual runtime values at the failure boundary. What is the value that's wrong? Not "the data is probably malformed" — *which field, what value, expected what*. Quote evidence verbatim in the ledger; paraphrased evidence is pre-laundered to fit the story you already have.

### Gate 2 — The hypothesis ledger: two or more, falsifiable, with predictions

Write **at least two** candidate causes. The single-hypothesis trap is where assumption-patching begins — with one story, every observation gets bent to fit it; with two, observations get to choose.

Each hypothesis must carry:
- a **discriminating test** — an experiment whose outcome differs depending on which hypothesis is true, and
- a **written prediction** — "if H1, this experiment shows X; if H2, it shows Y."

A hypothesis with no test that could kill it is not a hypothesis; it's a mood. If you genuinely can only think of one cause, the mandatory second is: **"the bug is upstream — this code is receiving bad input."** Test the boundary: is the data already wrong when it arrives here? Half of all "mystery" bugs die at this question, because the model was staring at the crime scene instead of the cause.

### Gate 3 — Discriminating experiments: one change per run

Run the tests. The iron rule: **each experiment changes exactly one variable.** Change two things and the result is uninterpretable — you've spent a cycle and bought nothing, and worse, you may have *masked* the bug instead of finding it.

Narrow by halving, not by wandering:
- **Bisect time:** `git bisect` between last-known-good and first-known-bad. Mechanical, deterministic, embarrassingly effective — use it whenever "it worked before" is true.
- **Bisect space:** probe the data at layer boundaries. Is the value already wrong at the API response? After parsing? After the transform? Each probe halves the suspect region. This beats reading all the code in between, every time.
- **Bisect input:** cut the failing input in half until the minimal failing case is found. The minimal case usually *names* the cause.

After every experiment, write the verdict line: which hypothesis died, which survived, what new one emerged. Suspect space must shrink measurably each cycle; three experiments with no shrinkage means the hypotheses are wrong in kind — step back to Gate 2 and generate different ones (this is where the-inverse's question helps — see Synergy).

### Gate 4 — Conviction

The root cause is convicted when **both** hold:
1. **The causal chain is complete:** you can narrate trigger → mechanism → symptom, and every link in the chain is something you *observed* in an experiment — not something that "must be happening".
2. **The toggle proof:** you can make the bug appear and disappear by flipping the suspected cause — apply the bad input and it fails, remove it and it passes; check out the bad commit and it fails, the parent and it passes. **If toggling the cause doesn't toggle the symptom, it is not the cause** — no matter how plausible the story. Back to Gate 2.

This gate is the whole skill. Everything before it feeds it; nothing after it is allowed without it.

### Gate 5 — The kill

The fix is **minimal and at the root-cause layer.** Fixing where the error *surfaced* instead of where it *originated* is symptom-patching — the bug retreats one layer and waits.

- No drive-by refactors, no "while we're here" cleanups riding in the same change. They contaminate the verification: if Gate 6 fails, you won't know which change broke it.
- If a true root-cause fix is out of scope (third-party bug, architectural) and you must patch at the symptom layer, that's sometimes the right call — but say so in plain words: *"this is a workaround at the symptom layer; root cause is X in Y."* A workaround labeled as a fix is a lie with a delay timer.

### Gate 6 — Verify the kill

Non-negotiable, including in the fast lane:
1. **Rerun the exact Gate-0 reproduction.** It must pass. "It should pass now" is a banned phrase (below) — run it.
2. **Run the neighbouring tests** — the file's suite, the module's suite, whatever exists. The fix must not pay for one bug with another.
3. **Sibling sweep:** grep for the same pattern elsewhere. A bug convicted as "this call site forgot to handle the empty case" has brothers at every other call site — find them now, while the cause is loaded in context, or refind them one incident at a time.
4. If a regression test doesn't exist for this bug, write one — the Gate-0 repro is usually 90% of it already. A killed bug with no tombstone test resurrects.

Report results plainly: what was run, what passed, what wasn't run and why.

---

## Banned-phrase tripwires

These phrases are legal in exactly one place — the hypothesis lines of the ledger. Appearing anywhere else (a conviction, a fix description, a commit message, a "done" report), they are a confession of a skipped gate:

> *probably · likely · should fix · I believe · I think the issue is · might be · seems like · this must be · almost certainly · should work now*

The mechanical rule for any model running this skill: **when one of these words is about to precede an edit, the edit is premature.** Convert the sentence into a hypothesis line, give it a test and a prediction, and run the test. The phrase isn't forbidden because it's impolite — it's forbidden because it is the exact linguistic signature of assumption-patching, and making it a tripwire converts the bad habit into a gate check.

---

## Forensics mode (can't reproduce)

For prod-only failures, races, heisenbugs, "happens sometimes":

1. **Collect the corpse:** logs, stack traces, env, versions, timestamps, the exact input if recoverable. Build the timeline of what is *known* vs *assumed* — keep the two lists separate in the ledger.
2. **Instrument, don't guess:** add structured probes (log lines with values, not "got here") at the suspected boundaries and let the bug come to you. A probe that fires once is worth ten theories.
3. **Recreate the conditions deliberately:** races → add forced delays/interleavings to make the window deterministic; load-dependent → reproduce the load. A heisenbug made deterministic is just a bug — return to the full lane.
4. **Probe-patch (last resort):** if a patch must ship on best evidence, label it a **probe-patch** and write down, in advance, the signal that will confirm or refute it ("if H1 was right, error rate for X goes to zero within a day; if it persists, H1 is dead and we pull the patch"). A probe-patch without a named signal is just assumption-patching with paperwork.
5. **Never** "fix" a race with `sleep()`, a flaky test with a retry loop, or an exception with a bare `try/except pass` — these don't kill bugs, they bribe them to testify less often.

---

## Synergy — lanes with the sibling skills (mandatory, no overlap)

Skill order when stacked: **the-inverse → the-prescription → birdseye → the-conviction.** Each fires on its own trigger; none is a prerequisite. This skill can and usually does fire alone — most bug turns need no pre-flight, just the kill chain.

- **birdseye-vision** owns turn classification. Its tiebreaker already routes bugs: clear cause → doesn't apply, unclear cause → Type C. When birdseye fires on a bug turn, its block chooses *whether and at what depth* to engage; the-conviction owns the kill chain itself, and **the evidence ledger serves as the Process section** — never write both. No B/C/D re-derivation here; lanes (fast/full/forensics) are this skill's internal routing, not a competing classification.
- **the-inverse** is the hypothesis-generator of last resort. When Gate 3 stalls (three experiments, no shrinkage), the debugging-native inversions are: *"what would have to be true for this code to work?"* — enumerate the preconditions, check each one as an observation; and *"how would I deliberately write code that produces exactly this symptom?"* — the answer is a hypothesis list. If the-inverse fired this turn and named the load-bearing question, inherit it as H1 — never re-derive.
- **the-prescription** owns the channel switch. If the *user* repeatedly doesn't follow what the bug is, the cure is a trace diagram or an annotated path through the failure — that's a prescription vehicle, hand it off. This skill convicts bugs; it doesn't teach.
- **grill-me:** when the user arrives with a pre-chosen fix and asks to ship it, the ledger is the grill — does their fix come with a conviction? If not, run Gates 0–4 against their hypothesis before cutting.

**Stance memory:** a multi-session hunt saves a Stance via birdseye's mechanism with the ledger path in the body, so the next session resumes from evidence, not from a summary's impression of the evidence.

---

## Failure modes (predictive guardrails — also the pre-ship check)

| # | Failure | Guardrail |
|---|---|---|
| 1 | **Assumption-patching** — first plausible story → edit | Governing law; no edit without an observed cause |
| 2 | **Shotgun debugging** — change several things, see if it helps | One change per experiment; uninterpretable results are wasted cycles |
| 3 | **Patch-and-pray** — "should work now", no rerun | Gate 6 step 1 is mandatory in every lane |
| 4 | **Symptom fix** — patch where it surfaced, not where it originated | Gate 5; workarounds must be labeled as workarounds |
| 5 | **Single-story lock-in** — evidence bent to fit hypothesis #1 | Two-hypothesis minimum; verdict line after every experiment |
| 6 | **Ledger theatre** — ledger written backwards to justify a chosen patch | Predictions before runs; forward-only construction |
| 7 | **Debugging from memory of the code** | Gate 1: open the real file at the real revision |
| 8 | **Paraphrased evidence** — "the data looks malformed" | Quote values verbatim; name field, value, expected |
| 9 | **Reading instead of running** — hypothesis hoarding, zero observations | Unit of progress is an observation; get to Gate 3 fast |
| 10 | **Deleting/weakening the failing test** to make the suite green | The test is the repro; killing the witness is not killing the bug |
| 11 | **Band-aids** — sleep() for races, retries for flakes, bare except | Forensics rule 5; bribed bugs return with interest |
| 12 | **Toggle skipped** — convicted on plausibility | Gate 4: no toggle proof, no conviction |
| 13 | **Fixing a different bug found en route** and claiming the kill | Rerun the original Gate-0 repro; new bugs get their own ledger |
| 14 | **Fast-lane abuse** — "I'm pretty sure" treated as "observed in trace" | Lane honesty check; one failed blind fix forces full lane |
| 15 | **No sibling sweep** — same bug pattern left alive elsewhere | Gate 6 step 3 while the cause is loaded in context |
| 16 | **No tombstone test** — killed bug free to resurrect | Gate 6 step 4; the repro is already 90% of the test |
| 17 | **Ceremony inflation** — ten-line ledger for a typo | Lane selection; compression law governs the ledger too |

---

## Self-audit before declaring a bug dead

1. Does a reproduction exist, and was it rerun **after** the fix — actually rerun, not "would pass"?
2. Can I narrate the causal chain trigger → mechanism → symptom with every link pointing at an observation in the ledger?
3. Did the toggle proof run — cause flipped, symptom flipped with it?
4. Is the fix at the root-cause layer, or is it a labeled workaround? (Unlabeled workaround = fail.)
5. Were predictions written before their experiments? Any banned phrase in the conviction, fix description, or report?
6. Siblings swept? Tombstone test written? Neighbouring tests run?
7. If forensics: is every shipped patch labeled a probe-patch with a named confirm/refute signal?
8. Lane honesty: if this started fast-lane and the first fix missed, did it escalate to full lane instead of a second blind patch?

If any answer is no, the bug is not dead — it's hiding. Go back to the gate that failed.

## Tagline

> Reproduce it, observe it, accuse it twice, make the evidence pick, toggle the proof — and only then cut. The patch is the verdict; the verdict requires the conviction.
