# Measure the Slaughter Floor with Computers

Configuring a computer that currently has only one job: reliably collecting and preserving animal ID/weight events.

`listen.py` starts measurement recording

`shout.py` starts a simple http server to share the measurements
- http://localhost/test displays the table
- http://localhost/get?seconds=N is an api which accepts an integer number of seconds, and returns json for all measurements taken in the most recent N seconds

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

### Scale Indicator

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
