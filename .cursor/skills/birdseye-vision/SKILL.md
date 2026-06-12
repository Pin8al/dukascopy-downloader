---
name: birdseye-vision
description: Strategic pre-flight scan before non-trivial work. Classifies the turn → routes to the right depth → forces the thinking (vision, rejected path, blast radius, stance) BEFORE the path is locked, not after → then embodies the operator the work needs (Step 1.6) as a self-installing brief before executing. Adversary-first block so the challenge precedes the choice. Action-delta law — no block, and no operator, unless it would change the move. Reads and writes stance memory so decisions survive compaction. Pairs after the-inverse; runs before domain skills. Mastering the process masters the outcome.
type: process
---

# birdseye-vision

> **Mastering the process masters the outcome.**

Scan before non-trivial turns. The scan is 2 seconds — invisible infrastructure, not a ritual. Classify, route to depth, force the thinking, act. Never action-first on multi-path work.

**Objectivity mandate.** Birdseye is analysis, not validation. If the plan has a flaw, name it. If the obvious path is wrong, say so. Agreeableness is the anti-pattern — the operator needs clear thinking, not agreement.

---

## The unfakeability law — non-dismissible, top of stack

A birdseye block is real only if it could have **changed the move**. Everything below serves this one test. A block that dresses up a decision already made is theatre, and theatre is worse than no block — it teaches the operator the block is wallpaper.

**The action-delta gate.** Before writing any block, name the path you're rejecting. If the choice reads **"X over Y because Z"** — with a real Y you genuinely considered and dropped — the block has earned its place. If there is no Y, there was only ever one path: **skip the block, act, cite the stance in one line.** A block with no rejected path is theatre by definition.

**Adversary before commitment.** The block is ordered so the attack lands *before* the choice is locked: **Devil's Advocate → Comeback → then Chosen Path.** You cannot honestly attack a path you've already crowned two lines above; you can attack one you haven't committed to yet — and let it kill the path. Writing the DA first is what makes it bite. If no Comeback holds, the path was wrong — change it before you write the Chosen Path line.

**Theatre markers — if any fire, the block was fake. Redo it or drop it:**
- Chosen Path with no named rejected path (`X over Y because Z` fails)
- Devil's Advocate written *after* the path was chosen, attacking a strawman
- Vision that paraphrases the request ("operator wants X") instead of naming the deeper goal
- Vision written as a 1–3-year essay on a tactical turn — manufactured grandiosity is also theatre
- A "Pre-flight" / "Birdseye:" header followed by tool calls within two lines on a C/D turn
- Skipping the scan because another skill fired (they run AFTER birdseye, not instead)
- An Operator Brief (Step 1.6) written as third-person description, or carrying a Tell that prevents no real mistake — costume, not capability

When the operator calls "half-arse": stop, surface the missed block in full, then continue. Don't argue. Don't minimise.

---

## The compression law — proportionality governor

Birdseye must create **less text than the work it improves.** The scan is a blade, not a cockpit. If the block would take more room than the next useful action, collapse it to four lines — **Vision / Risk / Chosen Path / Next move** — and act. Process that costs more than the work it saves is the exact inflation this skill exists to prevent. When in doubt, ship the smaller block. This law governs every section below, the Operator Brief included.

---

## When birdseye fires — and when it doesn't

**Default bias: does not fire.** Birdseye is for strategic process selection, not a tax on every turn. Most messages never invoke it.

**Does not apply at all** — no block, no header, no Type tag, just act:
- **Pure execution:** one obvious action, no decision. Typo, rename, version bump, "change X to Y", confirmed bug with known cause, formatting, file move, factual lookup.
- **Continuation** ("keep going", "next", "continue") with no new info → inherit the prior classification if one was active; reset every 3 turns so it can't compound.
- **Mid-execution tool-result turn** with no new user message → finish the work, don't re-classify.
- **Explicit override:** "just do it", "skip the thinking", "no birdseye" → does not apply.
- **The-inverse already landed the path.** If the-inverse fired this turn and named the load-bearing path, birdseye does NOT re-enumerate. Inherit its cutting inverse as the Chosen Path; add only Stance + Process. Never redo the path work the inverse already did.
- **The-prescription already chose the vehicle.** If the-prescription fired this turn and prescribed a non-trivial artifact or activity, birdseye does NOT re-decide *what* to build — take the vehicle as given and own only the *how* (operator, standard, process). Never re-litigate the cure.

