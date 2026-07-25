# Checklist

This is the short external-task checklist for getting the show box ready. It
omits already-decided hardware, storage, and SD-card details.

Assumptions:

- record all X18 channels
- record 24-bit WAV
- disk space is more than enough

## Things I need to decide on

- Pi access point SSID.
- Pi access point password.
- Pi static IP address on its own Wi-Fi network.
- DHCP range for devices connected to the Pi Wi-Fi.
- Ethernet subnet between the Pi and the X18.
- X18 wired Ethernet IP address.
- Whether Twitch is enabled when there is no reliable internet.
- Twitch account to use for the show.
- Where the Twitch stream key and API token will be stored.
- How Twitch tokens will be replaced or refreshed.
- Default Twitch stream title.
- Default Twitch category.
- Default Twitch tags.
- Standard chat messages.
- Standard stream marker descriptions.
- Showco URL bookmark name and location on the tablet.
- Normal setup sequence before a show.
- What to do if Twitcho fails.
- What to do if Recs fails.
- What to do if the Pi network fails.
- Labels for the ports and cables used during setup.
- Final recording directory path.

## Things I need to discover

- X18 USB device name as seen by Linux.
- Whether the tablet can control the X18 through the Pi network.
- Whether local Wi-Fi can support the mixer, tablet, and Mac rehearsal setup.
- Whether the Pi access point can support the tablet and mixer control reliably.
- Whether the Pi can use a second Wi-Fi path for Twitch internet.
- Twitch broadcaster ID.
- Twitch sender ID.
- Twitch moderator ID.
- Twitch OAuth token scopes actually granted.
- Whether Twitch clip creation succeeds only while live.
- Whether Twitch stream markers succeed only while live.
- Exact Showco URL from the tablet.
- Whether the tablet UI remains readable in stage lighting.
- Whether switching between the mixer app and Showco is fast enough.
- Whether Recs calibration succeeds after stage levels are set.
- Whether Twitcho mute, unmute, stop, chat, announcement, clip, and marker
  actions succeed from Showco.
- Whether Recs and Twitcho recover cleanly when restarted while Showco is open.
- Whether losing internet leaves Recs recording unaffected.
- Maximum CPU use during a full recording and streaming test.
- Maximum memory use during a full recording and streaming test.
- Maximum Pi temperature during a full recording and streaming test.
- Actual network latency for mixer control.
