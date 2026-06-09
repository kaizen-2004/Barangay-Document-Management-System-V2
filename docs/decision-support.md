# Decision Support

Use this workflow by default:

THINK → MAP → SUGGEST → CHOOSE → PATCH → CHECK

Use the workflow lightly. Do not turn every task into a long report.

## THINK
Clarify:
- the user's goal
- constraints
- preferences
- intended behavior change
- what is still uncertain

Skip extra clarification when the goal is already clear.

## MAP
Inspect before proposing edits.

Identify:
- relevant files
- current behavior
- code flow
- nearby patterns to reuse
- smallest change surface

Mapping must be targeted:
- Start from explicit file names, stack traces, route names, component names, commands, or screenshots.
- Prefer `rg "term" likely/path` or direct file reads over full-repo scanning.
- Do not inspect unrelated modules just to be comprehensive.
- Do not repeat the same map step unless the task scope changes.

## SUGGEST
When helpful, briefly remind the user of relevant:
- tools
- technologies
- patterns
- techniques

Suggestions must be:
- relevant
- brief
- optional
- grounded in the current stack or task

Avoid suggestion overload. If the simplest path is obvious, recommend it directly.

## CHOOSE
When multiple reasonable approaches exist:
- present 2 to 3 viable options
- include short pros and cons
- recommend the simplest viable option
- let the user decide when tradeoffs matter

Do not create artificial options when there is one obvious fix.

## PATCH
Once direction is clear:
- implement the smallest coherent change
- preserve existing architecture unless asked otherwise
- avoid unrelated cleanup
- avoid broad rewrites
- avoid new dependencies unless necessary

## CHECK
After implementation:
- run the narrowest useful validation
- report the exact command and result
- mention remaining risks or follow-up items briefly

## Facts vs assumptions
Separate:
- Observed: directly seen in code/config/tests
- Assumed: plausible but not yet verified
- Unknown: not yet determined

Do not present assumptions as facts.
