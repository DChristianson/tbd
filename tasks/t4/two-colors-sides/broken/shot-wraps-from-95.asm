; Broken: the shot goes back to x = 158 after 95, not 96. Must fail the check.

        processor 6502
        include "vcs.h"
NO_ILLEGAL_OPCODES = 1          ; SLEEP pads odd cycles with `bit VSYNC`
        include "macro.h"

SHIP_X_RANGE = 64               ; ship_x: 0..64
SHIP_Y_RANGE = 185              ; ship_y: 0..185
SHOT_LEFT = 95                  ; shot_x: 96..159, back to 158 from 96
SHOT_RIGHT = 158
SHOT_START = 188                ; shot_y: 0..189, back to 188 from 0
SHOT_HEIGHT = 4
SHOT_WIDTH_2 = $10              ; NUSIZ0: a missile 2 clocks wide
WHITE   = $0E                   ; palette.white, NTSC
RED     = $46                   ; palette.red

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

        ; The next line's ENAM0, in a, from y, as in objects-2d: carry is
        ; clear on the missile's lines, where y - ShotY, as a byte, is
        ; under 4. 13 cycles.
        MAC ENAM_VALUE
        tya
        sec
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
        lda #SHOT_RIGHT
        sta ShotX               ; shot_x = 158
        lda #SHOT_START
        sta ShotY               ; shot_y = 188

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
        ldy #0                  ; picture line 0's values, in line 39
        lda (ShipPtr),y
        tax
        ENAM_VALUE
        jmp Picture

        ; Aligned, so that the loop's branch does not cross a page.
        ALIGN 256
Picture:
        ; Picture: lines 40-231. y is the picture line.
        sta WSYNC               ; line 40 (first iteration, peeled)
        stx GRP0                ; 3
        sta ENAM0               ; 6
        lda #0
        sta VBLANK              ; 11
        lda #WHITE
        sta COLUP0              ; 16: white from color clock 48
        iny                     ; 18
        lda (ShipPtr),y         ; 23, or 24 across a page
        tax                     ; 25
        lda #RED                ; 27
        SLEEP 20                ; 47
        sta COLUP0              ; 50: red from color clock 150
        ENAM_VALUE              ; 63
        jmp Next                ; 66
Line:
        sta WSYNC               ; picture lines 1-191
        stx GRP0                ; ends at cycle 3, clock 9
        sta ENAM0               ; 6, clock 18
        lda #WHITE              ; 8
        sta COLUP0              ; 11: white from color clock 33
        iny                     ; 13
        lda (ShipPtr),y         ; 18, or 19 across a page
        tax                     ; 20
        lda #RED                ; 22
        SLEEP 25                ; 47
        sta COLUP0              ; 50: red from color clock 150 (visible 82)
        ENAM_VALUE              ; 63
Next:
        cpy #192                ; 65
        bne Line                ; 68

        ; Overscan: lines 232-261
        sta WSYNC               ; line 232
        lda #%00000010
        sta VBLANK
        STEP ShipX, 1, SHIP_X_RANGE
        STEP ShipY, 1, SHIP_Y_RANGE
        ; if shot_x == 96, shot_x = 158; else shot_x = shot_x - 1.
        ldx ShotX
        cpx #SHOT_LEFT
        bne .Left
        ldx #SHOT_RIGHT + 1
.Left:
        dex
        stx ShotX
        ; if shot_y == 0, shot_y = 188; else shot_y = shot_y - 1.
        ldx ShotY
        bne .Up
        ldx #SHOT_START + 1
.Up:
        dex
        stx ShotY
        ldx #29
OverscanLoop:
        sta WSYNC               ; lines 233-261
        dex
        bne OverscanLoop

        jmp Frame

        ; The player's rows, with 184 zero rows on each side, and one more
        ; after, as in objects-2d.
        ds 184, 0
Ship:
        .byte %00011000         ; ...##...
        .byte %00111100         ; ..####..
        .byte %01111110         ; .######.
        .byte %11111111         ; ########
        .byte %11111111         ; ########
        .byte %01111110         ; .######.
        .byte %00100100         ; ..#..#..
        .byte %01000010         ; .#....#.
        ds 185, 0

        org $FFFC
        .word Reset             ; reset vector
        .word Reset             ; IRQ/BRK vector (unused)
