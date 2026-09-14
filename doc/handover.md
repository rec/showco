# Handover

This file is intentionally limited to working context that cannot be recovered
from the source tree, Git history, plans, or the other documentation.

## Current Focus

The first live installation uses two Twinkly strings. Wearable lighting remains
deferred because the garment hardware is not currently in use. Keep the
wearable catalogue available in Lyte, but do not let wearable work change
Showco's default lighting installation or deployment work until the hardware is
back in scope.

The project is in an integration and physical-validation phase rather than a
feature-expansion phase. Prefer small fixes that make the existing live path
more observable, reliable, and easier to operate.

## Unresolved Work

There is no recorded end-to-end acceptance result for the exact hardware,
network, removable recording disk, and service revisions that will be used at
the next performance. Automated checks do not establish current device
reachability, audio capture, storage durability, lighting output, or streaming.

Recs is changing quickly. Treat its public control, status, and waveform
interfaces as compatibility boundaries: read the current Recs protocol and
source before changing a Showco adapter, and do not preserve behavior based
only on an older Showco assumption.

## Boundaries

Do not alter machine-local secrets, live network credentials, mixer routing, or
physical-device configuration as incidental code work. Those changes need an
explicit request and a corresponding live verification.
