"""Checks for a stable NTSC frame with two ships and two shots, all moving."""

# Reported, not graded: racer's strict 3-line VSYNC, and when the reference writes its registers.
INFORMATIONAL = {"vsync", "grp0_in_hblank", "grp1_in_hblank", "enam0_in_hblank", "enam1_in_hblank"}
