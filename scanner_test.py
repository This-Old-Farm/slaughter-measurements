from evdev import InputDevice, ecodes

SCANNER_DEVICE = "/dev/input/event14"

scanner = InputDevice(SCANNER_DEVICE)

print(f"Listening to scanner: {scanner.name}")
print("Scan a barcode...")
print("Press Ctrl+C to stop.")

barcode = ""

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


for event in scanner.read_loop():

    # Ignore everything except key presses
    if event.type != ecodes.EV_KEY:
        continue

    key_event = ecodes.KEY[event.code]

    # value 1 = key pressed
    # value 0 = released
    # value 2 = held/repeated
    if event.value != 1:
        continue

    if key_event == "KEY_ENTER":
        if barcode:
            print(f"SCANNED: {barcode}")
            barcode = ""
        continue

    character = key_map.get(key_event)

    if character:
        barcode += character