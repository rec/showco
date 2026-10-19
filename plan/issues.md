# showCo issues

No outstanding functional findings remain from the 2026-10-19 review.

Keep this file for outstanding findings only. Remove each issue's section in the
commit that completes it rather than retaining resolution notes.

## Additional work beyond the prompt

None.

## Intended behavior and scope

Cable testing is a separate utility, not an expected runtime workflow. Its
results do not need to become live-show faults.

`showco-errors.txt` intentionally describes exactly the last invocation. Starting
another invocation replaces the previous diagnostic; retaining failure history
across invocations is not a requirement.

Automated verification does not replace physical acceptance of Pi networking,
X18 routing, audio output, or broadcast delivery. Those checks remain in the
existing hardware plans.
