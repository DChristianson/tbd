; Broken: the ship is right, but the background is dark gray ($02), not black. Must fail the check.

        processor 6502
        include "vcs.h"
NO_ILLEGAL_OPCODES = 1          ; SLEEP pads odd cycles with `bit VSYNC`
        include "macro.h"

SHIP_X  = 76                    ; visible clock of the player's leftmost pixel
SHIP_Y  = 92                    ; picture line of its first row
WHITE   = $0E

        seg code
        org $F000

Reset:
        sei                     ; CPU setup
        cld
        ldx #$FF
        txs
        ; Clear the TIA's write registers, as sweep-1c does.
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
        lda #$02
        sta COLUBK
        lda #WHITE
        sta COLUP0
        lda #$20                ; 2 clocks left
        sta HMP0
        sta WSYNC               ; line 4
        SLEEP 44
        sta RESP0               ; ends at cycle 47, clock 141
        sta WSYNC               ; line 5
        sta HMOVE               ; ends at cycle 3
        ldx #34
VBlankLoop:
        sta WSYNC               ; lines 6-39
        dex
        bne VBlankLoop

        ; Picture: lines 40-231. y is the picture line.
        ldy #0
        sta WSYNC               ; line 40
        lda #0
        sta VBLANK
        beq PictureRow          ; always
PictureLoop:
        sta WSYNC               ; lines 41-231
PictureRow:
        lda Ship - SHIP_Y,y     ; the player's row, 0 outside it
        sta GRP0
        iny
        cpy #192
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

        ; The player's rows, with 184 zero rows on each side: enough for a
        ; read on any picture line, wherever in the picture the player is.
        ds 184, 0
Ship:
        .byte %00011000
        .byte %00111100
        .byte %01111110
        .byte %11111111
        .byte %11111111
        .byte %01111110
        .byte %00100100
        .byte %01000010
        ds 184, 0

        org $FFFC
        .word Reset             ; reset vector
        .word Reset             ; IRQ/BRK vector (unused)
