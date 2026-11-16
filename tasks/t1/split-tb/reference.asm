; Reference solution: red top half, blue bottom half.
;
;   lines   0-2    VSYNC
;   lines   3-39   vertical blank
;   lines  40-135  picture, red
;   lines 136-231  picture, blue
;   lines 232-261  overscan, blanked
; Overscan is 29 WSYNCs: VERTICAL_SYNC's first WSYNC ends line 261.

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

    lda #$42
    sta COLUBK
    
    ldx #37
_vblank_loop:
    sta WSYNC
    dex
    bne _vblank_loop
    stx VBLANK
    
    ldx #96
_top_loop:
    sta WSYNC
    dex
    bne _top_loop

    lda #$84                ; line 136, still in horizontal blank
    sta COLUBK

    ldx #96
_bottom_loop:
    sta WSYNC
    dex
    bne _bottom_loop

    lda #2
    sta VBLANK              ; line 232: blank overscan, VSYNC and vertical blank

    ldx #29
_overscan_loop:
    sta WSYNC
    dex
    bne _overscan_loop
    jmp NewFrame

    org $FFFA
    .word Reset     ; NMI    (unused on the 2600, but the vector is read)
    .word Reset     ; RESET
    .word Reset     ; IRQ    (unused, no maskable IRQ source is enabled)
