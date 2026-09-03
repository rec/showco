# Fix Lyte Twinkly Installation

## Problem

Lyte and the target Raspberry Pi can reach and control the Twinkly controller
at the protocol level, but no LEDs illuminate. The Twinkly mobile app appeared
to complete adding the device while connected to the Pi's network, yet the
device did not subsequently appear in the app's Installation. This has happened
before.

The controller was also not successfully moved to the main network because the
long Wi-Fi password was unavailable at the time. Do not treat the controller's
network presence as proof that it has a usable Twinkly installation.

## Observed Evidence

On 3 September 2026, the Raspberry Pi reached `192.168.1.17` through `wlan0`
from `192.168.1.21`. Ping and ARP resolution succeeded. The controller's
Gestalt response identified it as:

- Product: `TWS250STP-B`
- Hardware ID: `183dc8`
- Controller MAC: `24:6f:28:18:3d:c9`
- LED count: 250 RGB LEDs in two strings of 125
- Firmware: 2.9.1, family F
- Compatibility mode: enabled

Lyte was healthy while sending output:

- State: `streaming`
- Target: `192.168.1.17`
- Frame-send count advanced into the thousands.
- No renderer, output, authentication, or recovery error was reported.

The controller also acknowledged direct, authenticated API calls to set full
white static colour and `color` mode. It reported brightness 91 and a timer
whose current time was inside its configured on interval. The LEDs remained
dark. This eliminates Showco action dispatch, Lyte's queued-test handling,
Lyte's realtime UDP output, target routing, and controller authentication as
the immediate cause.

The relevant missing evidence is that the Twinkly app has a valid Installation
containing this physical controller and can visibly control it. The current app
state indicates that it does not.

## Recovery And Verification Plan

1. Use the Twinkly app to remove any incomplete or duplicate onboarding state.
   Confirm that `Twinkly_183DC9` appears in an Installation, not merely as a
   device discovered during setup.
2. With the phone connected to the same Wi-Fi as the controller, use the app
   to set the installation to a bright static colour. Confirm that the exact
   250-LED physical string lights.
3. If the app cannot illuminate the string, treat this as controller, string,
   power, or app-installation recovery work. Check the controller-to-string
   connections and power before changing Lyte.
4. Once the app controls the string, record the controller's current IP and
   confirm that the Pi can again read its Gestalt response. Keep Compatibility
   Mode enabled.
5. Run `lyte patch locator` on the assembled fixture. It must visibly identify
   the intended string and establish that Lyte's configured layout maps to the
   physical LEDs.
6. Only after the locator succeeds, run the Showco `Test lights` action and
   confirm the requested flash. Capture Lyte status if that step fails.

## Acceptance Criteria

- The app lists `Twinkly_183DC9` in an Installation.
- The app visibly controls the intended 250-LED fixture.
- `lyte patch locator` visibly addresses that fixture.
- Showco `Test lights` produces a visible flash and Lyte Health reports a
  meaningful failure if it cannot.

## Additional Work Beyond The Prompt

None.
