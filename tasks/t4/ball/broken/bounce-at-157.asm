; Broken: the ball turns back at x = 157, a pixel short of the right edge. Must fail the check.

        processor 6502
        include "vcs.h"

BALL_HEIGHT = 2
BALL_RIGHT = 160 - 3                    ; ball_x: 0..158
BALL_BOTTOM = 192 - BALL_HEIGHT         ; ball_y: 0..190
BALL_WIDTH_2 = $10                      ; CTRLPF: a ball 2 clocks wide
RED     = $46
OVERSCAN_TIMER = 35                     ; INTIM reads 0 during line 260

        seg.u vars
        org $80
BallX   ds 1                    ; visible clock of the ball's left edge
BallY   ds 1                    ; picture line of the ball's top
BallDX  ds 1                    ; +1 or -1 ($FF)
BallDY  ds 1                    ; +1 or -1 ($FF)

        seg code
        org $F000

        ; Place object x at visible clock a, as in objects-2c.
        MAC POSITION
        sec
        sta WSYNC
.Divide:
        sbc #15                 ; 2
        bcs .Divide             ; 3, or 2 at the end
        eor #7                  ; 2
        asl                     ; 8
        asl
        asl
        asl
        sta.wx HMP0,x           ; 5
        sta RESP0,x             ; 4: ends at cycle 5k + 18
        sta WSYNC
        ENDM

        ; Picture line y's ENABL, in a: $FF on the ball's lines, 0
        ; elsewhere (bit 1 is the enable). 13 cycles.
        MAC BALL_VALUE
        tya                     ; 2: carry clear on the ball's lines:
        sec                     ; 2  y - ball_y, as a byte, is under 2
        sbc BallY               ; 3  only there
        cmp #BALL_HEIGHT        ; 2
        lda #0                  ; 2: $FF if carry is clear, 0 if set
        adc #$FF                ; 2
        ENDM

        ; Turn the velocity in {2} back if the position in {1} is at {3}
        ; and the velocity is {4} (1, or $FF for -1).
        MAC BOUNCE
        lda {1}
        cmp #{3}
        bne .Done
        lda {2}
        cmp #{4}
        bne .Done
        lda #0
        sec
        sbc {2}
        sta {2}
.Done:
        ENDM

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
        lda #79
        sta BallX               ; ball_x = 79
        lda #95
        sta BallY               ; ball_y = 95
        lda #1
        sta BallDX              ; moving right
        sta BallDY              ; and down

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
        lda #RED
        sta COLUPF
        lda #BALL_WIDTH_2
        sta CTRLPF
        lda BallX
        clc
        adc #1                  ; the ball is drawn a clock left of a player
        ldx #RESBL - RESP0      ; the ball
        POSITION                ; line 4
        sta HMOVE               ; line 5: ends at cycle 3
        ldx #34
VBlankLoop:
        sta WSYNC               ; lines 6-39
        dex
        bne VBlankLoop
        ldy #0
        BALL_VALUE              ; for picture line 0, in line 39

        ; Picture: lines 40-231. y is the picture line.
        sta WSYNC               ; line 40
        sta ENABL
        lda #0
        sta VBLANK
        beq NextRow             ; always
PictureLoop:
        sta WSYNC               ; lines 41-231
        sta ENABL               ; ends at cycle 3, clock 9
NextRow:
        iny                     ; 5
        BALL_VALUE              ; 18: for the next line
        cpy #192                ; 20
        bne PictureLoop         ; 23
        IF >PictureLoop != >.
        ECHO "PictureLoop's branch crosses a page"
        ERR
        ENDIF

        ; Overscan: lines 232-261
        sta WSYNC               ; line 232
        lda #%00000010
        sta VBLANK
        lda #OVERSCAN_TIMER
        sta TIM64T              ; ends at cycle 11

        ; The edges, then the move.
        BOUNCE BallX, BallDX, 0, $FF
        BOUNCE BallX, BallDX, BALL_RIGHT, 1
        BOUNCE BallY, BallDY, 0, $FF
        BOUNCE BallY, BallDY, BALL_BOTTOM, 1
        lda BallX
        clc
        adc BallDX
        sta BallX
        lda BallY
        clc
        adc BallDY
        sta BallY

WaitOverscan:
        lda INTIM
        bne WaitOverscan
        sta WSYNC               ; line 261
        jmp Frame

        org $FFFC
        .word Reset             ; reset vector
        .word Reset             ; IRQ/BRK vector (unused)
