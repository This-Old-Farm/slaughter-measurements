# Measure the Slaughter Floor with Computers

Configuring a computer that currently has only one job: reliably collecting and preserving animal ID/weight events.

`listen.py` starts measurement recording

`shout.py` starts a simple http server to share the measurements
- http://localhost/test displays the table
- http://localhost/get?seconds=N is an api which accepts an integer number of seconds, and returns json for all measurements taken in the most recent N seconds
- http://localhost/matches?date=YYYY-MM-DD returns json pairing each live animal with its hang tags for a day (default today), with QA flags
- http://localhost/live is for trying the three candidate live-station pages (`/live/a`, `/live/b`, `/live/c`), which show the animal being scanned, what's left to scan for it, and today's live animals. It has a short guide to the scan process, buttons to switch versions, and switches to hide the animals table, barcode names, or flags column. The operator picks one version and the others are deleted (#62)

## Scan Process

**Live station:** scan the animal ID (ends in `B`, `P`, or `L` for beef, pork, or lamb). That alone starts weight collection. Then scan one answer for each step that applies, in this order:

| Step | Yes | No | Species |
|---|---|---|---|
| Firearm | `FIREARM` | `NOFIREARM` | beef, pork, lamb |
| Dirty | `DIRTY` | `CLEAN` | beef, lamb |
| +30 months | `PLUS30` | `UNDER30` | beef |
| Dairy | `DAIRY` | `NOTDAIRY` | beef |
| Sex/type | the sex/type code | | beef, pork, lamb |

Every step is required, so a "no" is scanned too. A scan meant for a different step is ignored. If the next animal ID is scanned first, the weight and the answers already scanned are kept, and the missing steps are left blank.

To start over after a wrong scan, rescan the animal ID. Every scan sequence is kept, even one without a weight. Matching uses the one with the most answers (the newest on a tie) and flags the rest for QA, and the live table on `/display` highlights unfinished and unused sequences.

**Hang station:** put a half carcass on the scale and scan any unused hang tag. The first stable weight is saved with that tag.

Scanning a code that was already weighed today records a reweigh: the new weight is saved and the code keeps its original place in the order.

`matching.py` pairs the nth live animal with the next hang tags in first-scan order (two for beef and pork, one for lamb). It uses each code's most recent valid weight (a zero or negative reading means the scale was empty or hadn't settled, and is never used) and flags for QA: reweighs, codes with no valid weight, a yield (hang total / live weight) outside the species' band, and two halves that differ by more than 15%. It is a prototype that may move to lapis.

Run its tests with:

``` bash
python3 -m unittest
```

## Current Hardware Setup

### Barcode Scanner

The barcode scanner is connected by USB and operates as a USB HID keyboard device.

Linux exposes the scanner as an input device. During initial testing it was:

``` text
/dev/input/event14
```

The scanner was successfully tested with:

``` bash
sudo evtest /dev/input/event14
```

### Live Scale Indicator

The scale indicator is an MSI-8000HD connected directly to the laptop's built-in RS-232 serial port.

Linux exposes the serial port as:

``` text
/dev/ttyS0
```

Current expected serial configuration:

``` text
9600 baud
8 data bits
No parity
1 stop bit
```

This is commonly written as:

``` text
9600-8-N-1
```

### Hang Scale Indicator

The hang station's scale indicator is a Rice Lake IQ355+, connected through a Prolific USB-to-serial adapter.

Linux exposes the adapter as `/dev/ttyUSB0`. The PC's cellular modem also takes `ttyUSB` numbers, so check which one the adapter got with:

``` bash
ls -l /dev/serial/by-id/
```

The IQ355+ must stream continuously, with these settings (a reset of the indicator clears them):

``` text
Stream:     EDP
Serial:     9600-8-N-1
Units:      pounds
Mode:       gross
```

