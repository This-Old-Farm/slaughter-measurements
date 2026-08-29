# Measure the Slaughter Floor with Computers

Configuring a computer that currently has only one job: reliably collecting and preserving animal ID/weight events.

## Current Hardware Setup

### Barcode Scanner

TODO add link to tech manual

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

TODO add link to manual in google-drive

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

## Combined Capture Program

Run the prototype:

``` bash
sudo python3 capture_test.py
```

## Expected Workflow

The program begins in a state where it is waiting for an animal ID.

``` text
READY - Scan an animal.
```

Scan:

``` text
BEEF010
```

The program should display:

``` text
SCANNED: BEEF010
Waiting for weight...
```

When the scale sends:

``` text
1348
```

the program should produce:

``` text
==============================
CAPTURED: BEEF010,1348
==============================

READY - Scan next animal.
```

The current animal ID is then cleared so that another weight cannot
accidentally be assigned to the same animal.

## Known Prototype Limitations

This is not production-ready yet.

Known limitations include:

-   Scanner device path is hardcoded as `/dev/input/event14`.
-   Captured records are not saved to disk.
-   There is no timestamp or event ID.
-   There is no network/API interface.
-   There is no retry/synchronization system.
-   LOAD/CONT/USER mode behavior still needs to be validated under actual operating conditions.
-   Error recovery and operator correction workflows are not complete.
-   The system has not yet been tested under slaughter-floor environmental conditions.

## Planned Next Steps

After reliable `ID,weight` capture is proven:

1.  Replace the scanner's `event14` path with a persistent `/dev/input/by-id/` device path.
2.  Add timestamps to every capture.
5.  Implement propper logging and sqlite storage.
6.  Add an HTTP API for another computer/service to retrieve captured records.
7.  Mark records as synchronized after successful retrieval.
8.  Test operation during a network outage.
9.  Configure the capture program to start automatically when Linux boots.
10. Run the capture computer headlessly without a keyboard or monitor.
