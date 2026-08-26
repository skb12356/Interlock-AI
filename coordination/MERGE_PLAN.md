# Merge plan

**How Person 2's console comes back together with Person 1's backend.**

Read `ALLOTED_WORK.md` first. This file is what you both follow at the end of day 1.

---

## 0. Why this should be boring

A merge is painful when two people edited the same lines. This split was chosen so that
almost nothing overlaps:

```
Person 2 touches:  console/**
                   interlock/gateway/console_ws.py     (pre-created, already mounted)
                   scripts/replay_console.py
                   coordination/PERSON2_NOTES.md

Person 1 touches:  everything else
```

**`interlock/gateway/app.py` is the only file both would naturally need**, and it has
already been edited — the console websocket router is mounted *now*, before Person 2
starts, so they never open it. If that file shows up in Person 2's diff, something went
wrong; see §5.

Expected conflicts: **zero**. If git reports any, treat it as a signal that the split
was violated, not as routine merge work.

---

## 1. Branches

```bash
# Person 2, at the start
git checkout -b console
# ... work ...
git push -u origin console

# Person 1, at the start
git checkout -b backend
# ... work ...
git push -u origin backend
```

Neither pushes to `master`. `master` only ever moves via a merge that both have seen.

---

## 2. Person 2's handoff checklist

Before pushing at the end of day 1, run these **in the repo root** and paste the output
into the PR description (or a message — a PR is nicer but not required):

```bash
uv run ruff check .          # must pass; the CI runs it
uv run pytest -q             # must still be 653+ passing, 0 failing
```

Then confirm each of these honestly. "Partly" is a fine answer; a wrong answer here
costs an hour on day 2.

- [ ] `git status` is clean and `node_modules/` is **not** tracked
- [ ] No API key, `.env`, or tenant canary string committed
- [ ] Nothing outside my four owned paths appears in `git diff master --name-only`
- [ ] The console runs against `scripts/replay_console.py` with **no** Ollama running
- [ ] Which of the six console tasks are done, and which are stubs:
  - [ ] D4-A4 split-screen risk trail
  - [ ] D2-A7 live stream plumbing
  - [ ] D4-A5 review queue
  - [ ] D4-B4 ledger view
  - [ ] D4-B5 chart panels
  - [ ] D1-A5 demo chat UI
- [ ] What stack I actually used (React/Vite? plain HTML? something else?)
- [ ] Anything I had to fake because the backend data was not there yet — **list it**,
      because P1 needs to know what to wire up for real

That last line matters more than the rest. A console with a hardcoded loss table looks
identical to a working one until the demo, and the person who wrote it is the only one
who knows.

---

## 3. The merge, step by step

Person 1 drives this. Person 2 should be reachable for ~30 minutes.

```bash
# 1. Get both branches locally and confirm what is coming
git fetch origin
git log --oneline master..origin/console
git diff master...origin/console --name-only     # <- must be only P2's owned paths

# 2. Land the backend first. It is the larger diff and it owns master's direction.
git checkout master
git merge --no-ff backend -m "merge: backend work (F-019, ledger, Lane C)"

# 3. Land the console on top.
git merge --no-ff origin/console -m "merge: operator console and demo UI"
```

If step 3 conflicts, **stop and read §5** — do not resolve it by picking a side.

```bash
# 4. Verify the merged tree, in this order. Each step is cheap and catches a
#    different failure, so do not skip to the last one.
uv run ruff check .
uv run mypy --strict interlock/core interlock/retrieval interlock/interlock_tools
uv run pytest -q                                  # backend contract still intact
uv run python scripts/build_index.py              # retrieval index rebuilds
uv run python scripts/calibrate.py                # artefacts regenerate
uv run python scripts/eval.py                     # six numbers still print

# 5. Then the thing tests cannot check: run it and look at it.
make up
# open the console, send one high-stakes question, watch the risk trail populate
```

Step 5 is not optional. Every bug worth finding on day 2 will be found there and not by
`pytest` — the last two sessions each turned up a real defect that only appeared when
the system was actually run.

---

## 4. What "merged" means

Do not call it merged until all of these hold:

- [ ] `ruff` and `mypy --strict` clean
- [ ] `pytest` green, and the count went **up**, not down
- [ ] `make eval` still prints six numbers
- [ ] The console renders a real decision from a **live** gateway, not from the replay
      server — this is the first moment the two halves have ever met
- [ ] `IMPLEMENTATION_STATUS.md` and `STATE_CHECKPOINT.json` updated to say the console
      exists and what it covers
- [ ] `TODO.md` boxes ticked for whatever actually landed, and **only** those

Then, and only then:

```bash
git push origin master
```

---

## 5. If it does conflict

**Do not resolve a conflict by choosing a side.** A conflict here means the ownership
boundary was crossed, and picking one version silently deletes someone's work.

```bash
git merge --abort
git diff master...origin/console --name-only     # find the file that should not be there
```

Then decide together which is true:

| Situation | Do this |
|---|---|
| P2 edited a P1 file by accident | P2 reverts just that file on their branch, re-pushes |
| P2 genuinely needed a backend change | P1 makes the change on `backend`, P2 rebases onto it |
| Both edited `app.py` | P1's version wins; P2's need should be re-expressed inside `console_ws.py` |
| An artefact JSON differs | **P1's wins.** They are regenerated output — never hand-merge them; re-run `scripts/calibrate.py` and `scripts/eval.py` after merging |

That last row catches a real trap: `artifacts/**` files are *generated*, and a
hand-merged calibration artefact would be a number nobody can reproduce, which is worse
than a broken build because it looks fine.

---

## 6. After the merge — the remaining work, splittable again

The point of merging on day 2 is that neither of you is blocked afterwards. The split
still holds:

**Person 1 continues on:** the observer probe (D2-B4/B5/B7), MiniCheck (D2-B6), the
router + semantic cache (D3-A4), the 300 hand labels (D2-B3), evidence pack (D4-A6).

**Person 2 continues on:** whatever console tasks were stubs, then polish — and the
single most valuable follow-up is making **F-019's fix visible**: once P1 changes the
impact model, the console should show the before/after so the change is legible rather
than a silently different number.

**Both, together, on day 5:** the measurement run (`make eval`, three seeds, off vs on)
and the rehearsal. `TODO.md` has a **FEATURE FREEZE** entry for a reason — after that
hour, no new features, only fixing what the rehearsal exposed.

---

## 7. One-paragraph summary for whoever reads this cold

Person 1 owns the control plane — proxy, retrieval, calibration, risk engine, tool
interlock, ledger, Lane C. It is built and measured, and one finding (F-019) currently
makes the flagship demo block a reasonable answer; fixing it is P1's first job. Person 2
owns everything visible — the operator console and the demo chat UI — and builds it
against a frozen SSE contract and a replay server that needs no GPU, no model, and no
running backend. They merge on day 2, with an expected conflict count of zero, because
the only shared file was edited in advance to remove the collision.
