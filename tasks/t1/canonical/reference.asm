; Task 1 (canonical) reference: a stable NTSC frame with a
; solid red background.
;
; Frame, with line 0 the line VSYNC turns on (racer's numbering):
;   lines   0-2    VSYNC
;   lines   3-39   vertical blank
;   lines  40-231  picture, red
;   lines 232-261  overscan, blanked
; 262 lines. VERTICAL_SYNC's first WSYNC ends line 261, so overscan counts 29
; WSYNCs, not 30 (30 made 263 lines).

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
    
    ldx #192
_screen_loop:
    sta WSYNC
    dex
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
