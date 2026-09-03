# First Gear Test Setup

This checklist assumes the Raspberry Pi has a fresh Raspberry Pi OS Lite image,
the correct hostname or IP address, the expected user account, and working SSH
access, but Showco has not yet provisioned it.

Before its first boot, prepare the Imager-written card while its boot volume is
mounted on the developer machine:

```bash
showco prepare-card
```

This enables noninteractive `sudo` for the configured Showco user. Current
Raspberry Pi OS Lite images do not expose that setting in Raspberry Pi Imager.

1. Put the Raspberry Pi, X18, tablet, and developer machine where they can all be
   powered and reached during the test.

2. Plug the X18 Ethernet port directly into the Raspberry Pi Ethernet port.

3. Plug the X18 USB audio interface into the Raspberry Pi.

4. Plug any extra USB storage for recordings into the Raspberry Pi.

5. Plug in any second Wi-Fi interface that should be used for the external
   network.

6. Power on the X18.

7. Power on the Raspberry Pi and wait until SSH is reachable.

8. From the developer machine, confirm the Pi is reachable with
   `ssh tom@bertrand.local true`.

9. In `showco/provision/config.toml` and `showco/provision/secrets.toml`, confirm
   the Pi host, user, X18 network, internal Wi-Fi, external Wi-Fi, Twitch, Lyte, and
   USB device settings are correct for this test.

10. From the developer machine, run `uv run showco go`.

11. Wait for provisioning to finish, reboot the Pi, reconnect over SSH, and print
    `Success!`.

12. On the Pi, check Showco and Recs with
    `cd <root>/showco && uv run showco run service-status recs showco`, where
    `<root>` is `[paths].root` in the provisioning configuration. If Lyte is
    enabled, also run `uv run showco run service-status lyte`.

13. On the Pi, confirm the X18 USB device is visible with `arecord -l`.

14. Connect the tablet to the Pi internal Wi-Fi network.

15. On the tablet, confirm it can control the X18.

16. From a browser on the tablet or developer machine, open the Showco web UI.

17. Start a short Recs recording and confirm Showco shows recording status.

18. If Twitch is part of this test, start a short stream and confirm Showco shows
    Twitch status.

19. Stop the test recording and stream.

20. Confirm the recording files exist on the intended recording disk.
