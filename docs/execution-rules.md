# Execution Rules

## General
- Follow the surrounding code style first.
- Prefer clarity over cleverness.
- Keep control flow straightforward.
- Use local conventions over generic best practices when they conflict.
- Keep diffs small and reviewable.

## Change design
- Prefer the smallest fix or feature addition that solves the problem cleanly.
- Do not over-engineer.
- Do not create new abstractions unless they reduce real complexity.
- Do not mix bug fixes with unrelated refactors unless necessary.
- Do not touch files outside the mapped scope unless a direct dependency requires it.

## Working style
- Inspect first, then implement.
- Reuse existing utilities and patterns.
- Avoid speculative cleanup.
- Keep changes easy to review and revert.
- Keep explanations concise while working.

## Search and inspection rules
- Use targeted search terms from the task.
- Prefer direct file reads for known files.
- Limit initial inspection to the smallest useful file set.
- Do not repeatedly run repository-wide searches.
- Exclude generated/vendor/cache folders from searches unless explicitly relevant.
- If the needed file cannot be found quickly, report the search terms tried and ask for the path or inspect one more targeted location.

Suggested search shape:

```bash
rg "specific_symbol_or_error" app/ templates/ static/ tests/ \
  -g '!node_modules' \
  -g '!.git' \
  -g '!dist' \
  -g '!build' \
  -g '!.next' \
  -g '!venv' \
  -g '!.venv' \
  -g '!__pycache__' \
  -g '!coverage'
```

## Patch rules
- Make one coherent patch per fix hypothesis.
- Do not perform repeated blind edits.
- If a fix fails, use the error output to target the next edit.
- Stop after two failed patch attempts unless the user asks to continue.

## Validation
Choose the smallest useful check:
- focused test
- package/module test
- typecheck
- lint for touched files
- narrow build step

If nothing can be run, say so explicitly.
