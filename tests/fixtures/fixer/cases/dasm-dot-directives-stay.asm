    processor 6502
    include "vcs.h"
    org $F000
    .org $F800
    .byte 1
    .word 2
    .ds 1
    .repeat 2
    nop
    .repend
