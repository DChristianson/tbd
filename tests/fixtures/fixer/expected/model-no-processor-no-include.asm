    processor 6502
    include "vcs.h"
    .org $F000        ; Start of the program

Start:
    SEI             ; Disable interrupts
    CLD             ; Clear decimal mode
    LDX #$FF
    TXS             ; Set stack pointer

; Initialize TIA registers
InitVCS:
    lda #$00
    sta VSYNC       ; Disable vertical sync
    sta VBLANK      ; Disable vertical blank
    sta WSYNC       ; Wait for horizontal sync
    sta VBLANK      ; Enable vertical blank

    lda #$1E        ; Set background color to red
    sta COLUBK

    lda #$1E        ; Set playfield color to red
    sta COLUPF

; Main loop
MainLoop:
    lda #$1E        ; Set background color to red
    sta COLUBK

    lda #$1E        ; Set playfield color to red
    sta COLUPF

    ; Vertical sync routine
    lda #$02
    sta VSYNC       ; Enable vertical sync
    sta WSYNC       ; Wait for horizontal sync
    sta WSYNC       ; Wait for horizontal sync
    sta WSYNC       ; Wait for horizontal sync
    lda #$00
    sta VSYNC       ; Disable vertical sync

    ; Vertical blank routine
    lda #$02
    sta VBLANK      ; Enable vertical blank
    ldx #$1E
VBlankLoop:
    sta WSYNC       ; Wait for horizontal sync
    dex
    bne VBlankLoop

    lda #$00
    sta VBLANK      ; Disable vertical blank

    ; Display area
    ldx #$1E
ScanlineLoop:
    sta WSYNC       ; Wait for horizontal sync
    dex
    bne ScanlineLoop

    jmp MainLoop    ; Repeat the main loop

    .org $FFFA      ; Vector table
    .word Start     ; NMI vector
    .word Start     ; Reset vector
    .word Start     ; IRQ vector

    .org $FFFC      ; End of program
