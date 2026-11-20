    processor 6502
    include "vcs.h"
    org $F000
    lda #0
    sta COLUBK
    include "macro.h"
    CLEAN_START
    .include "vcs.h"
