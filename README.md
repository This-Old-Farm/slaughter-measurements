# Slaughter Floor Weight Capture Prototype

## Purpose

This project is the first proof of concept for digitizing
slaughter-floor weight collection.

The immediate goal is to combine two independent inputs:

1.  A barcode/QR scanner that provides an animal ID.
2.  An MSI-8000HD indicator that provides the animal's weight over
    RS-232.

The program pairs those inputs and produces a record in this format:

``` text
BEEF010,1348
```

This prototype intentionally does **not** yet handle Google Sheets,
databases, HTTP APIs, billing, cut sheets, or other production
integrations.

## Current Hardware Setup

### Barcode Scanner

The barcode scanner is connected by USB and operates as a USB HID
keyboard device.

It has been successfully tested under Linux.

Example scan:

``` text
BEEF010
```

Linux exposes the scanner as an input device. During initial testing it
was:

``` text
/dev/input/event14
```

**Important:** `/dev/input/event14` is not guaranteed to remain the same
after a reboot. A persistent `/dev/input/by-id/` path should be used
before production deployment.

The scanner was successfully tested with:

``` bash
sudo evtest /dev/input/event14
```

Python can read the scanner directly using the `evdev` library.

### Scale Indicator

The scale indicator is an MSI-8000HD connected directly to the laptop's
built-in RS-232 serial port.

Linux exposes the serial port as:

``` text
/dev/ttyS0
```

The serial connection has been successfully tested.

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

The MSI-8000HD has successfully transmitted weight data to Linux.

Example output:

``` text
1348
```

Continuous mode was used during initial testing. The intended production
workflow assumes LOAD mode can transmit a single weight when a load
becomes stable and require the scale to return near zero before another
load event.

## Software Requirements

The prototype uses Python 3.

Install the required packages on Ubuntu:

``` bash
sudo apt update
sudo apt install python3-serial python3-evdev
```

Useful diagnostic tools:

``` bash
sudo apt install evtest minicom
```

## Testing the Scanner

To identify input devices:

``` bash
sudo evtest
```

To test the scanner directly:

``` bash
sudo evtest /dev/input/event14
```

Scanning a barcode should generate `EV_KEY` events followed by Enter.

The Python scanner test should reconstruct those events into a complete
barcode such as:

``` text
SCANNED: BEEF010
```

## Testing the Scale

The built-in RS-232 port is:

``` text
/dev/ttyS0
```

A simple serial test can be performed with:

``` bash
sudo minicom -D /dev/ttyS0 -b 9600
```

When the MSI transmits a weight, a value such as the following should
appear:

``` text
1348
```
### **❗ IMPORTANT**
> [!IMPORTANT]
> ## Before Running the Prototype
>
> Exit minicom before running the Python capture application so that both programs do not attempt to use the serial port simultaneously.


## Combined Capture Program

Create the program:

``` bash
nano capture_test.py
```

Use the following code:

``` python
import serial
from evdev import InputDevice, ecodes
import select

SCANNER_DEVICE = "/dev/input/event14"
SCALE_DEVICE = "/dev/ttyS0"

scanner = InputDevice(SCANNER_DEVICE)

scale = serial.Serial(
    port=SCALE_DEVICE,
    baudrate=9600,
    bytesize=8,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=0
)

# Prevent scanner input from also being typed into other applications.
scanner.grab()

print(f"Scanner: {scanner.name}")
print(f"Scale:   {SCALE_DEVICE}")
print()
print("READY - Scan an animal.")

key_map = {
    "KEY_0": "0",
    "KEY_1": "1",
    "KEY_2": "2",
    "KEY_3": "3",
    "KEY_4": "4",
    "KEY_5": "5",
    "KEY_6": "6",
    "KEY_7": "7",
    "KEY_8": "8",
    "KEY_9": "9",
    "KEY_A": "A",
    "KEY_B": "B",
    "KEY_C": "C",
    "KEY_D": "D",
    "KEY_E": "E",
    "KEY_F": "F",
    "KEY_G": "G",
    "KEY_H": "H",
    "KEY_I": "I",
    "KEY_J": "J",
    "KEY_K": "K",
    "KEY_L": "L",
    "KEY_M": "M",
    "KEY_N": "N",
    "KEY_O": "O",
    "KEY_P": "P",
    "KEY_Q": "Q",
    "KEY_R": "R",
    "KEY_S": "S",
    "KEY_T": "T",
    "KEY_U": "U",
    "KEY_V": "V",
    "KEY_W": "W",
    "KEY_X": "X",
    "KEY_Y": "Y",
    "KEY_Z": "Z",
    "KEY_MINUS": "-",
}

barcode_buffer = ""
current_animal_id = None

try:
    while True:
        readable, _, _ = select.select(
            [scanner.fd, scale.fileno()],
            [],
            [],
            1
        )

        if scanner.fd in readable:
            for event in scanner.read():
                if event.type != ecodes.EV_KEY:
                    continue

                if event.value != 1:
                    continue

                key = ecodes.KEY[event.code]

                if key == "KEY_ENTER":
                    if barcode_buffer:
                        current_animal_id = barcode_buffer

                        print()
                        print(f"SCANNED: {current_animal_id}")
                        print("Waiting for weight...")

                        barcode_buffer = ""

                    continue

                character = key_map.get(key)

                if character:
                    barcode_buffer += character

        if scale.fileno() in readable:
            raw = scale.readline()

            if not raw:
                continue

            try:
                weight_text = raw.decode("ascii").strip()
                weight = float(weight_text)
            except (UnicodeDecodeError, ValueError):
                print(f"WARNING: Invalid scale data: {raw!r}")
                continue

            if current_animal_id is None:
                print(
                    f"WARNING: Weight {weight:g} received "
                    "without an animal ID."
                )
                continue

            print()
            print("==============================")
            print(f"CAPTURED: {current_animal_id},{weight:g}")
            print("==============================")
            print()

            current_animal_id = None

            print("READY - Scan next animal.")

except KeyboardInterrupt:
    print("\nStopping...")

finally:
    scanner.ungrab()
    scale.close()
```

