; Reference solution: a gate whose right half is its left half reflected,
; in two colors.
; Imported from racer's reference/playfield-3a.asm (see tasks/t3/NOTICE.md).
; The comments below are racer's, and use racer's names for its files and
; documents.
;
; Expected behavior (checked by check.toml):
;   - Every frame after the first is 262 lines, VSYNC is on for frame lines
;     0-2, and output is blanked outside frame lines 40-231.
;   - Every clock of the picture shows the image's color: 12 rows of 16
;     lines, 40 pixels of 4 clocks.
;
; Each row's half is three bytes, PF0, PF1, and PF2, and CTRLPF's reflect bit draws the right half as the left half mirrored. The
; colors never change, so they are written once per frame.
;
; Rows are stored once per row, 12 entries a table: X is the row, and Y
; counts its 16 lines. Each line writes the row's three registers in
; horizontal blank, by cycle 21 (clock 63); the busiest line, which starts a
; row, takes 37 of 76 cycles with its WSYNC.

        processor 6502
        include "vcs.h"
NO_ILLEGAL_OPCODES = 1          ; SLEEP pads odd cycles with `bit VSYNC`
        include "macro.h"

        seg code
        org $F000

Reset:
        sei                     ; CPU setup
        cld
        ldx #$FF
        txs
        ; Clear the TIA's write registers, which power up with unknown
        ; values (BOARD-POWERON-1), except the strobes. RAM is not cleared.
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

        ; Vertical blank: lines 3-39. CTRLPF = 1, the colors, and the
        ; picture's first row.
        sta WSYNC               ; line 3
        lda #0
        sta VSYNC
        lda #1
        sta CTRLPF
        lda #$3C               ; palette.orange
        sta COLUPF
        lda #$84               ; palette.blue
        sta COLUBK
        lda RowPF0
        sta PF0
        lda RowPF1
        sta PF1
        lda RowPF2
        sta PF2
        ldx #36
VBlankLoop:
        sta WSYNC               ; lines 4-39
        dex
        bne VBlankLoop

        ; Picture: lines 40-231. Line 40's row was set in vertical blank, so
        ; it turns VBLANK off instead.
        ldx #0                  ; row
        ldy #16                 ; lines left in the row
        sta WSYNC               ; line 40
        lda #0
        sta VBLANK
        jmp Next
Line:
        sta WSYNC               ; lines 41-231
        lda RowPF0,x            ;  4
        sta PF0                 ;  7
        lda RowPF1,x            ; 11
        sta PF1                 ; 14
        lda RowPF2,x            ; 18
        sta PF2                 ; 21: clock 63, in horizontal blank
Next:
        dey
        bne Line
        inx
        ldy #16
        cpx #12
        bne Line

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

        ; The tables share one page, so no indexed read crosses one.
        ALIGN 256
; BEGIN TABLES (written by tools/playfield-images.py)
RowPF0:
        .byte $F0,$10,$D0,$D0,$D0,$D0,$D0,$D0,$D0,$10,$10,$F0
RowPF1:
        .byte $FF,$00,$C0,$C0,$C0,$C0,$C0,$C0,$C0,$00,$00,$FF
RowPF2:
        .byte $FF,$00,$FF,$01,$FD,$05,$05,$05,$05,$00,$00,$FF
; END TABLES

        org $FFFC
        .word Reset             ; reset vector
        .word Reset             ; IRQ/BRK vector (unused)
