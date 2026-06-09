# Token Efficiency and Cost Control

Use this file to keep agent work efficient for large projects.

## Main principle
Work from the smallest reliable context, not the largest possible context.

## Avoid repeated large-codebase scans
Do:
- inspect known files first
- search for exact symbols, route names, component names, error text, or template names
- reuse the file map from the current task
- limit search paths to likely directories
- exclude generated/vendor/cache folders

Do not:
- repeatedly run full-repo scans
- read entire large files when a function or section is enough
- reopen unchanged files already inspected
- explore unrelated modules without a clear reason

## Search budget
Default limits for one task:
- Initial mapping: 1 to 3 targeted searches or direct file reads
- Extra inspection: only when a specific uncertainty remains
- Broad search: only once, and only when targeted search fails
- Repeated broad search: stop and explain why it is needed

## Patch budget
Default limits for one task:
- 1 planned patch
- 1 focused correction if validation fails
- Stop after 2 failed patch attempts

## Output budget
Default answer length after implementation:
- 5 bullets or fewer
- no full file paste unless requested
- no long explanation unless requested

Use this compact report:

```text
Changed:
- path/to/file: what changed

Validated:
- command: result

Risks:
- remaining issue, if any
```

## When the user asks for deeper work
For large refactors, first create a bounded sprint:
- goal
- files or folders in scope
- files or folders out of scope
- maximum scan strategy
- validation command
- stop condition

Do not attempt a whole-project rewrite unless the user explicitly asks for it.
