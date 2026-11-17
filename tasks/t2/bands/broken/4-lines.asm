; Broken: stripes 4 lines tall, not 8. Must fail the check.

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
    and #$FC
    asl
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
