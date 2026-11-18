; Broken: the ship goes back to the left edge a frame early. Must fail the check.

        processor 6502
        include "vcs.h"

SHIP_X_RANGE = 152              ; ship_x: 0..153
SHIP_Y_RANGE = 185              ; ship_y: 0..185
WHITE   = $0E

        seg.u vars
        org $80
ShipX   ds 1                    ; visible clock of the player's leftmost pixel
ShipY   ds 1                    ; picture line of the player's first row
ShipPtr ds 2                    ; Ship - ShipY

        seg code
        org $F000

        ; Place object x at visible clock a: on the line after the one in
        ; progress, strobe its RES register and set its HM register for the
        ; HMOVE that follows. The code after it starts the line after that.
        MAC POSITION
        sec
        sta WSYNC
.Divide:
        sbc #15                 ; 2
        bcs .Divide             ; 3, or 2 at the end
        eor #7                  ; 2
        asl                     ; 8
        asl
        asl
        asl
        sta.wx HMP0,x           ; 5
        sta RESP0,x             ; 4: ends at cycle 5k + 18
        sta WSYNC
        ENDM

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
        sta ShipX               ; ship_x = 0
        sta ShipY               ; ship_y = 0

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
        lda #WHITE
        sta COLUP0
        lda #<Ship              ; ShipPtr = Ship - ShipY
        sec
        sbc ShipY
        sta ShipPtr
        lda #>Ship
        sbc #0
        sta ShipPtr+1
        lda ShipX
        ldx #0                  ; player 0
        POSITION                ; line 4
        sta HMOVE               ; line 5: ends at cycle 3
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
        lda (ShipPtr),y         ; the player's row, 0 outside it
        sta GRP0
        iny
        cpy #192
        bne PictureLoop

        ; Overscan: lines 232-261
        sta WSYNC               ; line 232
        lda #%00000010
        sta VBLANK
        lda ShipX               ; ship_x = (ship_x + 1) % 153
        clc
        adc #1
        cmp #SHIP_X_RANGE
        bcc StoreShipX
        sbc #SHIP_X_RANGE
StoreShipX:
        sta ShipX
        lda ShipY               ; ship_y = (ship_y + 1) % 185
        clc
        adc #1
        cmp #SHIP_Y_RANGE
        bcc StoreShipY
        sbc #SHIP_Y_RANGE
StoreShipY:
        sta ShipY
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
