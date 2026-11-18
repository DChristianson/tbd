; Reference solution: four stripes, each 9 clocks wide, in the middle of
; every picture line.
; Imported from racer's reference/playfield-3e-narrow.asm (see tasks/t3/NOTICE.md).
; The comments below are racer's, and use racer's names for its files and
; documents.
;
; Expected behavior:
;   - Every frame after the first is 262 lines, with VSYNC on for lines 0-2
;     and the output blanked outside lines 40-231, as in frame-0a.
;   - On every picture line, visible clocks 40-48 are red ($46), 49-57
;     white ($0E), 58-66 blue ($84), and 67-75 green ($C4). The sketch asks
;     nothing of the other clocks; here they are red before 40 and green
;     from 76.
;
; The background draws all four, rewritten at each boundary. Red is written
; in horizontal blank. The other three changes are 3 cycles apart, one store
; each, so their colors must all be in registers before the first: white in
; A, loaded after the red store; blue in X and green in Y, loaded once a
; frame, before the picture. That leaves no register for the line counter,
; which is in RAM, counted down with `dec` and tested with `bne`.
;
; The stores end at cycles 39, 42, and 45 after WSYNC, so the colors take
; effect at color clocks 117, 126, and 135: visible clocks 49, 58, and 67
; (TIA-COLOR-1).
;
; Cycles from WSYNC, each line's own WSYNC included: 56 on a picture line,
; so 20 left; the same on the first, which also turns VBLANK off.

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
        SLEEP 29                ; 36
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