**Tiebreakers:** unsure between "doesn't apply" and B → doesn't apply · unsure between B and C → C · bug with clear cause → doesn't apply, unclear cause → C.

---

## Step 0 — Classify (every non-trivial turn, 2 seconds)

| Type | What it is | Output |
|------|-----------|--------|
| **B** | Tactical — one clear path, small reversible decision, continuing confirmed work | One-liner → act |
| **C** | Strategic — multi-path, recommendation needed, new feature, bug with unclear cause | Compact block |
| **D** | Architectural / vision — cross-system, foundation-setting, hard-to-reverse, vision-shape language | Full block + stance + routing |

**The C↔D discriminator is stakes × reversibility — not a trigger count.** Counting how many trigger words appear is gameable. Ask instead: *if I pick wrong here, how much breaks and how hard is it to walk back?* A turn is **D** when getting it wrong is expensive *and* hard to undo — cross-system reach, sets a foundation others build on, or the operator has handed you the call on something shaped. Everything else strategic is **C**.

**Force to D** when vision-shape or autonomy language appears — "feel / vibe / elite / proper / holy grail / perfect / polished / world-class / the best", or "your call / you decide / figure it out / do whatever". That language is the operator telling you the decision is shaped, not mechanical.

**Force B → C** when the turn touches >2 files, introduces a new convention/folder/template, changes anything another agent or skill depends on, or edits a hook / settings.json.

---

## Step 0.5 — Drift sensors

The biggest failure is **silent drift** — the scan stops firing because the thread feels familiar.

**Re-scan (overrides "skip"):**
- **Stacked-AND** — "and on top of that…", "also let's", "while we're here". Each clause is its own classification.
- **Mood shift to vision-language** — a single "make it elite" / "holy grail" forces D even if prior turns were B.
- **New system introduced** — a skill, framework, integration, or architecture pattern, not just a new noun or file.

When one fires, surface a visible `**Re-scan:**` line with fresh classification before continuing.

**Cadence:** on a sustained thread, re-scan every 5 turns — silently, only surface if the classification changed. Continuation inheritance resets every 3 turns.

**Tier-inflation guard:** 3+ C/D turns in a row on one thread → recalibrate; most work is B. Exception: a genuine sustained-D thread stays D.

**Retroactive surface:** caught acting without scanning on a C/D turn? Surface the block in the next message before continuing — *"I drifted past the scan; here's the block I owe."*

**Stance lookup:** every C/D turn, check memory for an active `Stance:` matching the goal. If one exists, surface it under **Active Stance:** — and re-install its operator brief if it carried one: resume *in character* rather than re-deriving or drifting to generic. Freshness-check first — if the task has pivoted, re-derive the operator (Step 1.6).

---

## Step 0.6 — Active work-file resume (first turn of a session only)

Before starting new strategic work in a project, check for in-flight work-files:
1. Resolve root: `<cwd>/.planning/work-files/` if `.planning/` exists, else `<repo-root>/.work-files/`, else `<cwd>/.work-files/`.
2. List `active/`; for each, read `README.md` "Operator view" + the last line of `DONE.md`.

If any exist, surface before new work:

```
**Active work-files found:**
1. `<slug>` — <one-line status / next action>

Resume one, retire one, or start fresh?
```

