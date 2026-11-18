"""Checks for a stable NTSC frame with three overlapping objects, each shown on at least 1 frame in 2."""

# Reported, not graded: racer's strict 3-line VSYNC, and when the reference writes its registers.
INFORMATIONAL = {"vsync", "grp0_in_hblank", "grp1_in_hblank"}
