; Broken: reflected without score mode, so both words are drawn in COLUPF (black). Must fail the check.

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

        ; Vertical blank: lines 3-39. Reflected, in score mode, the colors,
        ; and the picture's first line's left half.
        sta WSYNC               ; line 3
        lda #0
        sta VSYNC
        sta COLUBK              ; palette.black
        lda #1
        sta CTRLPF
        lda #$46                ; palette.red
        sta COLUP0
        lda #$84                ; palette.blue
        sta COLUP1
        lda LeftPF0
        sta PF0
        lda LeftPF1
        sta PF1
        lda LeftPF2
        sta PF2
        ldx #36
VBlankLoop:
        sta WSYNC               ; lines 4-39
        dex
        bne VBlankLoop

        ; Picture: lines 40-231. Line 40's left half was set in vertical
        ; blank, so it turns VBLANK off instead, and joins the kernel at the
        ; same cycle as every other line.
        ldx #0                  ; row
        ldy #16                 ; lines left in the row
        sta WSYNC               ; line 40
        lda #0                  ;  2
        sta VBLANK              ;  5
        SLEEP 13                ; 18
        jmp Right               ; 21
Line:
        sta WSYNC               ; lines 41-231
        lda LeftPF0,x           ;  4
        sta PF0                 ;  7
        lda LeftPF1,x           ; 11
        sta PF1                 ; 14
        lda LeftPF2,x           ; 18
        sta PF2                 ; 21: clock 63, in horizontal blank
Right:
        lda RightPF1,x          ; 25
        SLEEP 9                 ; 34
        sta PF1                 ; 37: visible 43
        lda RightPF2,x          ; 41
        nop                     ; 43
        nop                     ; 45
        sta PF2                 ; 48: visible 76
        lda RightPF0,x          ; 52
        sta PF0                 ; 55: visible 97
        dey                     ; 57
        bne SameRow             ; 60 if taken
        inx                     ; 61
        ldy #16                 ; 63
SameRow:
        cpx #12                 ; 62, or 65 on a new row
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
        .byte $00,$00,$00,$E0,$80,$80,$80,$80,$00,$00,$00,$00
LeftPF1:
        .byte $00,$00,$00,$DE,$11,$1E,$11,$1E,$00,$00,$00,$00
LeftPF2:
        .byte $00,$00,$00,$1E,$22,$22,$22,$1E,$00,$00,$00,$00
RightPF0:
        .byte $00,$00,$00,$C0,$20,$20,$20,$C0,$00,$00,$00,$00
RightPF1:
        .byte $00,$00,$00,$CF,$51,$4F,$51,$CF,$00,$00,$00,$00
RightPF2:
        .byte $00,$00,$00,$3E,$08,$08,$08,$08,$00,$00,$00,$00
; END TABLES

        org $FFFC
        .word Reset             ; reset vector
        .word Reset             ; IRQ/BRK vector (unused)