Routing: request continues an active work-file → resume automatically (open the folder, continue from PROCESS.md's next step) · unrelated → mention briefly, proceed · stale (no `DONE.md` activity in 14+ days) → ask whether to retire first. Never let active work-files silently disappear across sessions.

---

## Step 1 — The block (adversary-first)

**Doesn't apply** — no output, act.

**Type B:**
```
→ [action] — [why in one clause]
```

**Type C (compact):**
```
**Vision:** [the deeper goal one level up — the next decision this unblocks. NOT the request reworded]
**Devil's Advocate:** [attack the main assumption a path depends on — written before any path is chosen, no strawman]
**Comeback:** [the honest response that holds a path — or "no comeback, rethink"]
**Chosen Path:** X over Y because Z   [name the rejected Y — no Y, no block]

**Process:**
1. [usually understand/context, not action]
2. ...

→ Embody operator (Step 1.6) if a specialist edge, then start step 1
```

**Type D (full):**
```
**Active Stance:** [pulled from memory if relevant — else omit]
**Vision:** [the deeper goal one level up — WHY it matters, not WHAT was asked]
**Blast Radius:** [specific files, systems, contracts, downstream consumers — vague is useless]

**Paths Considered:** [exactly 2–3, named]
- ...
- ...   [if the 3rd is obvious-wrong filler, ship 2 — don't invent a strawman]

**Devil's Advocate:** [attack the MAIN ASSUMPTION the leading path depends on — before the choice is locked. Make it uncomfortable]
**Comeback:** [the genuine response that holds — or rethink the path now, before committing]
**Blind Spot:** [what's outside the frame entirely — mandatory on D]

**Chosen Path:** X over Y because Z
**Stance:** [the position — one line that survives compaction]
**Reversibility:** [low / med / high — see gating]

**Process:**
1. ...
2. ...

→ [One sharp question] OR [Branching to work-file] OR [Invoking /skill] OR [Starting step 1]
→ Embody operator (Step 1.6) before executing
→ Save Stance to memory if open goal · hand 3+ step lists to TodoWrite
```

**Block rules:**
- Vision = the goal one level up, not a paraphrase and not a multi-year essay. If you wrote "operator wants X" — rewrite it.
- DA attacks the real load-bearing assumption, before the path is crowned. If it doesn't make you reconsider even briefly, it isn't real.
- Comeback is the dialectic close — if none holds, the path is wrong; fix it before the Chosen Path line.
- Chosen Path must name the rejected path (`X over Y because Z`). No Y → no block, act with a one-line stance.
- Blind Spot mandatory on D. Paths capped at 3.
- Skill routing only when the fit is obvious — never forced. One sharp question only when paths genuinely depend on info only the operator has.

---

## Step 1.5 — Inline or work-file?

**Stay inline when ALL hold:** ≤4 files · shippable same day · no new convention / folder / template / hook / skill · no cross-package or cross-repo touch · no decision that must outlive the chat.

**Branch to work-file when ANY holds:** >4 files or new files in >2 dirs · spans >1 session or has a deploy-gate / observation window · introduces a new convention / folder / template / hook / skill / contract · cross-package or cross-repo · strategic feature with parallel-able slices · an unresolved architecture / product decision must persist across sessions.

A work-file is persistent shipping infrastructure. If the work fits the inline floor, infrastructure is overhead — ship inline. Speed > ceremony.

To branch: `→ Branching to work-file — this is real shipping, not a chat plan.` then invoke the `work-file` skill via the Skill tool. It scaffolds the folder, fills templates (VISION → REALITY → GAP-TABLE → PROCESS → AGENTS → DONE), dispatches parallel agents per slice, marks rows as they ship, moves to `completed/` when done. Work-file owns the artifact + execution discipline; birdseye still owns the scan + stance.

---

## Step 1.6 — Embody the operator (the bridge to action)

The block decided *what* and *why*. This step decides **who executes it, to what standard, equipped how** — the bridge from process to action. It is what stops a bland prompt ("optimise the whole codebase") getting a bland answer: three easy fixes, technically done, the other forty-seven left on the table.

**This block is a self-prompt, not a description.** You are not writing *about* an operator for a reader. You are writing the brief **you** operate under for the rest of the task — second person, an instruction you install and obey. Test every line: *does reading this change what I do?* If not, it's decoration — cut it. Text that sounds senior but changes nothing is worse than nothing; it teaches you the brief is wallpaper (same lesson as a theatre block).

Payload is **scope + operational readiness** — the persona is only the vehicle. Substantial in equipping, thin in costume: no backstory, no flavour. Completes the block, doesn't repeat it: **Vision = why · Stance = the position · Operator = who holds the stance and executes it to standard.**

**When it fires:** Doesn't-apply → never · **B** → light brief *only* with a real specialist edge (a quality ceiling a standard lifts) · **C** → light brief · **D** → full brief. When unsure → embody light; the Tell gate kills false fires, so leaning in is free. Bias toward firing — more quality is lost by *not* embodying than by embodying needlessly. (This bias is scoped to a turn where birdseye has *already* fired on C/D — it does not widen birdseye's own default-does-not-fire gate.) **Subject to the compression law:** the brief must be smaller than the work it governs. If it wouldn't be, you're on a light-brief or no-brief task — don't full-forge.

