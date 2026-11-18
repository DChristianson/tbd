"""Checks for a stable NTSC frame with two paddles that follow a bouncing ball."""

# Reported, not graded: racer's strict 3-line VSYNC, and when the reference writes its registers.
INFORMATIONAL = {"vsync", "grp0_in_hblank", "grp1_in_hblank", "enabl_in_hblank"}
