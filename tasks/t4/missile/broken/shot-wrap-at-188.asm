; Broken: the shot's y wraps at 188, not 189. Must fail the check.

        processor 6502
        include "vcs.h"

SHIP_X_RANGE = 153              ; ship_x: 0..153
SHIP_Y_RANGE = 185              ; ship_y: 0..185
SHOT_X_RANGE = 159              ; shot_x: 0..159
SHOT_Y_RANGE = 188              ; shot_y: 0..189
SHOT_HEIGHT = 4
SHOT_WIDTH_2 = $10              ; NUSIZ0: a missile 2 clocks wide
WHITE   = $0E

        seg.u vars
        org $80
ShipX   ds 1                    ; visible clock of the player's leftmost pixel
ShipY   ds 1                    ; picture line of the player's first row
ShotX   ds 1                    ; visible clock of the missile's leftmost pixel
ShotY   ds 1                    ; picture line of the missile's first line
ShipPtr ds 2                    ; Ship - ShipY

        seg code
        org $F000

        ; Place object x at visible clock a, as in objects-2c.
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

        ; Picture line y's values: x = its GRP0 row, and a = its ENAM0.
        MAC ROW_VALUES
        lda (ShipPtr),y         ; the player's row, 0 outside it
        tax
        tya                     ; carry clear on the missile's lines:
        sec                     ; y - ShotY, as a byte, is under 4 only there
        sbc ShotY
        cmp #SHOT_HEIGHT
        lda #0                  ; $FF if carry is clear, 0 if set
        adc #$FF
        ENDM

        ; v = (v + step) % range, for v already in 0..range.
        MAC STEP
        lda {1}
        clc
        adc #{2}
        cmp #{3}
        bcc .Store
        sbc #{3}
.Store:
        sta {1}
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
        lda #100
        sta ShotX               ; shot_x = 100
        lda #20
        sta ShotY               ; shot_y = 20

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
        lda #SHOT_WIDTH_2
        sta NUSIZ0
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
        lda ShotX               ; line 5
        clc
        adc #1
        ldx #RESM0 - RESP0      ; missile 0
        POSITION                ; line 6
        sta HMOVE               ; line 7: ends at cycle 3
        ldx #32
VBlankLoop:
        sta WSYNC               ; lines 8-39
        dex
        bne VBlankLoop
        ldy #0
        ROW_VALUES              ; for picture line 0, in line 39

        ; Picture: lines 40-231. y is the picture line.
        sta WSYNC               ; line 40
        stx GRP0
        sta ENAM0
        lda #0
        sta VBLANK
        beq NextRow             ; always
PictureLoop:
        sta WSYNC               ; lines 41-231
        stx GRP0                ; ends at cycle 3, clock 9
        sta ENAM0               ; ends at cycle 6, clock 18
NextRow:
        iny
        ROW_VALUES              ; for the next line
        cpy #192
        bne PictureLoop

        ; Overscan: lines 232-261
        sta WSYNC               ; line 232
        lda #%00000010
        sta VBLANK
        STEP ShipX, 1, SHIP_X_RANGE
        STEP ShipY, 1, SHIP_Y_RANGE
        STEP ShotX, 2, SHOT_X_RANGE
        STEP ShotY, 3, SHOT_Y_RANGE
        ldx #29
OverscanLoop:
        sta WSYNC               ; lines 233-261
        dex
        bne OverscanLoop

        jmp Frame

        ; The player's rows, with 184 zero rows on each side, and one more
        ; after: enough for a read on any picture line, wherever in the
        ; picture the player is, and for the kernel's read past the last.
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
        ds 185, 0

        org $FFFC
        .word Reset             ; reset vector
        .word Reset             ; IRQ/BRK vector (unused)
