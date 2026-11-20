; Atari 2600 NTSC Kernel - Solid Blue Background
; Assembler: DASM

include "vcs.h"
include "macro.h"

    org $F800
    ds $0400
    org $FC00

start:
    ; Set background color to blue (NTSC color code $0F)
    lda #$0F
    sta COLUBK
    jmp main_loop

main_loop:
    ; ==========================================
    ; Vertical Blank: 26 lines
    ; ==========================================
    lda #$02
    sta VBLANK
    ldx #26
vblank_loop:
    sta WSYNC
    ; Cycle-accurate delay: 76 cycles per NTSC scanline
    ; sta WSYNC (4) + dex (2) + bne (2) = 8 cycles
    ; Remaining: 68 cycles
    .repeat 68
        nop
    .endrepeat
    dex
    bne vblank_loop

    ; ==========================================
    ; Visible Screen: 192 lines
    ; ==========================================
    lda #$00
    sta VBLANK
    ldx #192
visible_loop:
    sta WSYNC
    .repeat 68
        nop
    .endrepeat
    dex
    bne visible_loop

    ; ==========================================
    ; Overscan: 30 lines
    ; ==========================================
    lda #$02
    sta VBLANK
    ldx #30
overscan_loop:
    sta WSYNC
    .repeat 68
        nop
    .endrepeat
    dex
    bne overscan_loop

    ; Frame complete, loop back for stable 60Hz framerate
    jmp main_loop

    ; ==========================================
    ; Memory Vectors
    ; ==========================================
    org $FFFC
    .word start      ; NMI vector
    .word start      ; Reset vector
    .word start      ; IRQ vector
