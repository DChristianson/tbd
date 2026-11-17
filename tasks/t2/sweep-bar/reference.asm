; Reference solution: picture line n shows color (2n) mod 256 on visible
; clocks 0-81, and red on 82-159.
; Imported from racer's reference/background-1e.asm (see tasks/t2/NOTICE.md).
;
; COLUBK is written twice a line: the sweep's color in horizontal blank, then
; red by a store ending at cycle 50, which takes effect at color clock 150
; (visible clock 82).
;
;   lines   0-2    VSYNC
;   lines   3-39   vertical blank
;   lines  40-231  picture
;   lines 232-261  overscan, blanked

        processor 6502
        include "vcs.h"
NO_ILLEGAL_OPCODES = 1          ; SLEEP pads odd cycles with `bit VSYNC`
        include "macro.h"

RED     = $46                   ; palette.red, NTSC

        seg code
        org $F000

Reset:
        sei                     ; CPU setup
        cld
        ldx #$FF
        txs
        ; Clear the TIA's write registers, except the strobes.
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
        jmp Picture

        ; Aligned, so that the loop's branch does not cross a page.
        ALIGN 256
Picture:
        ; Picture: lines 40-231. x is the sweep's color, from 0 each frame.
        ldx #0
        sta WSYNC               ; line 40 (first iteration, peeled)
        lda #0
        sta VBLANK              ; ends at cycle 5
        stx COLUBK              ; 8: the sweep's color, clock 24
        inx                     ; 10
        inx                     ; 12
        lda #RED                ; 14
        SLEEP 33                ; 47
        sta COLUBK              ; 50: red from color clock 150
        ldy #191
PictureLoop:
        sta WSYNC               ; lines 41-231
        stx COLUBK              ; ends at cycle 3: clock 9
        inx                     ; 5
        inx                     ; 7
        SLEEP 40                ; 47
        sta COLUBK              ; 50: red from color clock 150
        dey                     ; 52
        bne PictureLoop         ; 55

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
