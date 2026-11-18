; Broken: the stripes after red start 3 clocks late on every line but the first. Must fail the check.

        processor 6502
        include "vcs.h"
NO_ILLEGAL_OPCODES = 1          ; SLEEP pads odd cycles with `bit VSYNC`
        include "macro.h"

RED     = $46                   ; palette.red, NTSC
WHITE   = $0E                   ; palette.white
BLUE    = $84                   ; palette.blue
GREEN   = $C4                   ; palette.green

        seg.u vars
        org $80
Lines   ds 1                    ; picture lines left, counting down

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
        ldx #36
VBlankLoop:
        sta WSYNC               ; lines 4-39
        dex
        bne VBlankLoop
        lda #192
        sta Lines
        ldx #BLUE
        ldy #GREEN
        jmp Picture

        ; Aligned, so that the loop's branch does not cross a page.
        ALIGN 256
Picture:
        sta WSYNC               ; picture line 0
        lda #0
        sta VBLANK              ; ends at cycle 5
        lda #RED
        sta COLUBK              ; 10
        lda #WHITE              ; 12
        SLEEP 21                ; 33
        jmp Stripes             ; 36
Line:
        sta WSYNC               ; picture lines 1-191
        lda #RED
        sta COLUBK              ; ends at cycle 5: clock 15
        lda #WHITE              ; 7
        SLEEP 30                ; 37
Stripes:
        sta COLUBK              ; ends at cycle 39: visible clock 49, white
        stx COLUBK              ; 42: 58, blue
        sty COLUBK              ; 45: 67, green
        dec Lines               ; 50
        bne Line                ; 53

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
