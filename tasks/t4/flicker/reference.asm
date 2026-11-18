; Reference solution: three objects in one band, crossing each other,
; drawn by two players with flicker.
; Imported from racer's reference/objects-2h.asm (see tasks/t4/NOTICE.md).
; The comments below are racer's, and use racer's names for its files and
; documents.
;
; Expected behavior:
;   - Every frame after the first is 262 lines, with VSYNC on for lines 0-2
;     and the output blanked outside lines 40-231, as in frame-0a.
;   - The background is black. On the main loop's frame f (counting from 0),
;     the ship (white, $0E) is at visible clock f mod 153 and picture line
;     88, the rock (yellow, $1E) at (120 - f) mod 153 and line 91, and the
;     UFO (green, $C4) at (60 + 2f) mod 153 and line 94, with 2G's images.
;     All three are on picture lines 94-95 on every frame, so no two players
;     can draw them all on one frame, however they are placed.
;   - Frame f hides object f mod 3, in the program's order (ship, rock,
;     UFO), and draws the other two: each is shown on 2 of every 3 frames,
;     and never hidden on two in a row, which meets the sketch's
;     `shown: 1, frames: 2`. Where the two drawn overlap, the later in the
;     program is in front, so it is player 0 (TIA-OBJ-6), and the earlier
;     player 1.
;   - On every picture line, GRP0 and GRP1 are written before color
;     clock 68.
;
;   f mod 3   hidden   player 0   player 1
;   0         ship     UFO        rock
;   1         rock     UFO        ship
;   2         UFO      rock       ship
;
; The objects' positions are in RAM, indexed by object; Rot is f mod 3,
; stepped with them. In vertical blank, each player is given its object's
; color, a pointer to its image less its y, and its position, by
; objects-2c's POSITION, on lines 4 and 6, with one HMOVE on line 7. The
; kernel is objects-2d's with a second pointer: each line's two rows are
; read on the line before, and written at the start of the line, GRP0 at
; color clock 9 and GRP1 at 18. A line takes 28 cycles, so 48 are left.

        processor 6502
        include "vcs.h"

X_RANGE = 153                   ; every x: 0..153
WHITE   = $0E                   ; palette.white, NTSC
YELLOW  = $1E                   ; palette.yellow
GREEN   = $C4                   ; palette.green

        seg.u vars
        org $80
ObjX    ds 3                    ; visible clock of each object's leftmost pixel
ObjY    ds 3                    ; picture line of each object's first row,
                                ; which does not change
Rot     ds 1                    ; f mod 3: the object hidden this frame
P0Ptr   ds 2                    ; player 0's image - its y
P1Ptr   ds 2                    ; player 1's

SHIP    = 0                     ; objects, in the program's order
ROCK    = 1
UFO     = 2

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

        ; Give player {1} the object in this rotation's entry of table
        ; {2}: its color, its pointer {3}, and, in a, its x.
        MAC ASSIGN
        ldx Rot
        lda {2},x
        tax                     ; the object
        lda Colors,x
        sta COLUP0 + {1}
        lda ImagesLo,x
        sec
        sbc ObjY,x
        sta {3}
        lda ImagesHi,x
        sbc #0
        sta {3}+1
        lda ObjX,x
        ldx #{1}
        ENDM

        ; v = (v + step) % range, for v already in 0..range and step small
        ; enough that v + step < 256.
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

        ; v = (v + range - 1) % range: one less, wrapping to range - 1.
        MAC DECREMENT
        lda {1}
        bne .Down
        lda #{2}                ; 0 becomes range, less 1 below
.Down:
        sec
        sbc #1
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
        ldx #6
Init:
        lda Start,x             ; the sketch's initial positions, and Rot = 0
        sta ObjX,x
        dex
        bpl Init

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
        ASSIGN 0, Player0, P0Ptr
        POSITION                ; line 4
        ASSIGN 1, Player1, P1Ptr ; line 5
        POSITION                ; line 6
        sta HMOVE               ; line 7: ends at cycle 3
        ldx #32
VBlankLoop:
        sta WSYNC               ; lines 8-39
        dex
        bne VBlankLoop
        ldy #0                  ; picture line 0's rows, in line 39
        lda (P0Ptr),y
        tax
        lda (P1Ptr),y

        ; Picture: lines 40-231. y is the picture line.
        sta WSYNC               ; line 40
        stx GRP0
        sta GRP1
        lda #0
        sta VBLANK
        beq NextRow             ; always
PictureLoop:
        sta WSYNC               ; lines 41-231
        stx GRP0                ; ends at cycle 3, clock 9
        sta GRP1                ; ends at cycle 6, clock 18
NextRow:
        iny
        lda (P0Ptr),y           ; the next line's rows, 0 outside each image
        tax
        lda (P1Ptr),y
        cpy #192
        bne PictureLoop

        ; Overscan: lines 232-261
        sta WSYNC               ; line 232
        lda #%00000010
        sta VBLANK
        STEP ObjX + SHIP, 1, X_RANGE
        DECREMENT ObjX + ROCK, X_RANGE
        sta WSYNC               ; line 233
        STEP ObjX + UFO, 2, X_RANGE
        STEP Rot, 1, 3
        ldx #28
OverscanLoop:
        sta WSYNC               ; lines 234-261
        dex
        bne OverscanLoop

        jmp Frame

Start:
        .byte 0, 120, 60        ; ObjX: ship_x, rock_x, ufo_x
        .byte 88, 91, 94        ; ObjY
        .byte 0                 ; Rot

        ; Each rotation's objects, by f mod 3.
Player0:
        .byte UFO, UFO, ROCK
Player1:
        .byte ROCK, SHIP, SHIP

Colors:
        .byte WHITE, YELLOW, GREEN
ImagesLo:
        .byte <Ship, <Rock, <Ufo
ImagesHi:
        .byte >Ship, >Rock, >Ufo

        ; The images, with 184 zero rows before each and 185 after, shared
        ; between neighbors: enough for a read on any picture line, wherever
        ; in the picture the image is, and for the kernel's read past the
        ; last.
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
Rock:
        .byte %00111100         ; ..####..
        .byte %01111110         ; .######.
        .byte %11011111         ; ##.#####
        .byte %11111111         ; ########
        .byte %11111011         ; #####.##
        .byte %11111111         ; ########
        .byte %01111110         ; .######.
        .byte %00111100         ; ..####..
        ds 185, 0
Ufo:
        .byte %00011000         ; ...##...
        .byte %00111100         ; ..####..
        .byte %11111111         ; ########
        .byte %11011011         ; ##.##.##
        .byte %11111111         ; ########
        .byte %01111110         ; .######.
        .byte %00100100         ; ..#..#..
        .byte %01000010         ; .#....#.
        ds 185, 0

        org $FFFC
        .word Reset             ; reset vector
        .word Reset             ; IRQ/BRK vector (unused)
