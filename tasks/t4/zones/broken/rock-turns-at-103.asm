; Broken: the rock turns back up at y = 103, not 104. Must fail the check.

        processor 6502
        include "vcs.h"

SHIP_X_RANGE = 153              ; ship_x: 0..153
SHIP_Y = 16                     ; the ship's picture line
ROCK_X = 76                     ; the rock's visible clock
ROCK_TOP = 56                   ; rock_y: 56..105, turned back at 56 and 104
ROCK_BOTTOM = 103
UFO_X_RANGE = 153               ; ufo_x: 0..153
UFO_TOP = 144                   ; ufo_y: 144..177, turned back at 144 and 176
UFO_BOTTOM = 176
HEIGHT  = 8

ZONE1_END = 24                  ; the zones' first and end picture lines
ZONE2_TOP = 27
ZONE2_END = 112
ZONE3_TOP = 115
ZONE3_END = 192

WHITE   = $0E                   ; palette.white, NTSC
YELLOW  = $1E                   ; palette.yellow
GREEN   = $C4                   ; palette.green

        seg.u vars
        org $80
ShipX   ds 1                    ; visible clock of the ship's leftmost pixel
RockY   ds 1                    ; picture line of the rock's first row
RockDY  ds 1                    ; 1 or -1 ($FF)
UfoX    ds 1
UfoY    ds 1
UfoDY   ds 1
RowPtr  ds 2                    ; the zone's image - its y

        seg code
        org $F000

        ; Place object x at visible clock a, as in objects-2c. Takes the
        ; rest of this line and the next.
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

        ; Draw picture lines y to {1} - 1, with x the row for line y,
        ; reading rows through RowPtr. Ends on line {1} - 1, with x the row
        ; for line {1}.
        MAC ZONE
.Draw:
        sta WSYNC
        stx GRP0                ; ends at cycle 3, clock 9
        iny
        lda (RowPtr),y          ; the row for the next line, 0 outside
        tax
        cpy #{1}
        bne .Draw
        ENDM

        ; Change zones: on this line's end, GRP0 = 0 and the color {1};
        ; RowPtr = {2} - {3}; the player placed at {4} (an operand of lda);
        ; and, on the HMOVE line, x = the row for line {5}, with y = {5}.
        MAC CHANGE
        sta WSYNC               ; line A
        lda #0
        sta GRP0
        lda #{1}
        sta COLUP0
        lda #<{2}
        sec
        sbc {3}
        sta RowPtr
        lda #>{2}
        sbc #0
        sta RowPtr+1
        lda {4}
        ldx #0                  ; player 0
        POSITION                ; line A + 1
        sta HMOVE               ; line A + 2: ends at cycle 3
        ldy #{5}
        lda (RowPtr),y
        tax
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

        ; The sketch's bounce: if v == top and dv < 0, dv = 1; else if
        ; v == bottom and dv > 0, dv = -1. Then v = v + dv.
        MAC BOUNCE
        lda {2}
        ldx {1}
        cpx #{3}
        bne .Bottom
        tay
        bpl .Move               ; dv >= 0 at the top: unchanged
        lda #1
        bne .Store              ; always
.Bottom:
        cpx #{4}
        bne .Move
        tay
        bmi .Move               ; dv < 0 at the bottom: unchanged
        beq .Move
        lda #$FF
.Store:
        sta {2}
.Move:
        clc
        adc {1}
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
        lda #ROCK_TOP
        sta RockY               ; rock_y = 56
        lda #1
        sta RockDY              ; rock_dy = 1
        lda #100
        sta UfoX                ; ufo_x = 100
        lda #UFO_BOTTOM
        sta UfoY                ; ufo_y = 176
        lda #$FF
        sta UfoDY               ; ufo_dy = -1

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
        lda ShipX
        ldx #0                  ; player 0
        POSITION                ; line 4
        sta HMOVE               ; line 5: ends at cycle 3
        ldx #34
VBlankLoop:
        sta WSYNC               ; lines 6-39
        dex
        bne VBlankLoop
        ldy #0
        lda Ship - SHIP_Y,y     ; picture line 0's row, in line 39
        tax

        ; Picture: lines 40-231. y is the picture line.
        ; Zone 1, the ship: picture lines 0-23, and 24's GRP0 = 0.
        sta WSYNC               ; line 40
        stx GRP0
        lda #0
        sta VBLANK
        beq Zone1Next           ; always
Zone1:
        sta WSYNC               ; picture lines 1-23
        stx GRP0                ; ends at cycle 3, clock 9
Zone1Next:
        iny
        lda Ship - SHIP_Y,y     ; the ship's y is a constant
        tax
        cpy #ZONE1_END
        bne Zone1

        ; Picture lines 24-26: change to the rock. Zone 2: 27-111.
        CHANGE YELLOW, Rock, RockY, #ROCK_X, ZONE2_TOP
        ZONE ZONE2_END

        ; Picture lines 112-114: change to the UFO. Zone 3: 115-191.
        CHANGE GREEN, Ufo, UfoY, UfoX, ZONE3_TOP
        ZONE ZONE3_END

        ; Overscan: lines 232-261
        sta WSYNC               ; line 232
        lda #%00000010
        sta VBLANK
        STEP ShipX, 1, SHIP_X_RANGE
        BOUNCE RockY, RockDY, ROCK_TOP, ROCK_BOTTOM
        sta WSYNC               ; line 233
        STEP UfoX, 2, UFO_X_RANGE
        BOUNCE UfoY, UfoDY, UFO_TOP, UFO_BOTTOM
        ldx #28
OverscanLoop:
        sta WSYNC               ; lines 234-261
        dex
        bne OverscanLoop

        jmp Frame

        ; Each image with the zero rows its zone reads around it: from the
        ; zone's first line less the largest y, to its end line less the
        ; smallest.
        ds SHIP_Y, 0            ; lines 0..24, y = 16
Ship:
        .byte %00011000         ; ...##...
        .byte %00111100         ; ..####..
        .byte %01111110         ; .######.
        .byte %11111111         ; ########
        .byte %11111111         ; ########
        .byte %01111110         ; .######.
        .byte %00100100         ; ..#..#..
        .byte %01000010         ; .#....#.
        ds ZONE1_END - SHIP_Y - HEIGHT + 1, 0

        ds ROCK_BOTTOM - ZONE2_TOP, 0
Rock:
        .byte %00111100         ; ..####..
        .byte %01111110         ; .######.
        .byte %11011111         ; ##.#####
        .byte %11111111         ; ########
        .byte %11111011         ; #####.##
        .byte %11111111         ; ########
        .byte %01111110         ; .######.
        .byte %00111100         ; ..####..
        ds ZONE2_END - ROCK_TOP - HEIGHT + 1, 0

        ds UFO_BOTTOM - ZONE3_TOP, 0
Ufo:
        .byte %00011000         ; ...##...
        .byte %00111100         ; ..####..
        .byte %11111111         ; ########
        .byte %11011011         ; ##.##.##
        .byte %11111111         ; ########
        .byte %01111110         ; .######.
        .byte %00100100         ; ..#..#..
        .byte %01000010         ; .#....#.
        ds ZONE3_END - UFO_TOP - HEIGHT + 1, 0

        org $FFFC
        .word Reset             ; reset vector
        .word Reset             ; IRQ/BRK vector (unused)
