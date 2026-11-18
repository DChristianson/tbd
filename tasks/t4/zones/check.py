"""Checks for a stable NTSC frame with three moving objects that never share a line."""

# Reported, not graded: racer's strict 3-line VSYNC, and when the reference writes its registers.
INFORMATIONAL = {"vsync", "grp0_zone1", "grp0_zone2", "grp0_zone3", "resp0_change1", "hmove_change1", "resp0_change2", "hmove_change2"}
