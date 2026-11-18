"""Checks for a stable NTSC frame showing the word RACER, each row in its own color."""

# Reported, not graded: racer's strict 3-line VSYNC, and when the reference writes its registers.
INFORMATIONAL = {"vsync", "pf0_windows_40", "pf1_windows_40", "pf1_windows_41", "pf2_windows_40", "pf2_windows_41"}
