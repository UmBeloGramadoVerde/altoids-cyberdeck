from __future__ import annotations

LEFT_TOP = "left_top"
LEFT_BOTTOM = "left_bottom"
RIGHT_TOP = "right_top"
RIGHT_BOTTOM = "right_bottom"

SLOTS = (LEFT_TOP, LEFT_BOTTOM, RIGHT_TOP, RIGHT_BOTTOM)

BUTTON_TO_SLOT = {
    True: {
        "A": LEFT_TOP,
        "B": LEFT_BOTTOM,
        "X": RIGHT_TOP,
        "Y": RIGHT_BOTTOM,
    },
    False: {
        "A": RIGHT_BOTTOM,
        "B": RIGHT_TOP,
        "X": LEFT_BOTTOM,
        "Y": LEFT_TOP,
    },
}