**Derive by micro-inverse — "who fails", not "who fits".** "Who fits" lands on the obvious preset ("senior engineer"). Ask instead: *who would catastrophically fail this, on exactly what capability?* — and become the operator defined by having it. One contained question, not the full the-inverse skill; you're aiming the Devil's-Advocate muscle at identity. If the-inverse already fired this turn, inherit its load-bearing framing instead of re-deriving — that's the elite case. Birdseye never *calls* the-inverse; it sharpens by inheritance only.

**The Operator Brief** — write it whitespaced, as a dossier you can absorb:

Full (D):
```
━━━ OPERATOR BRIEF ━━━

You are <capability-anchored identity from the micro-inverse — one line, no backstory>.

Standards you will not drop:
  — <the bar that defines "done" — the thing that kills "3 of 50">
  — <...>

You optimise for <X>; you will trade <Y> to protect it.

Method:
  1. <how this operator actually works the problem>
  2. <...>

In place before you execute:
  — <tools to install · files/folders to scaffold · repos to clone · workflows/subagents>
  — <crosses the work-file floor? → back to Step 1.5, branch>

Failures you are hunting (name them first — this is how the role dies):
  — <trap>
  — <trap>

The whole job: <scope-lock — the full surface, not the easy subset>.
  <load-bearing axis unresolved? One thing to confirm first — <one sharp question>.>

Tell: <the behaviour this brief forces that a no-brief answer would get wrong>.
```

Light (B-edge / C): the identity line + `Standards` + `Tell`; add `Scope-lock` only if an axis is genuinely ambiguous. `Priorities` and `Method` are D-only.

**The Tell gate — this must be useful to YOU, not pretty.** A brief earns its place only if operating under it **prevents a mistake the default answer would plausibly make** — not a theoretical difference you can confect ("handles unicode"), a real landmine the no-brief instinct walks into ("adds `DEFAULT`, locks the table"). Test: *would a competent no-brief answer get this wrong?* Yes → load-bearing, wear it. No → you're decorating; write `no brief — acting plainly` and just work. This is the same action-delta gate the block runs on the Chosen Path (`X over Y` — no Y, no block), pointed at the operator: no real mistake prevented, no brief. The detachment — the brief is an instruction to yourself, valuable only because it changes your execution, never because it reads well.