Save the file in nano with:

``` text
Ctrl+O
Enter
Ctrl+X
```

Run it with:

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

## Basic State Machine

``` text
WAITING FOR ID
      |
      | scan BEEF010
      v
WAITING FOR WEIGHT
      |
      | receive 1348
      v
CAPTURE
BEEF010,1348
      |
      v
WAITING FOR ID
```

If a weight arrives while there is no current animal ID, the program
should warn instead of creating a valid record:

``` text
WARNING: Weight 1348 received without an animal ID.
```

## Prototype Acceptance Test

Before adding networking or storage, test the pairing logic repeatedly.

Example:

``` text
BEEF010 -> 1348
BEEF085 -> 1276
BEEF042 -> 1411
BEEF063 -> 1298
```

Expected output:

``` text
CAPTURED: BEEF010,1348
CAPTURED: BEEF085,1276
CAPTURED: BEEF042,1411
CAPTURED: BEEF063,1298
```

The initial reliability target is:

**100 successful ID/weight pairs with zero mismatches, missing captures,
or unexpected duplicates.**

## Known Prototype Limitations

This is not production-ready yet.

Known limitations include:

-   Scanner device path is hardcoded as `/dev/input/event14`.
-   Captured records are not saved to disk.
-   There is no timestamp or event ID.
-   There is no network/API interface.
-   There is no retry/synchronization system.
-   There is no central database.
-   LOAD mode behavior still needs to be validated under actual
    operating conditions.
-   Error recovery and operator correction workflows are not complete.
-   The system has not yet been tested under slaughter-floor
    environmental conditions.

## Planned Next Steps

After reliable `ID,weight` capture is proven:

1.  Replace the scanner's `event14` path with a persistent
    `/dev/input/by-id/` device path.
2.  Add timestamps to every capture.
3.  Generate a unique event ID for every capture.
4.  Save each record locally before attempting network transmission.
5.  Use a local SQLite database or similar durable queue.
6.  Add an HTTP API for another computer/service to retrieve captured
    records.
7.  Mark records as synchronized after successful retrieval.
8.  Test operation during a network outage.
9.  Configure the capture program to start automatically when Linux
    boots.
10. Run the capture computer headlessly without a keyboard or monitor.
11. Connect the central system to Google Sheets or another temporary
    operational data store.
12. Later cross-reference animal IDs with drop-off, cut-sheet,
    reporting, and billing data.

## Intended Architecture

``` text
Barcode Scanner ----\
                     \
                      > Slaughter Floor Capture Computer
                     /          |
MSI-8000HD ---------/           |
                                | local durable storage
                                |
                                v
                          Network / HTTP API
                                |
                                v
                         Central Application
                                |
                +---------------+---------------+
                |               |               |
                v               v               v
           Google Sheets    Cut Sheets       Reporting
                                                |
                                                v
                                             Billing
```

The slaughter-floor capture computer should remain focused on one job:
reliably collecting and preserving animal ID/weight events.

The central system can then fetch, organize, cross-reference, calculate,
report, and act on those records.
