; Reference solution: the player moving vertically and horizontally.
; Imported from racer's reference/objects-2c.asm (see tasks/t4/NOTICE.md).
; The comments below are racer's, and use racer's names for its files and
; documents.
;
; Expected behavior:
;   - Every frame after the first is 262 lines, with VSYNC on for lines 0-2
;     and the output blanked outside lines 40-231, as in frame-0a.
;   - The background is black. On the main loop's frame f (counting from 0),
;     player 0 shows the 8 rows of Ship, in white ($0E), from picture line
;     f mod 185 down, its leftmost pixel at visible clock f mod 153, bit 7
;     leftmost.
;   - On every picture line, GRP0 is written before color clock 68.
;
; ShipX and ShipY are the sketch's ship_x and ship_y, 0 at power-on and
; stepped by 1 at the end of each frame, wrapping at 153 and 185. The
; vertical motion is objects-2b's.
;
; The player is placed each frame by POSITION, the usual divide-by-15
; routine, on line 4, and HMOVE at the start of line 5.
;
; POSITION takes the position in A, and an object in X, as an offset from
; player 0's registers (0 for player 0, 2 for missile 0). After the WSYNC
; that starts its line, it subtracts 15 until A goes negative: k iterations,
; where x = 15(k - 1) + r and r is 0-14, taking 5k - 1 cycles. The strobe of
; RESP0,x then ends at cycle 5k + 18, color clock 15k + 54, visible clock
; 15k - 14, which places a player at 15k - 9 (TIA-OBJ-3). That leaves it
; 6 - r clocks right of x, a motion of -8 to 6.
;
; The motion is computed from what the loop leaves in A, r - 15, whose low
; nibble is r + 1. `eor #7` turns that into 6 - r, mod 16, and four shifts
; put it in the high nibble, where HMP0 reads it (TIA-OBJ-4).
;
; At the largest position, 152, k is 11, and the strobe ends at cycle 73.
; The WSYNC after it then ends exactly at the end of the line, which does not
; wait (TIA-WSYNC-1), so POSITION always takes exactly one line.

        processor 6502
        include "vcs.h"

SHIP_X_RANGE = 153              ; ship_x: 0..153
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
