; Reference solution: the word RACER across the picture, with a color
; for each row.
; Imported from racer's reference/playfield-3c.asm (see tasks/t3/NOTICE.md).
; The comments below are racer's, and use racer's names for its files and
; documents.
;
; Expected behavior (checked by check.toml):
;   - Every frame after the first is 262 lines, VSYNC is on for frame lines
;     0-2, and output is blanked outside frame lines 40-231.
;   - Every clock of the picture shows the image's color: 12 rows of 16
;     lines, 40 pixels of 4 clocks.
;
; Each line writes all six halves' registers. The left half's PF1 and PF2,
; and the row's COLUPF, are written in horizontal blank. The right half's
; are written in their windows (TIA-PF-5), each after the left half's last
; pixel from the register and in time for the right half's first: PF0 at
; visible clock 16 (window 13-76), PF1 at 43 (43-94), and PF2 at 76
; (76-124). Six loads, six stores, and COLUPF do not fit in horizontal
; blank's 22 cycles, so the next line's left PF0 is written late in this
; line, after the right half's last PF0 pixel (pixel 23, at 92): at 112, or
; 121 on the line that starts a new row.
;
; Rows are stored once per row, 12 entries a table: X is the row, and Y
; counts its 16 lines. The busiest line, which starts a row, takes 71 of 76
; cycles with its WSYNC; 5 are left. Each line takes the same cycles in every frame.

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

        ; Vertical blank: lines 3-39. Not reflected, no score mode, a black
        ; background, and the picture's first line set up.
        sta WSYNC               ; line 3
        lda #0
        sta VSYNC
        sta CTRLPF
        sta COLUBK
        lda LeftPF0
        sta PF0
        lda LeftPF1
        sta PF1
        lda LeftPF2
        sta PF2
        lda RowColor
        sta COLUPF
        ldx #36
VBlankLoop:
        sta WSYNC               ; lines 4-39
        dex
        bne VBlankLoop

        ; Picture: lines 40-231. Line 40's left half and color were set in
        ; vertical blank, so it turns VBLANK off instead, and joins the
        ; kernel at the same cycle as every other line.
        ldx #0                  ; row
        ldy #16                 ; lines left in the row
        sta WSYNC               ; line 40
        lda #0                  ;  2
        sta VBLANK              ;  5
        SLEEP 13                ; 18
        jmp Right               ; 21
Line:
        sta WSYNC               ; lines 41-231
        lda LeftPF1,x           ;  4
        sta PF1                 ;  7
        lda LeftPF2,x           ; 11
        sta PF2                 ; 14
        lda RowColor,x          ; 18
        sta COLUPF              ; 21: clock 63, in horizontal blank
Right:
        lda RightPF0,x          ; 25
        sta PF0                 ; 28: visible 16
        lda RightPF1,x          ; 32
        nop                     ; 34
        sta PF1                 ; 37: visible 43
        lda RightPF2,x          ; 41
        nop                     ; 43
        nop                     ; 45
        sta PF2                 ; 48: visible 76
        dey                     ; 50
        bne SameRow             ; 53 if taken
        inx                     ; 54
        ldy #16                 ; 56
SameRow:
        lda LeftPF0,x           ; 57, or 60 on a new row
        sta PF0                 ; 60: visible 112, or 63: 121
        cpx #12                 ; 62, or 65
        bne Line                ; 65, or 68

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
LeftPF0:
        .byte $00,$00,$00,$80,$80,$80,$80,$80,$00,$00,$00,$00,$00
LeftPF1:
        .byte $00,$00,$00,$E1,$12,$E3,$22,$12,$00,$00,$00,$00
LeftPF2:
        .byte $00,$00,$00,$C3,$24,$27,$24,$C4,$00,$00,$00,$00
RightPF0:
        .byte $00,$00,$00,$30,$00,$00,$00,$30,$00,$00,$00,$00
RightPF1:
        .byte $00,$00,$00,$F9,$81,$F1,$81,$F9,$00,$00,$00,$00
RightPF2:
        .byte $00,$00,$00,$07,$08,$07,$04,$08,$00,$00,$00,$00
RowColor:
        .byte $00,$00,$00,$4E,$4C,$4A,$48,$46,$00,$00,$00,$00
; END TABLES

        org $FFFC
        .word Reset             ; reset vector
        .word Reset             ; IRQ/BRK vector (unused)
