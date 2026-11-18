; Broken: every row's colored cells are $16. Must fail the check.

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
        ; $2A-$2C), where the write itself does something. This leaves
        ; COLUBK black and CTRLPF repeating the playfield. RAM is not
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

        ; Picture: lines 40-231. X is the row, Y the line within it.
        ldx #0
        ldy #16
PictureLoop:
        sta WSYNC               ; lines 40-231
        lda #0                  ; cycles after WSYNC: 2
        sta VBLANK              ; 5 (only line 40 needs it)
        lda PF0Tab,x
        sta PF0                 ; 12
        lda ColorTab,x
        sta COLUPF              ; 19
        lda PF1Tab,x
        sta PF1                 ; 26
        lda PF2Tab,x
        sta PF2                 ; 33
        dey
        bne PictureLoop
        ldy #16
        inx
        cpx #12
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

PF0Tab:
        .byte $F0, $00, $F0, $00, $F0, $00, $F0, $00, $F0, $00, $F0, $00
PF1Tab:
        .byte $83, $7C, $83, $7C, $83, $7C, $83, $7C, $83, $7C, $83, $7C
PF2Tab:
        .byte $07, $F8, $07, $F8, $07, $F8, $07, $F8, $07, $F8, $07, $F8
ColorTab:
        .byte $16, $16, $16, $16, $16, $16, $16, $16, $16, $16, $16, $16

        org $FFFC
        .word Reset             ; reset vector
        .word Reset             ; IRQ/BRK vector (unused)
