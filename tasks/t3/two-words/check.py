"""Checks for a stable NTSC frame showing RA in red and CE in blue."""

# Reported, not graded: racer's strict 3-line VSYNC, and when the reference writes its registers.
INFORMATIONAL = {"vsync", "pf0_windows_40", "pf0_windows_41", "pf1_windows_40", "pf1_windows_41", "pf2_windows_40", "pf2_windows_41"}
