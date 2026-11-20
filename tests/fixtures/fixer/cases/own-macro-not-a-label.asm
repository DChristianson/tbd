    processor 6502
    include "vcs.h"
    org $F000
    MAC WAIT
    sta WSYNC
    ENDM
    WAIT
    jmp WAIT
