    processor 6502
    include "vcs.h"
    org $F000
    .word Reset
    .word Reset, Reset
    .byte $01,$02
Table .byte 1
    ds 4
    ds 4
    repeat 3
    repend
    repend
    org $F000
    .byte $01
