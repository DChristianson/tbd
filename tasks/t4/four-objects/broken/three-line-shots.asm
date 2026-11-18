; Broken: each shot has a third line, above its top line. Must fail the check.

        processor 6502
NO_ILLEGAL_OPCODES = 1
        include "vcs.h"
        include "macro.h"

P_H     = 8                     ; player height, lines
M_H     = 2                     ; missile height, lines
LINES   = 192

        seg.u vars
        org $80
P0Y     ds 1                    ; top line of each object
P1Y     ds 1
M0Y     ds 1
M1Y     ds 1
M0X     ds 1
M1X     ds 1
P0Ptr   ds 2                    ; pointers into the padded tables
P1Ptr   ds 2
M0Ptr   ds 2
M1Ptr   ds 2
TM0     ds 1                    ; next line's ENAM0 and ENAM1
TM1     ds 1
TP0     ds 1                    ; line 0's GRP0

        ; ptr = region + height - 1 + top line: then, with Y = 192 - line,
        ; (ptr),Y is the image's row (line - top), or a padding zero.
        mac SETPTR              ; SETPTR ptr, region, height, ytop
        clc
        lda #<({2} + {3} - 1)
        adc {4}
        sta {1}
        lda #>({2} + {3} - 1)
        adc #0
        sta {1}+1
        endm

        ; v = v + d, wrapping at limit (d = 1 or 2) to 0.
        mac UP                  ; UP v, d, limit
        lda {1}
        clc
        adc #{2}
        cmp #{3}
        bcc .ok
        lda #0
.ok     sta {1}
        endm

        ; v = v - 1, wrapping below 0 to limit - 1.
        mac DOWN                ; DOWN v, limit
        ldx {1}
        dex
        cpx #$FF
        bne .ok
        ldx #{2} - 1
.ok     stx {1}
        endm

        seg code
        org $F000

Reset:
        CLEAN_START
        lda #$0E                ; palette.white
        sta COLUP0
        lda #$1E                ; palette.yellow
        sta COLUP1
        lda #$10                ; missiles 2 clocks wide, one player copy
        sta NUSIZ0
        sta NUSIZ1
        lda #0
        sta P0Y
        lda #100
        sta P1Y
        lda #50
        sta M0Y
        lda #150
        sta M1Y
        lda #30
        sta M0X
        lda #120
        sta M1X
        lda #2
        sta VBLANK

Frame:
        ; VSYNC, lines 0-2.
        sta WSYNC
        lda #2
        sta VSYNC
        sta WSYNC
        sta WSYNC
        sta WSYNC
        lda #0
        sta VSYNC

        ; Vertical blank, lines 3-39.
        lda #43
        sta TIM64T

        lda #20
        ldx #0
        jsr PosObject
        lda #100
        ldx #1
        jsr PosObject
        lda M0X
        ldx #2
        jsr PosObject
        lda M1X
        ldx #3
        jsr PosObject
        sta WSYNC
        sta HMOVE

        SETPTR P0Ptr, Gfx0, P_H, P0Y
        SETPTR P1Ptr, Gfx1, P_H, P1Y
        SETPTR M0Ptr, Mis0, M_H, M0Y
        SETPTR M1Ptr, Mis1, M_H, M1Y

.vblank lda INTIM
        bne .vblank
        sta WSYNC               ; the timer ends on line 38

        ; Line 39, still blank: line 0's values, read with Y = 192. Line
        ; 40 must also turn VBLANK off in its horizontal blank, so its
        ; writes come from four registers, and it joins the kernel after
        ; the kernel's own writes.
        sta HMCLR
        ldy #LINES
        lda (P0Ptr),y
        sta TP0
        lda (M1Ptr),y
        sta TM1
        lda (P1Ptr),y
        tax
        lda (M0Ptr),y
        tay
        lda #0
        sta WSYNC               ; 3
        sta VBLANK              ; 3   3
        stx GRP1                ; 3   6
        sty ENAM0               ; 3   9
        lda TP0                 ; 3  12
        sta GRP0                ; 3  15
        lda TM1                 ; 3  18
        sta ENAM1               ; 3  21
        ldy #LINES              ; 2  23
        jmp Next                ; 3  26

        ; The picture, lines 40-231. Cycles from the end of WSYNC.
Kernel:
        sta WSYNC               ; 3
        sta GRP0                ; 3   3
        stx GRP1                ; 3   6
        lda TM0                 ; 3   9
        sta ENAM0               ; 3  12
        lda TM1                 ; 3  15
        sta ENAM1               ; 3  18
Next:   dey                     ; 2  20   Y = 192 - next line
        lda (M0Ptr),y           ; 5  25 (+1 across a page)
        sta TM0                 ; 3  28
        lda (M1Ptr),y           ; 5  33 (+1)
        sta TM1                 ; 3  36
        lda (P1Ptr),y           ; 5  41 (+1)
        tax                     ; 2  43
        lda (P0Ptr),y           ; 5  48 (+1)
        cpy #0                  ; 2  50
        bne Kernel              ; 3  53   57 at most, then WSYNC

        ; Overscan, lines 232-261.
        sta WSYNC
        lda #2
        sta VBLANK
        lda #0
        sta GRP0
        sta GRP1
        sta ENAM0
        sta ENAM1
        lda #35
        sta TIM64T

        UP P0Y, 1, 185
        DOWN P1Y, 185
        UP M0Y, 2, 190
        UP M1Y, 1, 190
        UP M0X, 1, 158
        DOWN M1X, 158

.overscan
        lda INTIM
        bne .overscan
        jmp Frame

        ; Place object X (0 player 0, 1 player 1, 2 missile 0, 3 missile 1)
        ; at x = A, on a line of its own: coarse by RESxx, fine by HMxx.
PosObject subroutine
        sta WSYNC
        sec
.div    sbc #15
        bcs .div
        eor #7
        asl
        asl
        asl
        asl
        sta HMP0,x
        sta RESP0,x
        rts

        ; The padded tables. Adjacent tables share their 192 zeros.
        align 256
Gfx0:   ds 192, 0
        .byte $42, $24, $7E, $FF, $FF, $7E, $3C, $18   ; ship, upside down
Gfx1:   ds 192, 0
        .byte $42, $24, $7E, $FF, $FF, $7E, $3C, $18   ; foe, upside down
Mis0:   ds 192, 0
        .byte $02, $02, $02
Mis1:   ds 192, 0
        .byte $02, $02, $02
        ds 192, 0

        org $FFFC
        .word Reset
        .word Reset
