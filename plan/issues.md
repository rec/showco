# showCo code review

Initial static review of checkout `5125349` on 2026-10-19. This replaces the
previous issue list. That review ran no application, service, deployment, hardware
flow, or test suite. This plan contains outstanding findings only; remove
completed findings rather than retaining resolution notes. The findings are not
failures observed on
the Pi. P1 means potentially disruptive or misleading during operation, P2 means
an ordinary correctness or recovery problem, and P3 means a maintainability
concern. Line numbers refer to the reviewed checkout. Paths below are relative
to `showco/` unless prefixed with `site/` or `test/`.

## Additional work beyond the prompt

None.

## Intended behavior and scope

Cable testing is a separate utility, not an expected runtime workflow. Its
results do not need to become live-show faults.

`showco-errors.txt` intentionally describes exactly the last invocation. Starting
another invocation replaces the previous diagnostic; retaining failure history
across invocations is not a requirement.

## API shape, duplication, and module boundaries

### 27. P3: Action dispatch and music lifecycle concentrate too many contracts

`runtime/app.py` is 629 lines. `_dispatch_action()` combines performance
protection, workflows, recs configuration, musicians, service
lifecycle, lighting, and music. `runtime/music.py` is 678 lines and combines
configuration, persistence, synchronous transitions, an observer thread, closing
recovery, and shutdown. Ownership rules are spread across string sets, locks,
and controller methods, making the transition contracts difficult to follow.
The problem is mixed responsibility rather than line count alone.

Make the existing ownership/transition contracts explicit when fixing these
paths. A wider architectural extraction needs separate scope; splitting files
alone will not fix behavior.

## Review limits

This source review is not exhaustive execution coverage. Checks suggested for
the outstanding findings have not been run.
Pi networking, X18 routing, broadcast delivery, and physical audio acceptance
still require the existing hardware plans. Previous lack of physical verification
is not evidence of a newly discovered code defect.
