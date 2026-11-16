; Broken: bottom half of the picture is white. Must fail the check.

    processor 6502
NO_ILLEGAL_OPCODES = 1      ; CLEAN_START without lxa (see tbd-dpu.10)
    include "vcs.h"
    include "macro.h"

Color = $80

    SEG CODE
    ORG $F000

Reset:
    CLEAN_START

NewFrame:
    VERTICAL_SYNC

    inc Color
    inc Color
    lda Color
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
    lda #$0E                ; line 136: white, so the frame isn't solid
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
