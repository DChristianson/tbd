"""Checks for a stable NTSC frame showing a tiled pattern whose right half repeats its left."""

# Reported, not graded: racer's strict 3-line VSYNC, and when the reference writes its registers.
INFORMATIONAL = {"vsync", "pf0_windows_41", "pf1_windows_41", "pf2_windows_41"}
