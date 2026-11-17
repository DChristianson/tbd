; Reference solution: solid red background.
;
;   lines   0-2    VSYNC
;   lines   3-47   vertical blank
;   lines  48-275  picture, red
;   lines 276-311  overscan, blanked
; Overscan is 35 WSYNCs: VERTICAL_SYNC's first WSYNC ends line 311.

    processor 6502
NO_ILLEGAL_OPCODES = 1      ; CLEAN_START without lxa (see tbd-dpu.10)
    include "vcs.h"
    include "macro.h"

    SEG CODE
    ORG $F000

Reset:
    CLEAN_START

NewFrame:
    VERTICAL_SYNC

    lda #$62
    sta COLUBK
    
    ldx #45
_vblank_loop:
    sta WSYNC
    dex
    bne _vblank_loop
    stx VBLANK
    
    ldx #228
_screen_loop:
    sta WSYNC
    dex
    bne _screen_loop

    lda #2
    sta VBLANK              ; line 276: blank overscan, VSYNC and vertical blank

    ldx #35
_overscan_loop:
    sta WSYNC
    dex
    bne _overscan_loop
    jmp NewFrame

    org $FFFA
    .word Reset     ; NMI    (unused on the 2600, but the vector is read)
    .word Reset     ; RESET
    .word Reset     ; IRQ    (unused, no maskable IRQ source is enabled)
