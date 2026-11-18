; Reference solution: a checkerboard of 8 columns and 12 rows, each cell 20
; pixels wide and 16 lines tall. Row r's colored cells are $16 + $10 * r,
; and the others black. Written for tbd (see tasks/t3/NOTICE.md).
;
;   lines   0-2    VSYNC
;   lines   3-39   vertical blank
;   lines  40-231  picture
;   lines 232-261  overscan, blanked
;
; The playfield draws the colored cells and the background the black ones.
; A cell is 5 playfield pixels, so a half line of 20 pixels holds 4 cells,
; and the playfield's default mode, which repeats the left half in the
; right, draws all 8. Even rows light pixels 0-4 and 10-14 of each half,
; odd rows 5-9 and 15-19:
;
;   pixel   0-3 (PF0, bits 4-7)   4-11 (PF1, bit 7 first)   12-19 (PF2, bit 0 first)
;   even    1111                  1000 0011                 1110 0000
;   odd     0000                  0111 1100                 0001 1111
;
; Each picture line turns VBLANK off, then writes the row's PF0, COLUPF,
; PF1 and PF2, in that order, from tables indexed by the row in X. A store
; ending at cycle n after WSYNC takes effect at color clock 3n, visible
; clock 3n - 68: PF0 and COLUPF by cycle 19, before visible clock 0; PF1 at
; cycle 26 (visible clock 10) and PF2 at 33 (visible clock 31), each before
; its first pixel (visible clocks 16 and 48). Y counts the row's 16 lines.

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
        .byte $16, $26, $36, $46, $56, $66, $76, $86, $96, $A6, $B6, $C6

        org $FFFC
        .word Reset             ; reset vector
        .word Reset             ; IRQ/BRK vector (unused)
