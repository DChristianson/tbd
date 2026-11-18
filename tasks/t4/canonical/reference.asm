; Reference solution: one player, fixed.
; Imported from racer's reference/objects-2a.asm (see tasks/t4/NOTICE.md).
; The comments below are racer's, and use racer's names for its files and
; documents.
;
; Expected behavior:
;   - Every frame after the first is 262 lines, with VSYNC on for lines 0-2
;     and the output blanked outside lines 40-231, as in frame-0a.
;   - The background is black. Player 0 shows the 8 rows of Ship, in white
;     ($0E), on picture lines 92-99 (frame lines 132-139), its leftmost
;     pixel at visible clock 76 (color clock 144), bit 7 leftmost.
;   - On every picture line, GRP0 is written before color clock 68.
;
; The player is placed in vertical blank, on line 4: RESP0 is strobed at
; color clock 141 (visible clock 73), which puts the player at visible
; clock 78 (TIA-OBJ-3), and HMOVE at the start of line 5 moves it 2 clocks
; left, to 76 (TIA-OBJ-4). That is the c = 141, m = 2 of the milestone 4
; subset's example.
;
; Every picture line writes GRP0 from the padded table, so the lines above
; and below the player read 0 and clear it.

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
