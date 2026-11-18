"""Checks for a stable NTSC frame with a white ship on the left and a red shot on the right."""

# Reported, not graded: racer's strict 3-line VSYNC, and when the reference writes its registers.
INFORMATIONAL = {"vsync", "grp0_in_hblank", "enam0_in_hblank", "colup0_switch"}
