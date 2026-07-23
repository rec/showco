# Hardware-adjacent decisions

This checklist captures the non-code decisions that must be made before the
Raspberry Pi show box is field-ready.

## Raspberry Pi

- exact Pi model
- RAM size
- case
- cooling method
- power supply
- whether a touchscreen is included
- how the Pi is physically attached to the X18

## Storage

- storage type: nano USB key, larger USB key, short USB extension, or SSD
- capacity
- filesystem
- mount point
- physical strain relief
- backup rotation
- whether the final design accepts the risk of small USB key write stalls

## SD card

- size
- brand/model
- whether the SD card stores only the OS and software
- image backup process
- spare SD card plan

## Audio

- X18 USB device name as seen by Linux
- Recs input selection flags
- channel count to record
- bit depth and file format
- recording directory
- expected maximum show duration
- acceptable free-space threshold

## Network

- Pi access point SSID
- Pi access point password
- Pi static IP address
- DHCP range
- tablet IP behavior
- Ethernet subnet between Pi and X18
- X18 wired IP address
- whether the tablet controls the X18 through the Pi network
- whether an additional Wi-Fi dongle is needed for internet
- whether Twitch is enabled when there is no reliable internet

## Twitch

- Twitch account
- stream key storage
- OAuth token creation process
- token refresh/replacement process
- token scopes
- broadcaster ID
- sender ID
- moderator ID
- default title
- default category
- default tags
- standard chat messages
- standard marker descriptions

## Show operation

- tablet mounting or placement
- Showco URL bookmark
- normal setup sequence
- who presses calibration
- when calibration is pressed
- whether Twitch starts before doors, at downbeat, or manually
- what to do if Twitcho fails
- what to do if Recs fails
- what to do if the Pi network fails

## Cables and labels

- Pi power cable
- X18 USB cable
- X18 Ethernet cable
- storage device label
- spare USB storage
- spare SD card
- cable strain relief
- visible labels for every port used during setup

## Acceptance thresholds

- minimum successful soak-test duration
- maximum acceptable CPU use
- maximum acceptable memory use
- maximum acceptable Pi temperature
- minimum free disk space before show
- acceptable Twitch bitrate
- acceptable network latency for mixer control

## Open decisions

Record unresolved decisions here before hardware arrives:

- Pi model:
- storage model:
- case:
- cooling:
- access point SSID:
- X18 Ethernet subnet:
- recording mount point:
- Twitch enabled by default:
- fallback if USB storage is not mounted:
