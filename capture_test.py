import serial
from evdev import InputDevice, ecodes
import select

SCANNER_DEVICE = "/dev/input/event14"
SCALE_DEVICE = "/dev/ttyS0"

# -------------------------
# Connect to devices
# -------------------------

scanner = InputDevice(SCANNER_DEVICE)

scale = serial.Serial(
    port=SCALE_DEVICE,
    baudrate=9600,
    bytesize=8,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=0
)

# Prevent scanner keystrokes from also being typed
# into the terminal / other applications.
scanner.grab()

print(f"Scanner: {scanner.name}")
print(f"Scale:   {SCALE_DEVICE}")
print()
print("READY - Scan an animal.")

# -------------------------
# Barcode character mapping
# -------------------------

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


# -------------------------
# Main loop
# -------------------------

try:

    while True:

        readable, _, _ = select.select(
            [scanner.fd, scale.fileno()],
            [],
            [],
            1
        )

        # -------------------------
        # Scanner input
        # -------------------------

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

        # -------------------------
        # Scale input
        # -------------------------

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

            # -------------------------
            # Successful capture!
            # -------------------------

            print()
            print("==============================")
            print(
                f"CAPTURED: "
                f"{current_animal_id},{weight:g}"
            )
            print("==============================")
            print()

            # Require another scan before another
            # weight can be accepted.
            current_animal_id = None

            print("READY - Scan next animal.")


except KeyboardInterrupt:

    print("\nStopping...")


finally:

    scanner.ungrab()
    scale.close()