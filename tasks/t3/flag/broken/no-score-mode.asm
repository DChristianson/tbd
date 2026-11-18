; Broken: without score mode the playfield is drawn in COLUPF (black). Must fail the check.

        processor 6502
        include "vcs.h"
NO_ILLEGAL_OPCODES = 1          ; SLEEP pads odd cycles with `bit VSYNC`
        include "macro.h"

RED     = $46                   ; palette.red, NTSC
WHITE   = $0E                   ; palette.white
BLUE    = $84                   ; palette.blue

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
        lda #%00000000          ; no score mode
        sta CTRLPF
        lda #$F0                ; every playfield pixel lit
        sta PF0
        lda #$FF
        sta PF1
        sta PF2
        lda #BLUE
        sta COLUP1
        ldx #36
VBlankLoop:
        sta WSYNC               ; lines 4-39
        dex
        bne VBlankLoop
        jmp Picture

        ; Aligned, so that the loop's branch does not cross a page.
        ALIGN 256
Picture:
        ; Picture: lines 40-231. y counts the lines left.
        sta WSYNC               ; line 40 (first iteration, peeled)
        lda #0
        sta VBLANK              ; ends at cycle 5
        lda #RED
        sta COLUP0              ; 10: red from color clock 30
        lda #WHITE              ; 12
        SLEEP 21                ; 33
        sta COLUP0              ; 36: white from color clock 108
        ldy #191                ; 38
PictureLoop:
        sta WSYNC               ; lines 41-231
        lda #RED
        sta COLUP0              ; ends at cycle 5: red from clock 15
        lda #WHITE              ; 7
        SLEEP 26                ; 33
        sta COLUP0              ; 36: white from color clock 108 (visible 40)
        dey                     ; 38
        bne PictureLoop         ; 41

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

        org $FFFC
        .word Reset             ; reset vector
        .word Reset             ; IRQ/BRK vector (unused)
