; Reference solution: picture line n shows color (n / 8) * $10, mod 256.
;
;   lines   0-2    VSYNC
;   lines   3-39   vertical blank
;   lines  40-231  picture: Y counts 0-191, COLUBK = (Y & $F8) * 2
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

    ldx #37
_vblank_loop:
    sta WSYNC
    dex
    bne _vblank_loop

    stx VBLANK
    ldy #0
_screen_loop:
    tya
    and #$F8
    asl
    sta COLUBK              ; ends at cycle 16, color clock 48
    sta WSYNC
    iny
    cpy #192
    bne _screen_loop

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
