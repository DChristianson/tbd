    processor 6502
    include "vcs.h"
    include "macro.h"

    org $200

start:
    jmp main

main:
    ; Vertical Sync: 3 scanlines
    ldy #3
vsync_loop:
    lda #2
    sta VSYNC
    sta VBLANK
    sta WSYNC
    dey
    bne vsync_loop

    ; Vertical Blank: 34 more scanlines (37 total with VSYNC)
    lda #0
    sta VSYNC
    lda #$42        ; Red background (hue 4, luminance 2)
    sta COLUBK
    ldy #34
vblank_loop:
    sta WSYNC
    dey
    bne vblank_loop

    ; End Vertical Blank
    lda #0
    sta VBLANK

    ; Visible area: 189 scanlines
    ldy #189
visible_loop:
    sta WSYNC
    dey
    bne visible_loop

    ; Overscan: 33 scanlines
    ldy #33
overscan_loop:
    sta WSYNC
    dey
    bne overscan_loop

    ; Frame complete, loop back for stable framerate
    jmp main

    org $FFFA
    .word 0            ; NMI vector
    .word start        ; RESET vector
    .word 0            ; IRQ vector
