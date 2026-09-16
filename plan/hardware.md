# Open hardware issues

Hardware testing was deferred on 2026-09-16. These findings remain open; automated tests do not establish Pi/X18 behavior. Original issue numbers are retained for reference.

## 8. Cable-test capture timing

`showco/x18/cable_test.py:audio_round_trip` starts capture and playback independently and records for exactly the tone duration. Analysis discards 250 ms at each end. A larger startup offset can include silence or miss part of the tone, falsely reporting a cable fault.

Measure capture readiness and playback delay on the Pi. Establish a timing contract, then provide sufficient capture pre-roll and tail or align analysis to the recorded steady-state tone. Verify delayed startup without concealing actual signal dropouts.

## 9. Cable-test routing isolation

`X18TestRouting` enables the source on selected buses but leaves other channels' contributions and the source's sends to unselected buses intact. Existing scene audio can contaminate measurements, and the tone can reach unintended destinations. Matching physical AUX output assignments are documented as a prerequisite but are not checked.

Verify a required mixer scene, or save, isolate, and restore all necessary routing. Test with other active sends and nondefault output assignments. Confirm that only intended outputs receive the tone and that the original scene is restored after success and failure.

## 10. Cable-test calibration

Signal classification is fixed, but the thresholds have not been validated with known-good physical cables: at least 98 percent tone similarity, 70–130 percent level, peak below 0.99, and a fixed minimum signal RMS.

Measure the noise floor, level, and waveform quality with the actual gain configuration and known-good cables. Include disconnected, attenuated, and distorted signals. Use the results to confirm or adjust thresholds and record the calibration conditions.

## 25. Installation acceptance

The exact live installation still lacks a recorded end-to-end acceptance result. Browser, subprocess, routing-fake, and WAV regression tests do not prove physical acquisition or show readiness.

Follow the installation checks in [Operations and deployment](../doc/README.md#before-the-performance): confirm recording to removable storage, readable recordings after remounting, tablet and mixer access, enabled lighting and streaming output, power-cycle recovery, devices arriving after boot, and a five-hour soak with all live services.

Record the date, deployed commits, hardware, mixer scene, recording destination, measurements, and pass/fail results. Close each finding only when its hardware evidence is available.
