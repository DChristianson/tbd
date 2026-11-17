; Broken: no red bar (the canonical reference). Must fail the check.

        processor 6502
        include "vcs.h"

        seg code
        org $F000

Reset:
        sei                     ; CPU setup
        cld
        ldx #$FF
        txs
        ; Clear the TIA's write registers, which power up with unknown
        ; values (BOARD-POWERON-1), except the strobes ($02, $03, $10-$14,
        ; $2A-$2C), where the write itself does something. RAM is not
        ; cleared.
        lda #0
        sta VSYNC
        sta VBLANK
        ldx #$0F - $04
ClearTIA1:
        sta $04,x               ; NUSIZ0 through PF2
        dex
        bpl ClearTIA1
        ldx #$29 - $15
ClearTIA2:
        sta $15,x               ; AUDC0 through RESMP1
        dex
        bpl ClearTIA2

Frame:
        ; Vertical sync: lines 0-2
        sta WSYNC               ; line 0
        lda #%00000010
        sta VSYNC
        sta WSYNC               ; line 1
        sta WSYNC               ; line 2

        ; Vertical blank: lines 3-39
        sta WSYNC               ; line 3
        lda #0
        sta VSYNC
        ldx #36
VBlankLoop:
        sta WSYNC               ; lines 4-39
        dex
        bne VBlankLoop

        ; Picture: lines 40-231. x is the color, starting at 0 each frame.
        ldx #0
        sta WSYNC               ; line 40 (first iteration, peeled)
        lda #0
        sta VBLANK
        stx COLUBK
        inx
        inx
        ldy #191
PictureLoop:
        sta WSYNC               ; lines 41-231
        stx COLUBK
        inx
        inx
        dey
        bne PictureLoop

        ; Overscan: lines 232-261
        sta WSYNC               ; line 232
        lda #%00000010
        sta VBLANK
        ldx #29
OverscanLoop:
        sta WSYNC               ; lines 233-261
        dex
        bne OverscanLoop

        jmp Frame

        org $FFFC
        .word Reset             ; reset vector
        .word Reset             ; IRQ/BRK vector (unused)
