; Reference solution: a player and a missile in different colors,
; sharing COLUP0, in bands that never meet.
; Imported from racer's reference/objects-2i-zones.asm (see tasks/t4/NOTICE.md).
; The comments below are racer's, and use racer's names for its files and
; documents.
;
; Expected behavior:
;   - Every frame after the first is 262 lines, with VSYNC on for lines 0-2
;     and the output blanked outside lines 40-231, as in frame-0a.
;   - The background is black. On the main loop's frame f (counting from 0):
;     the ship, white ($0E), 2G's image, at visible clock f mod 153 and
;     picture line ship_y; and the shot, red ($46), 2 clocks wide and 4
;     lines tall, at visible clock (100 + 3f) mod 159 and picture line
;     shot_y. ship_y starts at 0 moving down, and turns back at 0 and 72,
;     so the ship is on picture lines 0-79 at most; shot_y starts at 188,
;     falls by 1 a frame, and goes back to 188 from 100, so the shot is on
;     lines 100-191.
;   - On every picture line, GRP0 and ENAM0 are written before color
;     clock 68; COLUP0 is written white in vertical blank, and red on
;     picture line 80, before color clock 68.
;
; This is objects-2d with the shot's color: both objects are placed in
; vertical blank, as there, and drawn by its kernel, in two loops: picture
; lines 0-79, in white, and 80-191, after COLUP0 is rewritten at the start
; of line 80, the first that neither band can reach before the shot's. The
; change takes no line of its own.
;
; Cycles from WSYNC, each line's own WSYNC included: 37 on a picture line,
; so 39 left; 45 on line 80, so 31 left.

        processor 6502
        include "vcs.h"

SHIP_X_RANGE = 153              ; ship_x: 0..153
SHIP_TOP = 0                    ; ship_y: 0..73, turned back at 0 and 72
SHIP_BOTTOM = 72
SHOT_X_RANGE = 159              ; shot_x: 0..159
SHOT_TOP = 100                  ; shot_y: 100..189, back to 188 from 100
SHOT_START = 188
SHOT_HEIGHT = 4
SHOT_WIDTH_2 = $10              ; NUSIZ0: a missile 2 clocks wide
CHANGE  = 80                    ; the picture line COLUP0 turns red
WHITE   = $0E                   ; palette.white, NTSC
RED     = $46                   ; palette.red

        seg.u vars
        org $80
ShipX   ds 1                    ; visible clock of the player's leftmost pixel
ShipY   ds 1                    ; picture line of the player's first row
ShipDY  ds 1                    ; 1 or -1 ($FF)
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

        ; Picture line y's values: x = its GRP0 row, and a = its ENAM0, as
        ; in objects-2d.
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
        lda #1
        sta ShipDY              ; ship_dy = 1
        lda #100
        sta ShotX               ; shot_x = 100
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
        beq ShipNext            ; always
ShipZone:
        sta WSYNC               ; picture lines 1-79
        stx GRP0                ; ends at cycle 3, clock 9
        sta ENAM0               ; ends at cycle 6, clock 18
ShipNext:
        iny
        ROW_VALUES              ; for the next line
        cpy #CHANGE
        bne ShipZone

        sta WSYNC               ; picture line 80
        stx GRP0
        sta ENAM0
        lda #RED
        sta COLUP0              ; ends at cycle 11, clock 33
        bne ShotNext            ; always
ShotZone:
        sta WSYNC               ; picture lines 81-191
        stx GRP0
        sta ENAM0
ShotNext:
        iny
        ROW_VALUES
        cpy #192
        bne ShotZone

        ; Overscan: lines 232-261
        sta WSYNC               ; line 232
        lda #%00000010
        sta VBLANK
        STEP ShipX, 1, SHIP_X_RANGE
        ; The sketch's bounce: if ship_y == 0 and ship_dy < 0, ship_dy = 1;
        ; else if ship_y == 72 and ship_dy > 0, ship_dy = -1. Then ship_y
        ; = ship_y + ship_dy.
        lda ShipDY
        ldx ShipY
        cpx #SHIP_TOP
        bne .Bottom
        tay
        bpl .Move
        lda #1
        bne .StoreDY            ; always
.Bottom:
        cpx #SHIP_BOTTOM
        bne .Move
        tay
        bmi .Move
        beq .Move
        lda #$FF
.StoreDY:
        sta ShipDY
.Move:
        clc
        adc ShipY
        sta ShipY
        sta WSYNC               ; line 233
        STEP ShotX, 3, SHOT_X_RANGE
        ; if shot_y == 100, shot_y = 188; else shot_y = shot_y - 1.
        ldx ShotY
        dex
        cpx #SHOT_TOP - 1
        bne .StoreShot
        ldx #SHOT_START
.StoreShot:
        stx ShotY
        ldx #28
OverscanLoop:
        sta WSYNC               ; lines 234-261
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
