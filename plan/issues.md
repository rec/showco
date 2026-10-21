# showCo issues

Keep this file for outstanding findings only. Remove each issue's section in the
commit that completes it rather than retaining resolution notes.

## Slow Pi connections over showbox Wi-Fi

Connecting to the Pi through showCo was slow on **showbox Wi-Fi**. The cause
remains unresolved. The quick SSH connections observed on 2026-10-21 used
**house Wi-Fi**, so they do not demonstrate that the showbox issue is fixed.

Existing SSH timeout and retry changes improve failure handling but do not
establish that the connection slowdown was resolved. Investigate and verify on
showbox Wi-Fi before closing this issue.

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
