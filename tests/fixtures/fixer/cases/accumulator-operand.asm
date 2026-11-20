    processor 6502
    include "vcs.h"
    org $F000
    asl A
    LSR a ; halve
    rol
    ror A
Lbl rol a
