; Reference solution: a flag of three vertical stripes, red, white and
; blue.
; Imported from racer's reference/playfield-3e.asm (see tasks/t3/NOTICE.md).
; The comments below are racer's, and use racer's names for its files and
; documents.
;
; Expected behavior:
;   - Every frame after the first is 262 lines, with VSYNC on for lines 0-2
;     and the output blanked outside lines 40-231, as in frame-0a.
;   - On every picture line, visible clocks 0-39 are red ($46), 40-79 white
;     ($0E), and 80-159 blue ($84).
;   - On every picture line, COLUP0 is written twice: red before color clock
;     68, and white at color clock 108 (visible clock 40), exactly.
;
; The playfield draws the whole line, every pixel lit, in score mode
; (TIA-PF-2): its left half in COLUP0, its right half in COLUP1. The change
; at 80 is score mode's own, at the middle of the line, with no write
; (TIA-PF-6), which no store could make: 80 is not a clock a store reaches.
; The change at 40 is a write: COLUP0 rewritten from red to white by a store
; ending at cycle 36 after WSYNC, color clock 108 (TIA-COLOR-1, TIA-PF-4).
; COLUP1 is blue for the whole frame, and the playfield registers are
; written once a frame.
;
; Drawn instead with the background red until a write at 40 makes it white,
; under a playfield lit in blue on its right half only, a line would need
; the playfield's registers rewritten for the right half (TIA-PF-5): five
; writes a line, not two.
;
; Cycles from WSYNC, each line's own WSYNC included: 44 on a picture line,
; so 32 left; 41 on the first, which also turns VBLANK off, so 35 left.

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
        lda #%00000010          ; score mode, not reflected
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
