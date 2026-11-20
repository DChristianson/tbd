    processor 6502
    include "vcs.h"
    org $F000
    asl
    LSR ; halve
    rol
    ror
Lbl rol