**Embodiment — persist to the goal, not the turn.** A full (D) brief is goal-bound: it travels with the Stance and stays installed across turns *until the Stance's success signal fires* — that is how you don't drift back to generic by turn five. Resume it from the Stance lookup each goal-turn; re-read it as you work, not once at the top. Two hard conditions: **re-derive on pivot** (if the task changes shape, the persisted operator is the wrong one wearing the right badge — re-run the micro-inverse) and **retire on success** (when the goal's success signal fires, the operator retires *with* the Stance — a brief with no retirement condition is the zombie-stance failure). Light briefs (C / B-edge) are task-local: they evaporate at end of turn. One operator at a time — to build then attack, fully exit one brief before the next.

---

## Step 2 — Self-check + gating

**Before writing the block, in your head:** kill the obvious first answer (what's the path if the obvious one were forbidden?) · assumption audit (what am I treating as known about the codebase — APIs, structure, conventions, that a file exists — that I haven't actually read? Verify before building on it; never assume structure) · bloodline check (does this match how the operator wants things shaped? technical optimum ≠ right answer if off-bloodline — read recent feel/vision memories) · on D, the 5-year question (if this works perfectly, what's the 5-year version?).

**Gating, wired to `Reversibility:`:**
- **low / med** → surface block, proceed. The operator can redirect mid-flight. Never ask permission here — slowness ≠ thoughtfulness.
- **high** → surface block, **wait for explicit yes** before acting.
- **Irreversible** (delete data, force push, paid action, deploy) → always hard-gate, regardless of Reversibility.

---

## Step 3 — Revision hook

When the operator pushes back on a Stance or Chosen Path ("no, the other way", "actually let's…", "I don't like that"):
1. Re-run the scan with the new constraint as input.
2. Surface a fresh block with updated Stance.
3. Update the saved Stance in memory — don't leave the stale one.
4. Save a `Process:` memory if the rejection revealed something non-obvious about how the operator thinks.
5. If a work-file is active, update its `VISION.md` and `GAP-TABLE.md` to reflect the pivot.

---

## Step 4 — Memory (the stance loop)

Memory is bidirectional — read at scan time (Stance lookup), write at decision time.

**Memory may not exist in every runtime.** If stance-memory tools are available → read on scan, write on decision. If they are not → surface the Stance inline in the block and continue. Never block the work on a memory layer that might be absent; memory is a compounding bonus, not a hard dependency.

- **Stance (Type D — mandatory on an open goal)** → `project` memory. Title `Stance: [goal]`. Body: goal, stance, why, **success signal**, **+ operator brief if one was forged** (so it resumes in-character on later goal-turns). Retire by deleting the file when the success signal fires — which retires the operator with it.
- **Bloodline** (vision / feel / aesthetic / decision-style signals) → `feedback` or `user` memory. Title `Bloodline: [theme]`. Save only when surprising.
- **Process insight** (a non-obvious pick that worked or was confirmed) → `feedback` memory. Title `Process: [insight]`. Save only when surprising.

**Stance freshness.** When a saved Stance's goal comes up again, check it against current reality before inheriting — if the world moved, supersede it (update the file). A saved Stance is reusable *until contradicted*; old framings don't become sacred law. Retire a Stance by deleting its file when the success signal fires.

(Compounding-frame logbook removed 2026-05-31: never wrote an entry in 27 days, and the-inverse log already does this job. Stance-to-memory above is the live mechanism.)

**Save bar:** would future-me, in another session, benefit? Save Stances aggressively (they prevent drift); Bloodline / Process only when it compounds.

---

## Sequencing + ambiguity

**Skill order:** the-inverse (question-sharpener) → the-prescription (vehicle-chooser) → **birdseye (process pre-flight)** → domain skills (office-leader, work-file, etc.). Birdseye is a pre-flight, not a competing option: scan → classify → *then* invoke the domain skill with the classification in hand. Never skip the scan because another skill triggered — it handles the role/domain, birdseye handles the thinking process. (Reinforced by the `birdseye-prompt-guard.js` UserPromptSubmit hook.)

**Ambiguity:** ask ONE sharp question only when the ambiguity would materially change the chosen path. Otherwise make the best grounded assumption and state it inline. Uncertainty is not a parking brake.

---

## Anti-patterns (top failures)

| Pattern | Why it fails |
|---|---|
| Action-first on Type C/D | Defeats the entire skill |
| Block with no rejected path | No decision happened — it's theatre; skip it and act |
| Devil's Advocate written after the path was chosen | Attacks a strawman; the choice is already locked |
| Vision = paraphrase of the request | Vision is the deeper goal one level up |
| Vision = 1–3-year essay on a tactical turn | Manufactured grandiosity — also theatre |
| Fake unconventional (slight tweak dressed as a path) | Say "only one real path" instead |
| Silent drift — stopped scanning mid-thread | Surface the retroactive block; re-scan every 5 turns |
| Tier inflation — everything is C/D | Most turns are execution or B; recalibrate |
| Inheriting classification past turn 3 | Continuation resets, doesn't compound |
| Holding multi-file plans in chat | Branch to work-file; chat plans rot |
| Stances saved but never retired | Memory fills with stale positions |
| Writing memory but never reading it | Bidirectional or it's useless |
| Operator brief described, not installed | It's costume — write it as a self-instruction that changes execution, or drop it |
| Tell that prevents only a theoretical mistake | Decoration; the gate wants a plausible default error — else `no brief — acting plainly` |
| Building on assumed structure / hallucinated APIs | Assumption audit (Step 2): read the real code before asserting it; never assume a file/API/convention exists |
