"""Checks for a stable NTSC frame with a white ship and a red shot, each shown on at least 1 frame in 2."""

# Reported, not graded: racer's strict 3-line VSYNC, and when the reference writes its registers.
INFORMATIONAL = {"vsync", "grp0_in_hblank", "enam0_in_hblank"}
