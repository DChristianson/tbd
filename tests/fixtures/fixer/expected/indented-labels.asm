    processor 6502
    include "vcs.h"
    org $F000
Start:
    lda #0
Loop: dex
    bne Loop
main_loop
    jmp main_loop
    VERTICAL_BLANK
