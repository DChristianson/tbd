; Broken: the playfield is reflected, so the right half is drawn mirrored. Must fail the check.

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
        lda #1
        sta CTRLPF
        lda #0
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
; BEGIN TABLES (tbd's picture, encoded as racer's tools/playfield-images.py does)
LeftPF0:
        .byte $00,$00,$00,$00,$00,$00,$00,$00,$00,$00,$00,$00
LeftPF1:
        .byte $00,$00,$00,$0F,$01,$01,$01,$01,$00,$00,$00,$00
LeftPF2:
        .byte $00,$00,$00,$C7,$40,$C0,$40,$C0,$00,$00,$00,$00
RightPF0:
        .byte $00,$00,$00,$70,$80,$70,$80,$70,$00,$00,$00,$00
RightPF1:
        .byte $00,$00,$00,$1F,$10,$10,$10,$1F,$00,$00,$00,$00
RightPF2:
        .byte $00,$00,$00,$00,$01,$01,$01,$00,$00,$00,$00,$00
RowColor:
        .byte $00,$00,$00,$4E,$4C,$4A,$48,$46,$00,$00,$00,$00
; END TABLES

        org $FFFC
        .word Reset             ; reset vector
        .word Reset             ; IRQ/BRK vector (unused)