`listen.py` expects each reading as `<STX><polarity><7-char weight><unit><G/N><status><CR><LF>` and saves the first stable one after a hang tag is scanned.

To see what the indicator is sending, stop listen (it holds the port), watch the port in hex with minicom, then start listen again:

``` bash
sudo systemctl stop listen-slaughter-measurements
sudo minicom -D /dev/ttyUSB0 -b 9600 -8 -H
sudo systemctl start listen-slaughter-measurements
```

Nothing is recorded while listen is stopped. Quit minicom with Ctrl-A, then X. A good stable reading of 16.0 lb, captured from the scale, looks like:

``` text
02 20 20 20 20 31 36 2e 30 4c 47 20 0d 0a
```

That is STX, a space (positive), `   16.0`, `L` (pounds), `G` (gross), a space (stable; `M` means moving), then CR LF. The scale repeats it continuously.

If minicom shows only `bf`, `fb`, `fe` and similar bytes, the indicator's port, wiring or stream setting is wrong. If nothing appears, turn off Hardware Flow Control in minicom (Ctrl-A, then O, Serial port setup).

## Software Requirements

Install the required packages on Ubuntu:

``` bash
sudo apt update
sudo apt install python3-serial python3-evdev
```

Useful diagnostic tools:

``` bash
sudo apt install evtest minicom
```

## System Time

Measurements are timestamped with the system clock, so accurate time matters. NTP must be active:

``` bash
timedatectl
```

Timestamps are stored in UTC. Pages, reports, and "today" use Indiana time (`America/Indiana/Indianapolis`, see `local_time.py`) regardless of the computer's time zone setting, which needs the `tzdata` package (installed on Ubuntu by default).

Expect `NTP: active` and `System clock synchronized: yes`. If not, enable it:

``` bash
sudo timedatectl set-ntp true
```

## Using the API

Get measurements from the last minute:

``` bash
curl "http://localhost/get?seconds=60"
```

## Simulation Tool

`simulate-slaughter-measurements` is available in the test container for using the keyboard as the data input.

``` bash
docker build -t slaughter-test .
docker run --rm -it --privileged -p 80:80 slaughter-test
docker exec <container name from docker ps> simulate-slaughter-measurements
```

A bare number is treated as a weight; anything else is treated as a scan. Press `Ctrl-C` to stop.

## Running as systemd Services

The capture computer runs `listen.py` and `shout.py` as systemd services so they start automatically when the computer boots and restart if they fail.

The service definitions are stored in the `systemd/` directory:

- `systemd/listen-slaughter-measurements.service`
- `systemd/shout-slaughter-measurements.service`

### Install the Services

From the repository directory:

```sh
sudo cp systemd/listen-slaughter-measurements.service /etc/systemd/system/
sudo cp systemd/shout-slaughter-measurements.service /etc/systemd/system/

sudo systemctl daemon-reload

sudo systemctl enable --now listen-slaughter-measurements.service
sudo systemctl enable --now shout-slaughter-measurements.service

```

### Check Service Status

```sh
sudo systemctl status listen-slaughter-measurements.service
sudo systemctl status shout-slaughter-measurements.service
```

Check whether the services are enabled at boot:

```sh
sudo systemctl is-enabled listen-slaughter-measurements.service
sudo systemctl is-enabled shout-slaughter-measurements.service
```

Both should report `enabled`.

### View Logs

```sh
sudo journalctl -u listen-slaughter-measurements.service
sudo journalctl -u shout-slaughter-measurements.service
```

To show logs from only the current boot:

```sh
sudo journalctl -u listen-slaughter-measurements.service -b
sudo journalctl -u shout-slaughter-measurements.service -b
```

### Restart the Services

```sh
sudo systemctl restart listen-slaughter-measurements.service
sudo systemctl restart shout-slaughter-measurements.service
```

The service files in this repository are the source of truth. If a service configuration is changed, update the repository copy and reinstall it into `/etc/systemd/system/`.
