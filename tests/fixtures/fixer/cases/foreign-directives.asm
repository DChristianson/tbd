    processor 6502
    include "vcs.h"
    org $F000
    dw Reset
    .dw Reset, Reset
    db $01,$02
Table .db 1
    .res 4
    res 4
    .rept 3
    .endrepeat
    .endr
* = $F000
db $01
