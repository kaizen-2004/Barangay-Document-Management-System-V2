# Debugging

For bug-related tasks, follow this order:

1. Reproduce
2. Trace the relevant code path
3. Identify the likely root cause
4. Confirm the fix hypothesis against the code
5. Apply the smallest coherent fix
6. Validate the fix and regression risk

## Rules
- Do not patch symptoms without tracing the likely cause.
- Prefer fixes supported by code evidence.
- If the issue cannot be reproduced, say so clearly.
- State what is observed, assumed, and unknown.
- Do not repeatedly scan the whole project looking for clues.
- Do not make multiple unrelated fixes in one debugging pass.

## Debugging budget
- First pass: inspect only the error, the direct caller, and the direct callee/config involved.
- If the cause is not found, inspect one adjacent layer only.
- If the cause is still unclear, stop and summarize the uncertainty instead of guessing.
- Maximum two patch attempts before stopping for a new plan.

## Failure handling
When validation fails, report briefly:
1. command run
2. exact failure category
3. likely cause
4. next safest action

Do not continue trial-and-error edits without a new hypothesis.
